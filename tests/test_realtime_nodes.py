"""Request construction contracts independent of a running Comfy installation."""
import asyncio
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from easyapi_comfy.media import MediaSource
from easyapi_comfy.protocols import get_protocol


def load_nodes():
    class ComfyType:
        @staticmethod
        def Input(name, **kwargs):
            return types.SimpleNamespace(id=name, **kwargs)

        @staticmethod
        def Output(**kwargs):
            return types.SimpleNamespace(**kwargs)

    io = types.SimpleNamespace(ComfyNode=object, Custom=lambda _: ComfyType,
                               Schema=lambda **kwargs: types.SimpleNamespace(**kwargs),
                               NodeOutput=lambda *args, **kwargs: types.SimpleNamespace(args=args, **kwargs))
    for name in ("String", "Int", "Boolean", "Combo", "Image", "Mask", "Audio", "Video"):
        setattr(io, name, ComfyType)
    latest = types.ModuleType("comfy_api.latest")
    latest.io = io
    spec = importlib.util.spec_from_file_location("easyapi_comfy._nodes_test", Path(__file__).parents[1] / "easyapi_comfy" / "nodes.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"comfy_api": types.ModuleType("comfy_api"), "comfy_api.latest": latest}):
        spec.loader.exec_module(module)
    return module


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.nodes = load_nodes()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "image.png"
        self.path.write_bytes(b"image-bytes")
        self.source = MediaSource("image", str(self.path), "first_frame", "image/png")
        self.client = types.SimpleNamespace(upload_media=AsyncMock(return_value={"url": "https://asset.invalid/ref.png"}))

    def tearDown(self):
        self.temp.cleanup()

    async def prepare(self, operation, sources=None, params=None, body=None, prompt="画一棵树"):
        return await self.nodes.prepare_request(self.client, get_protocol(operation), "test-model", prompt,
                                                params or {}, sources or [], body)

    async def test_inline_families_do_not_require_asset_service(self):
        for operation in ("image-generation", "image-task", "mj-imagine", "chat", "responses", "messages", "gemini-generate"):
            with self.subTest(operation=operation):
                path, body, files, query = await self.prepare(operation, [self.source])
                self.assertIn("aW1hZ2UtYnl0ZXM=", json.dumps(body))
                self.assertEqual(files, [])
        self.client.upload_media.assert_not_awaited()

    async def test_native_complete_envelope_preserves_user_fields(self):
        original = {"content": [{"type": "text", "text": "native"}], "parameters": {"seed": 7}, "prompt": "original", "model": "old"}
        _, body, _, _ = await self.prepare("native-seedance", body=json.dumps(original))
        self.assertEqual(body, {**original, "model": "test-model"})
        self.assertEqual(original["model"], "old")

    async def test_native_families_use_their_own_media_envelopes(self):
        _, kling, _, _ = await self.prepare("native-kling-image", [self.source])
        self.assertEqual(kling["image"], "aW1hZ2UtYnl0ZXM=")
        self.assertNotIn("input_references", kling)
        _, seedance, _, _ = await self.prepare("native-seedance", [self.source])
        self.assertEqual(seedance["content"][-1]["type"], "image_url")
        self.assertEqual(seedance["content"][-1]["role"], "first_frame")
        _, jimeng, _, _ = await self.prepare("native-jimeng", [self.source])
        self.assertIn("binary_data_base64", jimeng)
        _, dash, _, _ = await self.prepare("native-dashscope", [self.source])
        self.assertEqual(dash["input"]["img_url"], "https://asset.invalid/ref.png")
        self.assertNotIn("input_references", dash)

    async def test_unified_video_multipart_links_multiple_files_and_roles(self):
        last = MediaSource("image", str(self.path), "last_frame", "image/png")
        _, body, files, _ = await self.prepare("video-generation", [self.source, last])
        self.assertEqual([f.field for f in files], ["input_references[0]", "input_references[1]"])
        self.assertEqual(body["input_references"], [{"file_index": 0, "type": "image", "role": "first_frame"},
                                                     {"file_index": 1, "type": "image", "role": "last_frame"}])
        self.client.upload_media.assert_not_awaited()

    async def test_url_media_does_not_upload_missing_path(self):
        source = MediaSource("image", url="https://asset.invalid/existing.png", role="first_frame")
        _, chat, _, _ = await self.prepare("chat", [source])
        self.assertEqual(chat["messages"][0]["content"][-1]["image_url"]["url"], source.url)
        _, video, _, _ = await self.prepare("video-generations", [source])
        self.assertEqual(video["input_references"][0]["url"], source.url)
        _, unified_video, files, _ = await self.prepare("video-generation", [source])
        self.assertEqual(files, [])
        self.assertEqual(unified_video["input_references"], [{"url": source.url, "type": "image", "role": "first_frame"}])
        self.client.upload_media.assert_not_awaited()

    async def test_native_query_payload_is_distinct_from_url_query(self):
        original = {"query": "actual rerank text", "documents": ["first", "second"], "__query": {"version": "test"}}
        _, body, _, query = await self.prepare("rerank", body=json.dumps(original))
        self.assertEqual(body["query"], "actual rerank text")
        self.assertEqual(body["documents"], original["documents"])
        self.assertEqual(query, {"version": "test"})
        self.assertNotIn("__query", body)

    async def test_limits_apply_before_loading_large_inline_media(self):
        with patch.object(self.nodes, "MAX_MEDIA_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "100 MiB"):
                await self.prepare("mj-imagine", [self.source])
        with patch.object(self.nodes, "MAX_REQUEST_BYTES", 40):
            with self.assertRaisesRegex(ValueError, "100 MiB"):
                await self.prepare("image-generation", [self.source])

    async def test_custom_native_path_is_limited_to_user_creative_routes(self):
        protocol = get_protocol("custom-user-request")
        path, body, _, _ = await self.nodes.prepare_request(self.client, protocol, "", "ignored", {}, [],
            json.dumps({"__path": "/new-native/mj/submit/imagine", "prompt": "native"}))
        self.assertEqual(path, "/new-native/mj/submit/imagine")
        self.assertEqual(body, {"prompt": "native"})
        with self.assertRaises(ValueError):
            await self.nodes.prepare_request(self.client, get_protocol("custom-user-request"), "", "", {}, [],
                json.dumps({"__path": "/api/channel", "secret": "never-sent"}))

    async def test_custom_native_media_is_mapped_explicitly_or_rejected(self):
        with self.assertRaisesRegex(ValueError, "__media_fields"):
            await self.prepare("custom-user-request", [self.source], body=json.dumps({"__path": "/v1/new-creative"}))
        _, body, _, _ = await self.prepare("custom-user-request", [self.source], body=json.dumps({
            "__path": "/v1/new-creative", "__media_fields": {"image": "input.image_url"}, "prompt": "keep native"}))
        self.assertTrue(body["input"]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(body["prompt"], "keep native")
        _, body, _, _ = await self.prepare("custom-user-request", [self.source], body=json.dumps({"__path": "/v1/images/generations"}))
        self.assertTrue(body["images"][0].startswith("data:image/png;base64,"))
        self.client.upload_media.assert_not_awaited()

    async def test_catalog_is_exposed_by_native_node_and_generation_is_effectful(self):
        schema = self.nodes.EasyAPINativeRequest.define_schema()
        operations = next(i.options for i in schema.inputs if i.id == "operation")
        self.assertIn("custom-user-request", operations)
        self.assertTrue(schema.not_idempotent)
        self.assertEqual({"profile_id", "model", "params_json"} - {i.id for i in schema.inputs}, set())

    async def test_model_schema_checks_types_required_files_counts_and_roles(self):
        schema = {"type": "object", "properties": {"duration": {"type": "integer", "maximum": 10},
                  "image": {"type": "string"}, "input_references": {"type": "array", "maxItems": 2,
                  "items": {"type": "object", "properties": {"role": {"enum": ["first_frame", "last_frame"]}}}}},
                  "required": ["duration", "image"], "additionalProperties": True}
        self.client.model_schema = AsyncMock(return_value={"declared": True, "request_schema": schema})
        file = self.nodes.UploadFile("image", self.path)
        await self.nodes.validate_request(self.client, "test", get_protocol("image-edit"), {"duration": 4, "provider_option": True}, [file])
        for body in ({"duration": "4"}, {"duration": 11}, {"duration": 4, "input_references": [{"role": "unknown"}]},
                     {"duration": 4, "input_references": [{}, {}, {}]}):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "模型参数"):
                await self.nodes.validate_request(self.client, "test", get_protocol("image-edit"), body, [file])
        with self.assertRaisesRegex(ValueError, "image"):
            await self.nodes.validate_request(self.client, "test", get_protocol("image-edit"), {"duration": 4}, [])

    async def test_undeclared_schema_does_not_block_advanced_provider_fields(self):
        self.client.model_schema = AsyncMock(return_value={"declared": False, "request_schema": {}})
        await self.nodes.validate_request(self.client, "unknown", get_protocol("custom-user-request"), {"new_provider_field": {"anything": 1}}, [])

    async def test_required_voice_uses_displayed_model_default_without_overwriting_explicit_values(self):
        schema = {"type": "object", "required": ["input", "voice", "watermark", "speed"], "properties": {
            "input": {"type": "string"}, "voice": {"type": "string", "default": "longanlingxin"},
            "watermark": {"type": "boolean", "default": True}, "speed": {"type": "number", "default": 1}}}
        self.client.model_schema = AsyncMock(return_value={"declared": True, "matched": True, "request_schema": schema})
        body = {"input": "hello", "watermark": False, "speed": 0}
        await self.nodes.validate_request(self.client, "tts", get_protocol("audio-speech"), body, [], apply_defaults=True)
        self.assertEqual(body, {"input": "hello", "voice": "longanlingxin", "watermark": False, "speed": 0})
        with self.assertRaisesRegex(ValueError, "voice"):
            await self.nodes.validate_request(self.client, "tts", get_protocol("audio-speech"), {"input": "native"}, [])

    async def test_oversized_unified_video_switches_to_asset_references(self):
        last = MediaSource("image", str(self.path), "last_frame", "image/png")
        with patch.object(self.nodes, "MAX_REQUEST_BYTES", 500):
            _, body, files, _ = await self.prepare("video-generation", [self.source, last])
        self.assertEqual(files, [])
        self.assertEqual(len(body["input_references"]), 2)
        self.assertEqual([ref["role"] for ref in body["input_references"]], ["first_frame", "last_frame"])
        self.assertEqual(self.client.upload_media.await_count, 2)

    async def test_unsupported_typed_inputs_fail_before_request_instead_of_disappearing(self):
        with self.assertRaisesRegex(ValueError, "不支持已连接"):
            await self.prepare("embeddings", [self.source])
        with self.assertRaisesRegex(ValueError, "不支持已连接"):
            await self.prepare("suno-action", [MediaSource("audio", str(self.path))], params={"action": "music"})

    async def test_json_tools_preserve_input_and_handle_nested_lists(self):
        original = {"a": [{"value": 1}]}
        result = self.nodes.EasyAPIJSON.execute(operation="set", value=original, path="a.0.value", other=2)
        self.assertEqual(result.args[0], {"a": [{"value": 2}]})
        self.assertEqual(original["a"][0]["value"], 1)

    async def test_preview_keeps_image_and_audio_separate_for_nodes_two(self):
        latest = types.ModuleType("comfy_api.latest")
        latest.ui = types.SimpleNamespace(
            PreviewImage=lambda image: types.SimpleNamespace(as_dict=lambda: {
                "images": [{"filename": "frame.png", "subfolder": "test folder", "type": "temp"}]}),
            PreviewAudio=lambda audio: types.SimpleNamespace(as_dict=lambda: {
                "audio": [{"filename": "speech.flac", "subfolder": "", "type": "temp"}]}))
        folder_paths = types.ModuleType("folder_paths")
        with patch.dict("sys.modules", {"comfy_api.latest": latest, "folder_paths": folder_paths}), \
                patch.object(self.nodes, "get_runtime", return_value=types.SimpleNamespace(output_dir=Path(self.temp.name))), \
                patch.object(self.nodes.media, "encode_inputs", new=AsyncMock(return_value=[])):
            result = await self.nodes.EasyAPIPreview.execute(image=object(), audio={"waveform": object(), "sample_rate": 24000})
        self.assertNotIn("images", result.ui)
        self.assertNotIn("audio", result.ui)
        self.assertNotIn("animated", result.ui)
        self.assertEqual([item["kind"] for item in result.ui["easyapi_media"]], ["image", "audio"])
        self.assertIn("filename=frame.png", result.ui["easyapi_media"][0]["url"])
        self.assertIn("subfolder=test+folder", result.ui["easyapi_media"][0]["url"])
        self.assertIn("filename=speech.flac", result.ui["easyapi_media"][1]["url"])


if __name__ == "__main__":
    unittest.main()
