"""Same-origin realtime relay and durable, finite conversation snapshots.

Live connections never run on Comfy's prompt executor. Only explicit frozen
snapshots can be consumed by a workflow node.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024
ALLOWED_EVENTS = {
    "session.update", "input_audio_buffer.append", "input_audio_buffer.commit",
    "input_audio_buffer.clear", "response.create", "response.cancel",
    "conversation.item.create", "conversation.item.delete", "conversation.item.truncate",
    "conversation.item.retrieve", "output_audio_buffer.clear",
}
_SENSITIVE = re.compile(r"^(authorization|api[_-]?key|access[_-]?token|auth[_-]?token|secret|headers)$", re.I)


def _clean(value: Any, secret: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _clean(v, secret) for k, v in value.items() if not _SENSITIVE.match(str(k))}
    if isinstance(value, list):
        return [_clean(v, secret) for v in value]
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    return value


def realtime_url(base_url: str, model: str) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username:
        raise ValueError("服务地址必须为 http(s) URL")
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc,
                       path + "/realtime", urlencode({"model": model}), ""))


def _state_directory() -> Path:
    configured = os.environ.get("EASYAPI_STATE_DIR")
    if configured:
        return Path(configured) / "realtime"
    try:
        import folder_paths
        return Path(folder_paths.get_user_directory()) / "easyapi" / "realtime"
    except (ImportError, AttributeError):
        return Path.home() / ".easyapi-comfy" / "realtime"


class SnapshotStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def read(self, snapshot_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", snapshot_id or ""):
            raise ValueError("快照 ID 无效")
        with (self.directory / (snapshot_id + ".json")).open(encoding="utf-8") as f:
            return json.load(f)

    def list(self, profile_id=None) -> list[dict]:
        if not self.directory.exists():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                record = self.read(path.stem)
                if profile_id and record.get("profile_id") != profile_id:
                    continue
                records.append({k: record.get(k) for k in ("snapshot_id", "revision", "profile_id", "model", "created_at")})
            except (ValueError, OSError):
                continue
        return records

    def freeze(self, session: "Conversation") -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        snapshot_id = uuid.uuid4().hex
        session.revision += 1
        audio_name = None
        if session.audio:
            audio_name = snapshot_id + ".wav"
            with wave.open(str(self.directory / audio_name), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(session.output_rate)
                data = bytes(session.audio)
                f.writeframes(data[:len(data) // 2 * 2])
            if os.name != "nt":
                (self.directory / audio_name).chmod(0o600)
        record = {
            "snapshot_id": snapshot_id, "revision": session.revision,
            "session_id": session.id, "profile_id": session.profile_id,
            "model": session.model, "created_at": time.time(),
            "text": session.text(), "turns": list(session.turns),
            "events": list(session.events), "audio_file": audio_name,
            "sample_rate": session.output_rate, "audio_truncated": session.audio_truncated,
        }
        target = self.directory / (snapshot_id + ".json")
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        if os.name != "nt":
            target.chmod(0o600)
        session.dirty = False
        return record


class Conversation:
    def __init__(self, profile_id: str, model: str):
        self.id = uuid.uuid4().hex
        self.profile_id, self.model = profile_id, model
        self.created_at = self.last_activity = time.monotonic()
        self.revision = 0
        self.started = False
        self.response_active = False
        self.output_rate = 24000
        self.audio = bytearray()
        self.audio_truncated = False
        self.dirty = False
        self.response_number = 0
        self.turns: deque = deque(maxlen=200)
        self.events: deque = deque(maxlen=1000)
        self.drafts: dict[str, str] = {}
        self.event_bytes = 0
        self.completed: set[str] = set()

    def text(self) -> str:
        lines = [f"{'用户' if t['role'] == 'user' else '助手'}：{t['text']}" for t in self.turns]
        pending = [text for text in self.drafts.values() if text]
        return "\n".join(lines + (["助手：" + "".join(pending)] if pending else []))

    def remember(self, event: dict, secret: str = "") -> None:
        event = _clean(event, secret)
        kind = str(event.get("type", ""))
        self.last_activity = time.monotonic()
        if kind == "response.created":
            self.response_active = True
            self.response_number += 1
        elif kind in {"response.done", "response.cancelled", "response.failed"}:
            self.response_active = False
        if kind in {"session.created", "session.updated"}:
            config = event.get("session", {})
            fmt = config.get("output_audio_format") if isinstance(config, dict) else None
            if isinstance(fmt, dict):
                self.output_rate = int(fmt.get("rate", self.output_rate))
        if kind in {"response.audio.delta", "response.output_audio.delta"}:
            try:
                chunk = base64.b64decode(event.get("delta", event.get("audio", "")), validate=True)
            except (ValueError, TypeError):
                chunk = b""
            room = MAX_AUDIO_BYTES - len(self.audio)
            self.audio.extend(chunk[:room])
            self.audio_truncated |= len(chunk) > room
            self.dirty = True
            return
        if kind == "input_audio_buffer.append":
            return  # Raw microphone chunks need not be retained in conversation metadata.
        key = str(event.get("item_id", f"assistant-{self.response_number}")) + ":" + str(event.get("content_index", 0))
        if kind in {"response.audio_transcript.delta", "response.output_audio_transcript.delta",
                    "response.text.delta", "response.output_text.delta"}:
            if len(self.drafts) < 32 or key in self.drafts:
                self.drafts[key] = (self.drafts.get(key, "") + str(event.get("delta", "")))[:8192]
                self.dirty = True
        elif kind in {"response.audio_transcript.done", "response.output_audio_transcript.done",
                      "response.text.done", "response.output_text.done"}:
            text = str(event.get("text", event.get("transcript", self.drafts.get(key, "")))).strip()[:8192]
            if text and key not in self.completed:
                self.turns.append({"role": "assistant", "text": text})
                self.completed.add(key)
                self.dirty = True
            self.drafts.pop(key, None)
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = str(event.get("transcript", "")).strip()[:8192]
            if text:
                self.turns.append({"role": "user", "text": text})
                self.dirty = True
        elif kind == "conversation.item.create":
            item = event.get("item", {})
            if item.get("role") == "user":
                text = "\n".join(str(p.get("text", "")) for p in item.get("content", []) if p.get("type") == "input_text")
                if text:
                    self.turns.append({"role": "user", "text": text[:8192]})
                    self.dirty = True
        encoded = json.dumps(event, ensure_ascii=False)
        if self.event_bytes + len(encoded) <= MAX_EVENT_BYTES:
            self.events.append(event)
            self.event_bytes += len(encoded)


class RealtimeManager:
    def __init__(self, runtime, directory=None, *, idle_ttl=300, max_duration=1800,
                 max_sessions=16, guard: Callable | None = None):
        self.runtime = runtime
        self.snapshots = SnapshotStore(directory or _state_directory())
        self.idle_ttl, self.max_duration, self.max_sessions = idle_ttl, max_duration, max_sessions
        self.guard = guard
        self.sessions: dict[str, Conversation] = {}

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if self.guard:
            self.guard(request)
        profile_id, model = request.query.get("profile_id", ""), request.query.get("model", "").strip()
        if not model or len(model) > 256:
            raise web.HTTPBadRequest(text="请选择实时模型")
        try:
            profile = self.runtime.profiles.get(profile_id)
            address = realtime_url(profile["base_url"], model)
        except (KeyError, ValueError, FileNotFoundError):
            raise web.HTTPBadRequest(text="连接配置不存在或服务地址无效")
        if len(self.sessions) >= self.max_sessions:
            raise web.HTTPTooManyRequests(text="实时会话已达上限")
        browser = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME_BYTES)
        await browser.prepare(request)
        conversation = Conversation(profile_id, model)
        self.sessions[conversation.id] = conversation
        secret = str(profile.get("api_key", ""))
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=20)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                headers = {"Authorization": "Bearer " + secret, "OpenAI-Beta": "realtime=v1"}
                async with client.ws_connect(address, headers=headers, heartbeat=20,
                                             max_msg_size=MAX_FRAME_BYTES) as upstream:
                    await browser.send_json({"type": "easyapi.connected", "session_id": conversation.id})

                    async def from_browser():
                        async for frame in browser:
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                if frame.type == aiohttp.WSMsgType.ERROR:
                                    break
                                continue
                            try:
                                event = json.loads(frame.data)
                                if not isinstance(event, dict):
                                    raise ValueError()
                            except (ValueError, TypeError):
                                await browser.send_json({"type": "easyapi.error", "message": "实时消息必须为 JSON 对象"})
                                continue
                            kind = event.get("type", "")
                            conversation.last_activity = time.monotonic()
                            if kind == "easyapi.start":
                                conversation.started = True
                                rate = event.get("output_sample_rate", 24000)
                                conversation.output_rate = int(rate) if rate in {8000, 16000, 22050, 24000, 44100, 48000} else 24000
                                config = event.get("session")
                                if isinstance(config, dict):
                                    await upstream.send_json({"type": "session.update", "session": config})
                                await browser.send_json({"type": "easyapi.started"})
                            elif kind == "easyapi.freeze":
                                # Freeze a coherent point between events; subsequent events cannot mutate this file.
                                record = self.snapshots.freeze(conversation)
                                await browser.send_json({"type": "easyapi.snapshot", "snapshot_id": record["snapshot_id"],
                                                         "revision": record["revision"], "text": record["text"],
                                                         "audio_truncated": record["audio_truncated"]})
                            elif kind == "easyapi.interrupt":
                                if conversation.response_active:
                                    await upstream.send_json({"type": "response.cancel"})
                                    conversation.response_active = False
                                await browser.send_json({"type": "easyapi.interrupted"})
                            elif kind == "easyapi.stop":
                                conversation.started = False
                                await browser.send_json({"type": "easyapi.stopped"})
                                return
                            elif kind in ALLOWED_EVENTS and conversation.started:
                                conversation.remember(event, secret)
                                await upstream.send_json(event)
                            else:
                                await browser.send_json({"type": "easyapi.error", "message": "请先开始会话或检查事件类型"})

                    async def from_upstream():
                        async for frame in upstream:
                            if frame.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    event = json.loads(frame.data)
                                except ValueError:
                                    continue
                                if not isinstance(event, dict):
                                    continue
                                clean = _clean(event, secret)
                                conversation.remember(clean)
                                await browser.send_json(clean)
                            elif frame.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED}:
                                break

                    async def lifetime():
                        interval = max(0.05, min(5, self.idle_ttl / 3, self.max_duration / 3))
                        while True:
                            await asyncio.sleep(interval)
                            now = time.monotonic()
                            try:
                                current = self.runtime.profiles.get(profile_id)
                                changed = current.get("api_key") != profile.get("api_key") or current.get("base_url") != profile.get("base_url")
                            except (ValueError, KeyError, FileNotFoundError):
                                changed = True
                            if changed:
                                await browser.send_json({"type": "easyapi.expired", "message": "连接配置已变更，请重新连接"})
                                return
                            if now - conversation.last_activity > self.idle_ttl or now - conversation.created_at > self.max_duration:
                                await browser.send_json({"type": "easyapi.expired", "message": "会话已到期，请重新连接"})
                                return

                    pending = [asyncio.create_task(fn()) for fn in (from_browser, from_upstream, lifetime)]
                    try:
                        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not browser.closed:
                # Do not echo response headers, URLs, or upstream request objects.
                message = "实时服务连接失败，请检查模型、服务地址与后端凭据"
                if isinstance(exc, aiohttp.WSServerHandshakeError):
                    message += f"（HTTP {exc.status}）"
                await browser.send_json({"type": "easyapi.error", "message": message})
        finally:
            try:
                if conversation.dirty and (conversation.turns or conversation.audio or conversation.drafts):
                    record = self.snapshots.freeze(conversation)
                    if not browser.closed:
                        await browser.send_json({"type": "easyapi.snapshot", "snapshot_id": record["snapshot_id"],
                                                 "revision": record["revision"], "text": record["text"],
                                                 "audio_truncated": record["audio_truncated"]})
            except (OSError, ConnectionError, RuntimeError):
                pass
            finally:
                self.sessions.pop(conversation.id, None)
                await browser.close()
        return browser


_manager: RealtimeManager | None = None
_registered = False


def get_manager() -> RealtimeManager:
    global _manager
    if _manager is None:
        from .runtime import get_runtime
        _manager = RealtimeManager(get_runtime())
    return _manager


def get_snapshot(snapshot_id: str) -> dict:
    manager = get_manager()
    record = manager.snapshots.read(snapshot_id)
    record["audio"] = None
    if record.get("audio_file"):
        import numpy as np
        import torch
        with wave.open(str(manager.snapshots.directory / record["audio_file"]), "rb") as f:
            samples = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768
            record["audio"] = {"waveform": torch.from_numpy(samples.copy()).reshape(1, 1, -1),
                               "sample_rate": f.getframerate()}
    return record


def register_routes() -> None:
    global _registered
    if _registered:
        return
    from server import PromptServer
    from .server import require_management
    manager = get_manager()
    manager.guard = require_management
    routes = PromptServer.instance.routes

    @routes.get("/easyapi/realtime")
    async def realtime(request):
        return await manager.handle(request)

    @routes.get("/easyapi/snapshots/{snapshot_id}")
    async def snapshot(request):
        require_management(request)
        try:
            record = manager.snapshots.read(request.match_info["snapshot_id"])
        except (ValueError, FileNotFoundError):
            raise web.HTTPNotFound(text="快照不存在")
        return web.json_response({k: v for k, v in record.items() if k != "audio_file"})

    @routes.get("/easyapi/snapshots")
    async def snapshots(request):
        require_management(request)
        return web.json_response({"snapshots": manager.snapshots.list(request.query.get("profile_id"))})

    _registered = True
