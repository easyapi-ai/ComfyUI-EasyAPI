"""Wan tuning uses local HTTP streams and shared cross-host prompt fixtures."""
import asyncio
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes
from easyapi_comfy.prompt_tools import input_modalities


class WanTunerCoreTests(unittest.TestCase):
    def test_declared_multimodal_tags_without_hardcoded_model_names(self):
        self.assertEqual(input_modalities({"tags": ["文本", "多模态"]}), ["image"])
        self.assertEqual(input_modalities({"capabilities": {"input_modalities": ["text", "image", "video"]}}),
                         ["image", "text", "video"])
        self.assertIsNone(input_modalities({"id": "qwen-sounding-name", "category": "text"}))

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the shared browser engine")
    def test_shared_engine_and_split_streams(self):
        script = r'''
import assert from "node:assert/strict";
import fs from "node:fs";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";
const core = await import(pathToFileURL(process.cwd() + "/web/wan-tuner-core.js").href);
const knowledge = JSON.parse(fs.readFileSync("web/wan-tuner-knowledge.json", "utf8"));
assert.equal(knowledge.content_hash, crypto.createHash("sha256").update(JSON.stringify(knowledge.sections)).digest("hex"));
const base = { domain: "drama", output: "quick", prompt: '男人说：“先还书。”',
  targetModel: "wan3.0-video", params: { duration: 15, resolution: "480P", generate_audio: false }, media: [], locale: "zh-CN" };
for (const domain of core.TUNING_DOMAINS) for (const mode of core.TUNING_MODES) {
  const context = { ...base, domain, mode };
  const copy = JSON.stringify(context);
  const messages = core.buildTuningMessages(context, null, knowledge);
  assert.equal(JSON.stringify(context), copy);
  const input = JSON.parse(messages[1].content);
  assert.equal(input.prompt, base.prompt);
  assert.equal(input.mode, mode);
  assert.deepEqual(input.params, base.params);
  assert.match(messages[0].content, /read-only/);
}
const capabilities = { properties: { duration: { type: "integer", minimum: 2, maximum: 30 },
  resolution: { type: "string", enum: ["480P", "720P", "1080P"] } }, required: ["prompt"],
  inputRoles: ["first_frame", "last_frame"] };
const capContext = { ...base, capabilities };
const capBefore = JSON.stringify(capContext);
assert.deepEqual(JSON.parse(core.buildTuningMessages(capContext, null, knowledge)[1].content).capabilities, capabilities);
assert.deepEqual(JSON.parse(core.buildTuningMessages({ ...base, capabilities: { known: false } }, null, knowledge)[1].content).capabilities, { known: false });
assert.equal(JSON.stringify(capContext), capBefore);
assert.throws(() => core.buildTuningMessages({ ...base, capabilities: ["guessed"] }, null, knowledge), /plain metadata/);
assert.throws(() => core.buildTuningMessages({ ...base, capabilities: "unknown" }, null, knowledge), /plain metadata/);
const media = [
  { kind: "image", role: "first_frame", ready: true, url: "data:image/png;base64,AA==" },
  { kind: "video", role: "reference_video", ready: false },
  { kind: "audio", role: "reference_audio", ready: true, url: "https://fixture.invalid/sound.wav" },
  { kind: "image", role: "last_frame", ready: true, url: "https://fixture.invalid/end.png" }
];
assert.deepEqual(core.normalizeTuningMedia(media).map(x => x.label), ["@图片1","@视频1","@音频1","@图片2"]);
assert.equal(core.inferTuningMode({ media }), "first-last-frame");
assert.equal(core.inferTuningMode({ media: [{ kind: "video" }], editIntent: true }), "v2v");
const analysis = core.buildAnalysisMessages({ ...base, media });
assert.equal(analysis[1].content.filter(x => x.type === "image_url").length, 2);
assert.equal(analysis[1].content.filter(x => x.type === "video_url").length, 0);
assert.equal(analysis[1].content.filter(x => x.type === "audio_url").length, 0);
assert.throws(() => core.buildAnalysisMessages({ ...base, issue: { description: "stairs", start: 6, end: 4 } }));
assert.throws(() => core.normalizeTuningMedia([{ kind: "image", url: "file:///secret.png" }]));
const diagnostic = core.buildTuningMessages({ ...base, output: "diagnose", issue: { description: "4–6秒台阶变形", start: 4, end: 6 } }, null, knowledge);
assert.match(diagnostic[0].content, /edge lines/);
const value = { prompt: "保持画面清晰干净。", analysis: "Stable camera.", changes: ["One action"],
  risks: ["Physical contact"], suggestedParams: { duration: 10 }, assumptions: [],
  diagnosis: { observations: ["4s edge moves"], causes: ["contact"], patches: ["reduce contact"], retest: ["same seed"] } };
const fence = String.fromCharCode(96).repeat(3);
assert.deepEqual(core.parseTuningResult(fence + "json\n" + JSON.stringify(value) + "\n" + fence), value);
assert.throws(() => core.parseTuningResult({ prompt: "usable" }, { requireDiagnosis: true }), /Diagnostic output/);
assert.throws(() => core.parseTuningResult({ ...value, diagnosis: { ...value.diagnosis, patches: [] } }, { requireDiagnosis: true }));
assert.equal(core.parseAnalysisResult('{"facts":[],"uncertainties":["unseen"],"invariants":[]}').uncertainties[0], "unseen");
for (const bad of ['{"prompt":', '{"analysis":"missing"}', 'prose {"prompt":"x"}', '{"prompt":"x","changes":[{}]}'])
  assert.throws(() => core.parseTuningResult(bad));
assert.throws(() => core.parseAnalysisResult('{"facts":[]}'));
const evidenceContext = { ...base, mode: "v2v", media: [
  { id: "v", kind: "video", role: "reference_video", ready: false },
  ...Array.from({ length: 7 }, (_, i) => ({ kind: "image", role: "diagnostic_frame", analysisOnly: true,
    sourceMediaId: "v", sourceLabel: "@视频1", timestamp: 3.5 + i * .5, ready: true, url: "data:image/jpeg;base64,AA==" }))
] };
const evidence = core.normalizeTuningMedia(evidenceContext.media);
assert.equal(evidence[1].label, "观测帧1");
assert.equal(evidence[1].sourceMediaId, "v");
assert.equal(evidence[1].timestamp, 3.5);
assert.equal(core.parseTuningResult({ prompt: "修正@视频1的台阶" }, { context: evidenceContext }).prompt, "修正@视频1的台阶");
assert.throws(() => core.parseTuningResult({ prompt: "参考@图片7" }, { context: evidenceContext }), /outside/);
assert.throws(() => core.parseTuningResult({ prompt: "复制观测帧3" }, { context: evidenceContext }), /evidence/);
const bytes = new TextEncoder().encode('event: delta\r\ndata: {"text":"你好"}\r\n\r\nevent: result\ndata: {"text":"完成","finish_reason":"stop"}');
const response = new Response(new ReadableStream({ start(controller) {
  Array.from(bytes, byte => Uint8Array.of(byte)).forEach(x => controller.enqueue(x)); controller.close();
} }), { status: 200 });
const seen = [];
const result = await core.readEventStream(response, event => seen.push(event));
assert.equal(seen[0].type, "delta");
assert.equal(seen[0].data.text, "你好");
assert.equal(result.text, "完成");
await assert.rejects(core.readEventStream(new Response('event: error\ndata: {"message":"failed"}\n\n'), () => {}), /failed/);
const cancelled = new AbortController(); cancelled.abort();
await assert.rejects(core.readEventStream(new Response(""), () => {}, cancelled.signal), { name: "AbortError" });
console.log("Shared Wan core fixtures passed");
'''
        result = subprocess.run(["node", "--input-type=module", "-e", script], text=True, capture_output=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class WanTunerRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.calls = []
        self.finish = "stop"
        self.slow = False
        self.started, self.closed = asyncio.Event(), asyncio.Event()

        async def models(request):
            self.assertEqual(request.headers["Authorization"], "Bearer tuner-fixture-key")
            return web.json_response({"data": [
                {"id": "chat", "category": "text", "supported_endpoint_types": ["openai"], "input_modalities": ["text"]},
                {"id": "vision", "category": "text", "supported_endpoint_types": ["openai"],
                 "architecture": {"input_modalities": ["text", "image"]}},
            ]})

        async def chat(request):
            payload = await request.json()
            self.calls.append(payload)
            self.assertEqual(request.headers["Authorization"], "Bearer tuner-fixture-key")
            content = '{"prompt":"保持画面清晰干净","analysis":"test","changes":[],"risks":[],"suggestedParams":{},"assumptions":[]}'
            if not payload.get("stream"):
                return web.json_response({"choices": [{"message": {"content": content}, "finish_reason": self.finish}]})
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            self.started.set()
            if self.slow:
                try:
                    while request.transport and not request.transport.is_closing():
                        await asyncio.sleep(.02)
                finally:
                    self.closed.set()
                return response
            events = [
                {"choices": [{"delta": {"content": content[:18]}}]},
                {"choices": [{"delta": {"content": content[18:]}}]},
                {"choices": [{"delta": {}, "finish_reason": self.finish}]},
            ]
            for event in events:
                raw = ("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode()
                for start in range(0, len(raw), 7):
                    await response.write(raw[start:start + 7])
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response

        gateway_app = web.Application()
        gateway_app.router.add_get("/v1/models", models)
        gateway_app.router.add_post("/v1/chat/completions", chat)
        gateway = await self.enterAsyncContext(TestServer(gateway_app))
        runtime = Runtime(self.directory / "state")
        self.runtime = runtime
        self.addAsyncCleanup(runtime.close)
        runtime.profiles.save("fixture", str(gateway.make_url("/")).rstrip("/"), "tuner-fixture-key")
        app = web.Application()
        app.add_routes(build_routes(runtime))
        self.browser = await self.enterAsyncContext(TestClient(TestServer(app)))
        self.origin = str(self.browser.make_url("/")).rstrip("/")
        session = await (await self.browser.get("/easyapi/bootstrap")).json()
        self.headers = {"Origin": self.origin, "X-EasyAPI-Management": session["management_nonce"]}
        self.payload = {"action": "wan_tune", "profile_id": "fixture", "model": "chat", "stream": True,
                        "messages": [{"role": "system", "content": "structured prompt"},
                                     {"role": "user", "content": "人站在台阶上"}]}

    async def test_stream_and_catalog_capability_metadata(self):
        response = await self.browser.get("/easyapi/prompt/models?profile_id=fixture")
        models = (await response.json())["models"]
        self.assertFalse(models[0]["supports_image"])
        self.assertTrue(models[1]["supports_image"])
        response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers, json=self.payload)
        body = await response.text()
        self.assertEqual(response.content_type, "text/event-stream")
        for event in ("stage", "delta", "result"):
            self.assertIn("event: " + event, body)
        self.assertIn('"finish_reason": "stop"', body)
        self.assertNotIn("tuner-fixture-key", body)
        self.assertEqual(self.calls[0]["model"], "chat")
        self.assertNotIn("duration", self.calls[0])

    async def test_allowed_model_and_supported_media(self):
        content = [{"type": "text", "text": "4–6s"},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
        payload = self.payload | {"action": "wan_analyze", "stream": False, "messages": [{"role": "user", "content": content}]}
        for overrides in ({"model": "missing"}, {"model": "chat"}, {"messages": []},
                          {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///x"}}]}]}):
            response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers, json=payload | overrides)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])
        response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers, json=payload | {"model": "vision"})
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["text"])
        self.assertEqual(len(self.calls), 1)
        response = await self.browser.post("/easyapi/prompt/transform", json=payload)
        self.assertEqual(response.status, 403)

    async def test_truncated_stream_has_error_without_result(self):
        self.finish = "length"
        response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers, json=self.payload)
        result = await response.text()
        self.assertIn("event: error", result)
        self.assertNotIn("event: result", result)

    async def test_nonstream_tool_calls_cannot_be_adopted_as_complete_text(self):
        for finish in ("length", "content_filter", "tool_calls", "function_call"):
            self.finish = finish
            response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers,
                                               json=self.payload | {"stream": False})
            self.assertEqual(response.status, 502)
            self.assertNotIn("text", await response.json())

    async def test_disconnect_cancels_upstream_before_more_tokens(self):
        self.slow = True
        response = await self.browser.post("/easyapi/prompt/transform", headers=self.headers, json=self.payload)
        await asyncio.wait_for(self.started.wait(), 2)
        response.close()
        await asyncio.wait_for(self.closed.wait(), 2)
        self.assertEqual(len(self.calls), 1)

    async def test_ready_media_files_and_explicit_missing_state(self):
        directories = {name: self.directory / name for name in ("input", "output", "temp")}
        for path in directories.values():
            path.mkdir()
        (directories["input"] / "frame.png").write_bytes(b"png-fixture")
        self.runtime.output_dir.mkdir(parents=True, exist_ok=True)
        (self.runtime.output_dir / "generated.png").write_bytes(b"generated-png")
        folder_paths = types.SimpleNamespace(get_input_directory=lambda: str(directories["input"]),
                                            get_output_directory=lambda: str(directories["output"]),
                                            get_temp_directory=lambda: str(directories["temp"]))
        payload = {"profile_id": "fixture", "media": [
            {"id": "start", "kind": "image", "role": "first_frame", "filename": "frame.png"},
            {"id": "missing", "kind": "video", "filename": "missing.mp4"},
            {"id": "generated", "kind": "image", "filename": "generated.png", "type": "easyapi"},
        ]}
        with patch.dict(sys.modules, {"folder_paths": folder_paths}):
            response = await self.browser.post("/easyapi/prompt/media", headers=self.headers, json=payload)
            self.assertEqual(response.status, 200)
            media = (await response.json())["media"]
            self.assertEqual(media[0]["url"], "data:image/png;base64," + base64.b64encode(b"png-fixture").decode())
            self.assertTrue(media[0]["ready"])
            self.assertFalse(media[1]["ready"])
            self.assertNotIn("url", media[1])
            self.assertEqual(media[2]["url"], "data:image/png;base64," + base64.b64encode(b"generated-png").decode())
            for media_type in ("input", "easyapi"):
                payload["media"] = [{"kind": "image", "filename": "../outside.png", "type": media_type}]
                response = await self.browser.post("/easyapi/prompt/media", headers=self.headers, json=payload)
                self.assertEqual(response.status, 400)
            response = await self.browser.post("/easyapi/prompt/media", json=payload)
            self.assertEqual(response.status, 403)
