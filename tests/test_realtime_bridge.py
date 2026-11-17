import asyncio
import base64
import json
import struct
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web

from easyapi_comfy.realtime import Conversation, RealtimeManager, SnapshotStore, realtime_url
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes, require_management


async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


async def receive_type(ws, kind):
    for _ in range(30):
        event = await asyncio.wait_for(ws.receive_json(), 3)
        if event.get("type") == kind:
            return event
    raise AssertionError(f"event {kind} missing")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.received = []
        self.upstream_closed = asyncio.Event()

        async def upstream(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer test-secret-never-public")
            self.assertEqual(request.query["model"], "test-realtime")
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "session.created", "session": {"id": "mock-session"}})
            try:
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(message.data)
                    self.received.append(event)
                    if event["type"] == "session.update":
                        await ws.send_json({"type": "session.updated", "session": event["session"]})
                    elif event["type"] == "response.create":
                        await ws.send_json({"type": "response.created", "response": {"id": "r1"}})
                        await ws.send_json({"type": "response.audio_transcript.delta", "item_id": "a1", "delta": "你好"})
                        await ws.send_json({"type": "response.audio.delta", "delta": base64.b64encode(struct.pack("<100h", *range(100))).decode()})
                        await ws.send_json({"type": "response.audio_transcript.done", "item_id": "a1", "transcript": "你好，世界"})
                        await ws.send_json({"type": "response.done", "response": {"status": "completed"}})
            finally:
                self.upstream_closed.set()
            return ws

        app = web.Application()
        app.router.add_get("/v1/realtime", upstream)
        self.up_runner, self.up_url = await serve(app)
        self.runtime = Runtime(Path(self.temp.name) / "state")
        self.runtime.profiles.save("test", self.up_url, "test-secret-never-public")
        self.manager = RealtimeManager(self.runtime, Path(self.temp.name) / "snapshots", guard=require_management)
        app = web.Application()
        app.add_routes(build_routes(self.runtime))
        app.router.add_get("/easyapi/realtime", self.manager.handle)
        self.runner, self.url = await serve(app)
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        response = await self.client.get(self.url + "/easyapi/bootstrap")
        self.nonce = (await response.json())["management_nonce"]

    async def asyncTearDown(self):
        await self.client.close()
        await self.runtime.close()
        await self.runner.cleanup()
        await self.up_runner.cleanup()
        self.temp.cleanup()

    async def connect(self, nonce=None, origin=None):
        return await self.client.ws_connect(self.url + "/easyapi/realtime", params={
            "profile_id": "test", "model": "test-realtime", "nonce": self.nonce if nonce is None else nonce},
            headers={"Origin": origin or self.url})

    async def test_bidirectional_audio_text_freeze_and_cleanup(self):
        ws = await self.connect()
        await receive_type(ws, "easyapi.connected")
        await ws.send_json({"type": "easyapi.start", "session": {"voice": "alloy", "turn_detection": {"type": "server_vad"}}, "output_sample_rate": 24000})
        await receive_type(ws, "easyapi.started")
        await ws.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(b"\0\0").decode()})
        await ws.send_json({"type": "conversation.item.create", "item": {"role": "user", "type": "message", "content": [{"type": "input_text", "text": "你好"}]}})
        await ws.send_json({"type": "response.create"})
        await receive_type(ws, "response.done")
        await ws.send_json({"type": "easyapi.freeze"})
        frozen = await receive_type(ws, "easyapi.snapshot")
        record = self.manager.snapshots.read(frozen["snapshot_id"])
        self.assertIn("用户：你好", record["text"])
        self.assertIn("助手：你好，世界", record["text"])
        self.assertNotIn("test-secret", json.dumps(record))
        with wave.open(str(self.manager.snapshots.directory / record["audio_file"]), "rb") as audio:
            self.assertEqual(audio.getnframes(), 100)
            self.assertEqual(audio.getframerate(), 24000)
        self.assertTrue(any(e["type"] == "input_audio_buffer.append" for e in self.received))
        await ws.send_json({"type": "conversation.item.create", "item": {"role": "user", "content": [{"type": "input_text", "text": "第二轮"}]}})
        await ws.send_json({"type": "easyapi.freeze"})
        second = await receive_type(ws, "easyapi.snapshot")
        self.assertNotEqual(frozen["snapshot_id"], second["snapshot_id"])
        self.assertEqual(second["revision"], 2)
        self.assertNotIn("第二轮", self.manager.snapshots.read(frozen["snapshot_id"])["text"])
        await ws.send_json({"type": "easyapi.stop"})
        await receive_type(ws, "easyapi.stopped")
        await ws.close()
        await asyncio.wait_for(self.upstream_closed.wait(), 2)
        await asyncio.sleep(.03)
        self.assertEqual(self.manager.sessions, {})

    async def test_ws_requires_same_origin_cookie_and_nonce(self):
        for kwargs in ({"nonce": "wrong"}, {"origin": "https://evil.invalid"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(aiohttp.WSServerHandshakeError) as context:
                    await self.connect(**kwargs)
                self.assertEqual(context.exception.status, 403)
        self.assertEqual(self.manager.sessions, {})

    async def test_idle_ttl_closes_upstream(self):
        self.manager.idle_ttl = .15
        ws = await self.connect()
        await receive_type(ws, "easyapi.connected")
        await receive_type(ws, "easyapi.expired")
        await ws.close()
        await asyncio.wait_for(self.upstream_closed.wait(), 2)

    async def test_disconnect_retains_recoverable_snapshot(self):
        ws = await self.connect()
        await receive_type(ws, "easyapi.connected")
        await ws.send_json({"type": "easyapi.start"})
        await receive_type(ws, "easyapi.started")
        await ws.send_json({"type": "conversation.item.create", "item": {"role": "user", "content": [{"type": "input_text", "text": "保留我的对话"}]}})
        await ws.send_json({"type": "response.create"})
        await receive_type(ws, "response.done")
        await ws.close()
        await asyncio.wait_for(self.upstream_closed.wait(), 2)
        await asyncio.sleep(.05)
        saved = self.manager.snapshots.list("test")
        self.assertEqual(len(saved), 1)
        self.assertIn("保留我的对话", self.manager.snapshots.read(saved[0]["snapshot_id"])["text"])

    async def test_profile_rotation_terminates_existing_socket(self):
        self.manager.idle_ttl = .3
        ws = await self.connect()
        await receive_type(ws, "easyapi.connected")
        self.runtime.profiles.save("test", self.up_url, "replacement-mock-key")
        event = await receive_type(ws, "easyapi.expired")
        self.assertIn("配置已变更", event["message"])
        await ws.close()
        await asyncio.wait_for(self.upstream_closed.wait(), 2)


class SnapshotTests(unittest.TestCase):
    def test_audio_is_bounded_and_credentials_are_not_retained(self):
        conversation = Conversation("test", "model")
        with patch("easyapi_comfy.realtime.MAX_AUDIO_BYTES", 8):
            conversation.remember({"type": "response.audio.delta", "delta": base64.b64encode(b"x" * 20).decode()})
        self.assertEqual(len(conversation.audio), 8)
        self.assertTrue(conversation.audio_truncated)
        conversation.remember({"type": "error", "api_key": "secret", "error": {"message": "secret"}}, "secret")
        self.assertNotIn('"secret"', json.dumps(list(conversation.events)))

    def test_urls_use_gateway_prefix_and_snapshot_paths_cannot_traverse(self):
        self.assertEqual(realtime_url("https://gateway.test/prefix/v1", "model with space"), "wss://gateway.test/prefix/v1/realtime?model=model+with+space")
        with self.assertRaises(ValueError):
            SnapshotStore("unused").read("../../profiles")


if __name__ == "__main__":
    unittest.main()
