"""Owner-scoped tuning jobs survive browser panel closure and disconnection."""
from __future__ import annotations

import asyncio
import copy
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .client import redact


_MAX_JOBS = 128
_MAX_EVENTS = 256
_RETENTION_SECONDS = 3600
_CREDENTIAL = re.compile(r"^(?:api[_-]?key|authorization|access[_-]?token|auth[_-]?token|password|secret|headers)$", re.I)
_TERMINAL = {"completed", "failed", "cancelled"}


def public_tuning_value(runtime, value):
    """Neither upstream errors nor model-generated records may publish credentials."""
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items() if not _CREDENTIAL.fullmatch(str(key))}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return item

    value = clean(value)
    clients = [*getattr(runtime, "_clients", {}).values(), *getattr(runtime, "_retired_clients", [])]
    for client in clients:
        secret = getattr(client, "api_key", "")
        if secret:
            value = redact(value, secret)
    return value


def _service(runtime):
    from .video_tuning import get_video_tuning
    return get_video_tuning(runtime)


@dataclass
class TuningJob:
    run_id: str
    owner: str
    state: str = "queued"
    result: dict | None = None
    error: str | None = None
    sequence: int = 0
    updated_at: float = field(default_factory=time.monotonic)
    events: deque = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS))
    task: asyncio.Task | None = None


class TuningJobs:
    def __init__(self, runtime):
        self.runtime = runtime
        self.jobs: dict[str, TuningJob] = {}
        self.closed = False

    def _prune(self):
        now = time.monotonic()
        for run_id, job in tuple(self.jobs.items()):
            if job.state in _TERMINAL and now - job.updated_at > _RETENTION_SECONDS:
                self.jobs.pop(run_id, None)
        if len(self.jobs) >= _MAX_JOBS:
            terminal = sorted((job for job in self.jobs.values() if job.state in _TERMINAL), key=lambda job: job.updated_at)
            for job in terminal[:len(self.jobs) - _MAX_JOBS + 1]:
                self.jobs.pop(job.run_id, None)
        if len(self.jobs) >= _MAX_JOBS:
            raise ValueError("Too many active tuning jobs; wait for a result or cancel a task.")

    def create(self, owner, value, locale="zh-CN"):
        if self.closed:
            raise ValueError("Tuning service is shutting down.")
        if not isinstance(value, dict):
            raise ValueError("Video tuning settings must be a JSON object.")
        self._prune()
        job = TuningJob(uuid.uuid4().hex, owner)
        self.jobs[job.run_id] = job
        job.task = asyncio.create_task(self._run(job, copy.deepcopy(value), locale))
        return self.snapshot(owner, job.run_id)

    def _get(self, owner, run_id):
        job = self.jobs.get(run_id)
        if job is None or job.owner != owner:
            raise KeyError("Tuning job was not found.")
        return job

    def snapshot(self, owner, run_id, after=0):
        job = self._get(owner, run_id)
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("Event cursor must be a nonnegative integer.")
        result = {"run_id": job.run_id, "state": job.state, "cursor": job.sequence,
                  "events": [dict(event) for event in job.events if event["sequence"] > after]}
        if job.result is not None:
            result["result"] = copy.deepcopy(job.result)
        if job.error is not None:
            result["error"] = job.error
        return result

    async def _run(self, job, value, locale):
        async def emit(event):
            if not isinstance(event, dict) or job.state != "running":
                return
            job.sequence += 1
            clean = public_tuning_value(self.runtime, event)
            # Poll responses stay bounded even when a model streams long deltas.
            if isinstance(clean.get("text"), str):
                clean["text"] = clean["text"][:32768]
            job.events.append({**clean, "sequence": job.sequence})
            job.updated_at = time.monotonic()

        job.state = "running"
        try:
            result = await _service(self.runtime).run(value, locale=locale, emit=emit)
            if not isinstance(result, dict):
                raise ValueError("Tuning did not return a structured result.")
            job.result = public_tuning_value(self.runtime, result)
            job.state = "completed"
        except asyncio.CancelledError:
            job.state = "cancelled"
            raise
        except Exception as error:
            job.error = str(public_tuning_value(self.runtime, str(error)))[:2000]
            job.state = "failed"
        finally:
            job.updated_at = time.monotonic()

    async def cancel(self, owner, run_id):
        job = self._get(owner, run_id)
        if job.state not in _TERMINAL and job.task:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
            # Cancellation can arrive before the coroutine starts.
            if job.state not in _TERMINAL:
                job.state = "cancelled"
                job.updated_at = time.monotonic()
        return self.snapshot(owner, run_id)

    async def close(self):
        self.closed = True
        pending = [job for job in self.jobs.values() if job.task and not job.task.done()]
        for job in pending:
            job.task.cancel()
        if pending:
            await asyncio.gather(*(job.task for job in pending), return_exceptions=True)
            for job in pending:
                if job.state not in _TERMINAL:
                    job.state = "cancelled"


def get_tuning_jobs(runtime):
    manager = getattr(runtime, "_video_tuning_jobs", None)
    if manager is None:
        manager = TuningJobs(runtime)
        runtime._video_tuning_jobs = manager
    return manager
