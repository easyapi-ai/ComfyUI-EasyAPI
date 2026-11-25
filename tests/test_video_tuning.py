"""Offline shared-engine policy, real video sampling and paid-call replay regression tests."""
import asyncio
import base64
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from easyapi_comfy.client import APIResponse, EasyAPIError
from easyapi_comfy.media import MediaSource
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.video_tuning import VideoTuningService, get_video_tuning, _sample_times


def image_bytes(color="red"):
    target = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(target, "PNG")
    return target.getvalue()


def complete(prompt="A steady shot of the actor reading."):
    return {"prompt": prompt, "analysis": "Clarified one action.", "changes": ["Steady framing"], "risks": [],
            "suggestedParams": {"duration": 8}, "assumptions": [],
            "diagnosis": {"observations": ["A frame moves"], "causes": ["Possible contact"], "patches": ["Simplify contact"], "retest": ["Keep sources fixed"]}}


class FakeClient:
    def __init__(self, profile, owner):
        self.profile_id, self.owner = profile, owner

    async def discover_models(self, family=None, operation=None, **kwargs):
        if self.profile_id == "target":
            return [{"id": name, "operation_supported": True} for name in ("other-video", "wan3.0-video")]
        return [{"id": "text", "category": "text", "operation_supported": True, "input_modalities": ["text"]},
                {"id": "vision", "category": "text", "operation_supported": True, "input_modalities": ["text", "image"]}]

    async def model_schema(self, model, operation, **kwargs):
        self.owner.schemas.append((self.profile_id, model))
        if self.owner.schema_error:
            raise self.owner.schema_error
        return copy.deepcopy(self.owner.metadata)

    async def request(self, method, path, **kwargs):
        body = kwargs["body"]
        self.owner.calls.append((self.profile_id, copy.deepcopy(body)))
        self.owner.started.set()
        if self.owner.wait is not None:
            await self.owner.wait.wait()
        if self.owner.failure:
            raise self.owner.failure
        analysis = body["messages"][0]["content"].startswith("Analyze only")
        value = {"facts": ["Observed image shape"], "uncertainties": [], "invariants": ["Keep one actor"]} if analysis else self.owner.response
        text = value if isinstance(value, str) else json.dumps(value)
        return APIResponse(data={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

    async def download(self, address, path, **kwargs):
        self.owner.downloads.append(address)
        Path(path).write_bytes(self.owner.remote)


class VideoTuningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.runtime = Runtime(directory)
        self.runtime.input_dir.mkdir(parents=True)
        self.runtime.profiles.save("writer", "https://writer.invalid", "writer-fixture-secret")
        self.runtime.profiles.save("target", "https://target.invalid", "target-fixture-secret")
        self.calls, self.schemas, self.downloads = [], [], []
        self.response, self.failure, self.schema_error = complete(), None, None
        self.started, self.wait = asyncio.Event(), None
        self.remote = image_bytes()
        self.metadata = {"matched": True, "declared": True, "source": "docs", "request_schema": {"type": "object", "properties": {
            "duration": {"type": "integer", "minimum": 2, "maximum": 20}, "resolution": {"type": "string", "enum": ["480P", "1080P"]}}}}
        self.clients = {name: FakeClient(name, self) for name in ("writer", "target")}
        self.enterContext(patch.object(self.runtime, "client", side_effect=lambda profile_id=None: self.clients[profile_id or "writer"]))
        self.service = get_video_tuning(self.runtime)
        self.addAsyncCleanup(self.service.close)
        self.value = {"scope_id": "workflow:node-1", "nonce": 0, "prompt": "One actor reads a page.", "profile_id": "writer", "model": "text",
                      "target_profile_id": "target", "target_model": "other-video", "params": {"duration": 8, "resolution": "480P"},
                      "domain": "general", "mode": "auto", "output": "quick", "analyze": False}

    def local_image(self, name="first.png", color="red", role="first_frame"):
        path = self.runtime.input_dir / name
        path.write_bytes(image_bytes(color))
        return MediaSource("image", path, role, "image/png")

    async def test_separate_target_and_llm_profiles_and_public_result(self):
        source = self.local_image()
        value = self.value | {"media": [source], "analyze": True, "analysis_model": ""}
        before = copy.deepcopy(value)
        result = await self.service.run(value)
        self.assertEqual(self.schemas, [("target", "other-video")])
        self.assertEqual(len(self.calls), 1)
        profile, body = self.calls[0]
        self.assertEqual(profile, "writer")
        self.assertEqual(body["model"], "text")
        self.assertTrue(body["messages"][0]["content"].startswith("Optimize the supplied video prompt"))
        supplied = json.loads(body["messages"][1]["content"])
        self.assertEqual(supplied["policy"], "generic")
        self.assertEqual(supplied["media"][0]["label"], "生成参考1")
        self.assertEqual(result["context"]["params"], self.value["params"])
        self.assertEqual(value, before)
        self.assertTrue(any("not been visually analyzed" in item for item in result["sourceAnalysis"]["uncertainties"]))
        rendered = json.dumps(result)
        for private in (str(source.path), "writer-fixture-secret", "target-fixture-secret", "data:image", self.runtime.profiles.revision("writer")):
            self.assertNotIn(private, rendered)
        self.assertEqual(result["input_fingerprint"], result["context"]["input_fingerprint"])

    async def test_all_domains_outputs_and_explicit_analysis(self):
        source = self.local_image()
        for domain in ("general", "drama", "ecommerce"):
            for output in ("quick", "full", "diagnose"):
                value = self.value | {"scope_id": domain + output, "domain": domain, "output": output, "media": [source],
                                      "analysis_model": "vision", "analyze": True, "issue": {"description": "Geometry bends", "start": 0, "end": 1}}
                result = await self.service.run(value)
                self.assertIn("Observed image shape", result["sourceAnalysis"]["facts"])
        self.assertEqual(len(self.calls), 18)
        self.assertEqual(self.calls[0][1]["model"], "vision")
        self.assertEqual(self.calls[1][1]["model"], "text")
        content = self.calls[0][1]["messages"][1]["content"]
        self.assertEqual(sum(item["type"] == "image_url" for item in content), 1)
        self.assertFalse(any(item["type"] in {"video_url", "audio_url"} for item in content))
        self.assertTrue(all("@图片" not in item["text"] for item in content if item["type"] == "text"))
        webp = self.runtime.input_dir / "ref.webp"
        Image.new("RGB", (8, 6), "green").save(webp, "WEBP")
        await self.service.run(self.value | {"scope_id": "webp", "media": [MediaSource("image", webp, "first_frame")],
                                            "analysis_model": "vision", "analyze": True})
        content = self.calls[-2][1]["messages"][1]["content"]
        inline = next(item["image_url"]["url"] for item in content if item["type"] == "image_url")
        self.assertTrue(inline.startswith("data:image/jpeg;base64,"))
        with Image.open(io.BytesIO(base64.b64decode(inline.split(",", 1)[1]))) as decoded:
            self.assertEqual(decoded.format, "JPEG")

    async def test_wan_reference_rules_and_generic_does_not_invent_tokens(self):
        source = self.local_image()
        self.response = complete("Preserve @图片1 while the actor reads.")
        result = await self.service.run(self.value | {"target_model": "wan3.0-video", "media": [source]})
        self.assertEqual(result["context"]["policy"], "wan3")
        self.assertIn("Apply Wan 3", self.calls[-1][1]["messages"][0]["content"])
        self.response = complete("Preserve @图片2.")
        with self.assertRaisesRegex(ValueError, "outside"):
            await self.service.run(self.value | {"nonce": 1, "target_model": "wan3.0-video", "media": [source]})
        self.response = complete("Preserve @图片1.")
        with self.assertRaisesRegex(ValueError, "unverified"):
            await self.service.run(self.value | {"nonce": 2, "media": [source]})

    async def test_preflight_schema_and_permissions_never_submit_or_lock_nonce(self):
        for invalid in (False, 0, []):
            with self.subTest(params=invalid), self.assertRaisesRegex(ValueError, "JSON object"):
                await self.service.run(self.value | {"params": invalid})
        self.metadata["request_schema"]["properties"]["duration"]["maximum"] = 4
        with self.assertRaisesRegex(ValueError, "duration"):
            await self.service.run(self.value)
        self.assertEqual(self.calls, [])
        self.metadata["request_schema"]["properties"]["duration"]["maximum"] = 20
        self.schema_error = EasyAPIError("revoked", status=403)
        with self.assertRaises(EasyAPIError):
            await self.service.run(self.value)
        self.assertEqual(self.calls, [])
        self.schema_error = None
        result = await self.service.run(self.value)
        self.assertFalse(result["cache_hit"])

    async def test_success_cache_persists_and_uses_stable_private_revision(self):
        first = await self.service.run(self.value)
        old_public = self.runtime.profiles.list_public()[0]["model_catalog_revision"]
        restored = Runtime(self.runtime.state_dir)
        self.assertNotEqual(old_public, restored.profiles.list_public()[0]["model_catalog_revision"])
        restored.client = self.runtime.client
        service = VideoTuningService(restored)
        self.addAsyncCleanup(service.close)
        second = await service.run(self.value)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["input_fingerprint"], second["input_fingerprint"])
        self.assertEqual(len(self.calls), 1)
        self.runtime.profiles.save("writer", "https://writer.invalid", "rotated-fixture-secret")
        third = await self.service.run(self.value)
        self.assertFalse(third["cache_hit"])
        self.assertNotEqual(third["input_fingerprint"], first["input_fingerprint"])
        self.assertEqual(len(self.calls), 2)

    async def test_actual_file_content_role_order_scope_and_nonce_are_cache_inputs(self):
        first = self.local_image("one.png")
        same = self.local_image("renamed.png")
        base = self.value | {"media": [first]}
        result = await self.service.run(base)
        hit = await self.service.run(self.value | {"media": [same]})
        self.assertTrue(hit["cache_hit"], "generated temporary filenames must not defeat reuse of identical contents")
        Path(first.path).write_bytes(image_bytes("blue"))
        changed = await self.service.run(base)
        self.assertNotEqual(result["input_fingerprint"], changed["input_fingerprint"])
        for update in ({"scope_id": "another-node"}, {"nonce": 1}, {"params": {"duration": 9}},
                       {"media": [MediaSource("image", first.path, "reference_image")]}, {"media": [first, same]}, {"media": [same, first]}):
            value = base | update
            self.assertFalse((await self.service.run(value))["cache_hit"])
        self.assertEqual(len(self.calls), 8)

    async def test_failed_paid_attempt_does_not_replace_success_or_automatically_repeat(self):
        good = await self.service.run(self.value)
        self.response = "truncated {"
        with self.assertRaisesRegex(ValueError, "structured"):
            await self.service.run(self.value | {"nonce": 1})
        with self.assertRaisesRegex(ValueError, "increase nonce"):
            await self.service.run(self.value | {"nonce": 1})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.service.latest(self.value["scope_id"])["prompt"], good["prompt"])
        self.assertTrue((await self.service.run(self.value))["cache_hit"])
        self.response, self.failure = complete(), EasyAPIError("connection lost", retryable=True)
        with self.assertRaises(EasyAPIError):
            await self.service.run(self.value | {"nonce": 2})
        reloaded = VideoTuningService(self.runtime)
        self.addAsyncCleanup(reloaded.close)
        with self.assertRaisesRegex(ValueError, "increase nonce"):
            await reloaded.run(self.value | {"nonce": 2})
        self.assertEqual(len(self.calls), 3)

    async def test_concurrent_waiters_share_one_post_and_cancel_is_per_consumer(self):
        self.wait = asyncio.Event()
        one = asyncio.create_task(self.service.run(self.value))
        await self.started.wait()
        two = asyncio.create_task(self.service.run(self.value))
        await asyncio.sleep(.01)
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await one
        self.wait.set()
        self.assertEqual((await two)["prompt"], self.response["prompt"])
        self.assertEqual(len(self.calls), 1)

    async def test_last_consumer_cancellation_records_unknown_attempt(self):
        self.wait = asyncio.Event()
        pending = asyncio.create_task(self.service.run(self.value))
        await self.started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await asyncio.sleep(.01)
        with self.assertRaisesRegex(ValueError, "increase nonce"):
            await self.service.run(self.value)
        self.assertEqual(len(self.calls), 1)

    async def test_url_hashes_actual_bytes_and_safe_view_resolution(self):
        value = self.value | {"media": [{"kind": "image", "role": "first_frame", "url": "https://fixture.invalid/view?filename=remote.png"}]}
        first = await self.service.run(value)
        self.remote = image_bytes("blue")
        second = await self.service.run(value)
        self.assertNotEqual(first["input_fingerprint"], second["input_fingerprint"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.downloads), 2)
        source = self.local_image()
        fake_folders = type("Folders", (), {"get_input_directory": lambda: self.runtime.input_dir, "get_output_directory": lambda: self.runtime.output_dir, "get_temp_directory": lambda: self.runtime.input_dir})
        with patch.dict("sys.modules", {"folder_paths": fake_folders}):
            await self.service.run(self.value | {"scope_id": "view-node", "media": [{"kind": "image", "role": "first_frame", "previewUrl": "/view?filename=first.png&type=input"}]})
        self.assertEqual(len(self.downloads), 2, "Comfy /view assets resolve locally, not through HTTP")
        with self.assertRaisesRegex(ValueError, "outside"):
            await self.service.run(self.value | {"media": [{"kind": "image", "path": str(Path(self.runtime.state_dir) / "profiles.json")}]})

    async def test_audio_remains_unheard_metadata_and_unready_media_is_not_observed(self):
        self.response = complete()
        result = await self.service.run(self.value | {"media": [{"kind": "audio", "url": "data:audio/wav;base64,AAAA", "role": "reference_audio"},
                                                               {"kind": "image", "ready": False, "role": "reference_image"}],
                                                    "analyze": True, "analysis_model": "vision"})
        self.assertEqual(len(self.calls), 1)
        warnings = result["sourceAnalysis"]["uncertainties"]
        self.assertTrue(any("not listened" in item for item in warnings))
        self.assertTrue(any("not ready" in item for item in warnings))
        self.assertTrue(any("No ready visual" in item for item in warnings))

    async def test_diagnostic_schema_evidence_and_media_budget_are_enforced(self):
        self.response = complete("Use 观测帧1.")
        with self.assertRaisesRegex(ValueError, "evidence"):
            await self.service.run(self.value)
        self.response = complete()
        del self.response["diagnosis"]
        with self.assertRaisesRegex(ValueError, "Diagnostic"):
            await self.service.run(self.value | {"nonce": 1, "output": "diagnose", "issue": {"description": "bend"}})
        self.response = complete()
        with patch("easyapi_comfy.video_tuning.MAX_TOTAL_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "budget"):
                await self.service.run(self.value | {"nonce": 2, "media": [self.local_image()], "analyze": True, "analysis_model": "vision"})
        self.assertEqual(len(self.calls), 2)

    async def test_real_pyav_video_frames_are_evidence_not_generation_references(self):
        import av
        import numpy as np
        video = self.runtime.input_dir / "diagnosis.mp4"
        with av.open(str(video), "w") as output:
            stream = output.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            for index in range(20):
                pixels = np.zeros((48, 64, 3), dtype=np.uint8)
                pixels[:, :, 0] = index * 10
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        self.response = complete("Keep @图片1 stable.")
        result = await self.service.run(self.value | {"target_model": "wan3.0-video", "media": [self.local_image()],
                                                    "resultMedia": [{"kind": "video", "path": str(video), "role": "result_video", "analysisOnly": True}],
                                                    "analyze": True, "analysis_model": "vision", "output": "diagnose",
                                                    "issue": {"description": "Motion changes", "start": .5, "end": 1.5}})
        content = self.calls[0][1]["messages"][1]["content"]
        image_parts = [item for item in content if item["type"] == "image_url"]
        self.assertGreater(len(image_parts), 1)
        self.assertLessEqual(len(image_parts), 24)
        evidence = [json.loads(item["text"]) for item in content if item["type"] == "text" and '"video_frame"' in item["text"]]
        self.assertTrue(all(item["analysisOnly"] and "sourceMediaId" in item and "timestamp" in item for item in evidence))
        self.assertTrue(result["context"]["media"][1]["analysisOnly"])
        serialized = json.dumps(result)
        self.assertNotIn("data:image", serialized)
        self.assertNotIn(str(video), serialized)

    def test_sampling_times_stay_bounded_and_repeatable(self):
        self.assertEqual(_sample_times(2, {"start": .5, "end": 1.5}, 2), [0, 1.96])
        self.assertEqual(len(_sample_times(2, None, 1)), 1)
