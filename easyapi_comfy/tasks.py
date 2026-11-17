"""Durable task handles: waiting always resumes a query, never submits generation."""
from __future__ import annotations

import asyncio
import copy
import contextlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .client import APIResponse, EasyAPIError, redact
from .profiles import atomic_json


DONE = {"success", "succeed", "succeeded", "complete", "completed", "done", "finish", "finished"}
FAILED = {"failed", "failure", "error", "cancelled", "canceled", "rejected"}


def field_path(value, path, default=None):
    current = value
    for name, index in re.findall(r"(?:^|\.)([^.\[]+)|\[(\d+)\]", str(path)):
        try:
            current = current[int(index)] if index else current[name]
        except (KeyError, TypeError, IndexError):
            return default
    return current


def response_record(response):
    return {"data": redact(response.data), "status": response.status,
            "content_type": response.content_type}


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.suffix != ".json":
            self.path = self.path / "tasks.json"
        self._lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def save(self, handle):
        if not isinstance(handle, dict) or not handle.get("task_id") or not handle.get("profile_id"):
            raise ValueError("Task handle requires task_id and profile_id")
        value = redact(copy.deepcopy(handle))
        value["updated_at"] = time.time()
        value.setdefault("created_at", value["updated_at"])
        with self._lock:
            values = self._read()
            values[value["profile_id"] + ":" + value["task_id"]] = value
            atomic_json(self.path, values)
        return copy.deepcopy(value)

    def get(self, task_id, profile_id=None):
        with self._lock:
            values = [v for v in self._read().values() if v["task_id"] == task_id and (not profile_id or v["profile_id"] == profile_id)]
        if not values:
            return None
        if len(values) > 1:
            raise ValueError("Task ID exists in multiple profiles; specify profile_id")
        return copy.deepcopy(values[0])

    def list(self):
        with self._lock:
            return copy.deepcopy(sorted(self._read().values(), key=lambda v: v.get("updated_at", 0), reverse=True))


def create_task_handle(response, profile_id, protocol):
    polling = protocol.get("polling")
    if not polling:
        return None
    data = response.data if isinstance(response, APIResponse) else response
    id_fields = polling.get("id_fields") or ["id", "task_id", "data.task_id", "output.task_id", "result"]
    task_id = next((str(value) for path in id_fields if isinstance(value := field_path(data, path), (str, int)) and str(value)), None)
    if not task_id:
        return None
    encoded = quote(task_id, safe="")
    route_params = protocol.get("route_params") or {}
    poll_path = polling["path"].replace("{task_id}", encoded)
    for key, value in route_params.items():
        poll_path = poll_path.replace("{" + str(key) + "}", quote(str(value), safe=""))
    if "{" in poll_path or "}" in poll_path:
        raise ValueError("Task polling route has unresolved parameters")
    def expand(value):
        if isinstance(value, str):
            return value.replace("{task_id}", task_id)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value
    return {"profile_id": profile_id, "task_id": task_id, "operation": protocol.get("id"),
            "poll_path": poll_path,
            "poll_method": polling.get("method", "GET"), "poll_body": expand(polling.get("body")),
            "poll_query": expand(polling.get("query")), "status_field": polling.get("status_field", "data.status"),
            "result_field": polling.get("result_field"), "success_value": polling.get("success_value"),
            "state": "submitted", "latest": response_record(response) if isinstance(response, APIResponse) else {"data": data}}


class TaskTimeout(TimeoutError):
    def __init__(self, handle):
        self.handle = handle
        super().__init__(f"Task {handle['task_id']} is still available for resuming; waiting timed out")


def task_state(data, handle):
    state = field_path(data, handle.get("status_field", "data.status"))
    if state is None and isinstance(data, dict):
        for block in (data, data.get("data"), data.get("output")):
            if isinstance(block, dict):
                state = block.get("status") or block.get("task_status") or block.get("state")
                if state:
                    break
    return str(state or "pending").lower()


async def wait_for_task(client, store, handle, timeout=1800, interrupt=None, *, interval=2):
    deadline = time.monotonic() + max(0, timeout)
    def check_interrupt():
        if interrupt:
            try:
                interrupted = interrupt()
            except BaseException:
                handle["state"] = "paused"
                store.save(handle)
                raise
            if interrupted:
                raise asyncio.CancelledError()
    try:
        while True:
            check_interrupt()
            try:
                pending = asyncio.create_task(client.request(handle.get("poll_method", "GET"), handle["poll_path"],
                                                             body=handle.get("poll_body"), query=handle.get("poll_query")))
                try:
                    while not pending.done():
                        check_interrupt()
                        if time.monotonic() >= deadline:
                            handle["state"] = "paused"
                            store.save(handle)
                            raise TaskTimeout(handle)
                        await asyncio.wait({pending}, timeout=min(.1, max(0, deadline - time.monotonic())))
                    response = pending.result()
                finally:
                    if not pending.done():
                        pending.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pending
            except EasyAPIError as exc:
                if exc.status not in {429, 500, 502, 503, 504} and not (exc.status == 0 and exc.retryable):
                    handle["state"] = "paused"
                    store.save(handle)
                    raise
                handle["state"] = "paused"
                store.save(handle)
            else:
                state = task_state(response.data, handle)
                handle.update(state=state, latest=response_record(response))
                store.save(handle)
                if state in DONE or (handle.get("success_value") and state == str(handle["success_value"]).lower()):
                    return response
                if state in FAILED:
                    raise EasyAPIError(f"Task {handle['task_id']} failed: {redact(response.data, client.api_key)}", response=response)
            if time.monotonic() >= deadline:
                handle["state"] = "paused"
                store.save(handle)
                raise TaskTimeout(handle)
            remaining = min(interval, deadline - time.monotonic())
            # Check ComfyUI interruption during the wait instead of sleeping through it.
            end = time.monotonic() + remaining
            while time.monotonic() < end:
                check_interrupt()
                await asyncio.sleep(min(.1, end - time.monotonic()))
    except asyncio.CancelledError:
        handle["state"] = "paused"
        store.save(handle)
        raise
