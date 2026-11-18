"""Real transports and codecs talking only to the bundled offline gateway."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestServer

from easyapi_comfy.client import EasyAPIClient, UploadFile
from easyapi_comfy.media import decode_response
from easyapi_comfy.protocols import get_protocol
from easyapi_comfy.tasks import TaskStore, create_task_handle, wait_for_task
from tools.mock_gateway import create_app


class GatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.server = TestServer(create_app())
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "mock-only-key")
        self.store = TaskStore(self.directory)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.temp.cleanup()

    async def test_all_async_protocol_families_resume_and_decode_original_media(self):
        import av
        operations = ["image-task", "video-generation", "video-generations", "native-kling-text",
                      "native-kling-image", "native-seedance", "native-dashscope", "native-jimeng",
                      "mj-imagine", "suno-action", "tool-motion-video", "mj-mode-imagine"]
        for operation in operations:
            with self.subTest(operation=operation):
                protocol = get_protocol(operation)
                protocol["route_params"] = {"mode": "fast"}
                path = protocol["path"].replace("{action}", "generate").replace("{mode}", "fast")
                body = {"model": "mock-video", "prompt": "fixture"}
                if operation == "tool-motion-video":
                    body.update(image=str(self.server.make_url("/fixtures/image.png")),
                                video=str(self.server.make_url("/fixtures/video.mp4")))
                submitted = await self.client.request("POST", path, body=body, query=protocol.get("query"))
                handle = create_task_handle(submitted, "offline", protocol)
                self.assertIsNotNone(handle)
                self.store.save(handle)
                reopened = TaskStore(self.directory)
                response = await wait_for_task(self.client, reopened, reopened.get(handle["task_id"]), timeout=3, interval=.001)
                decoded = await decode_response(response, self.client, self.directory / "out")
                self.assertTrue(decoded["files"], operation)
                self.assertTrue(all(Path(p).is_file() for p in decoded["files"]))
                for file in decoded["files"]:
                    if Path(file).suffix == ".mp4":
                        with av.open(file) as video:
                            self.assertGreater(sum(1 for _ in video.decode(video=0)), 0)

    async def test_permission_catalog_schema_and_media_lifecycle(self):
        rows = await self.client.discover_models("image")
        self.assertIn("mock-image", [row["id"] for row in rows])
        schema = await self.client.model_schema("mock-image", "image-edit")
        self.assertTrue(schema["declared"])
        self.assertIn("seed", schema["request_schema"]["properties"])
        local = self.directory / "input.png"
        local.write_bytes(b"local-file-fixture")
        asset = await self.client.upload_media(local, kind="image")
        self.assertEqual(asset["size"], local.stat().st_size)
        fetched = await self.client.request("GET", "/v1/media/" + asset["id"])
        self.assertEqual(fetched.data["id"], asset["id"])
        deleted = await self.client.request("DELETE", "/v1/media/" + asset["id"])
        self.assertTrue(deleted.data["deleted"])

    async def test_video_common_form_remains_available_for_incompatible_inferred_variant(self):
        schema = await self.client.model_schema("mock-video-frames", "video-generation",
                                                variant="reference-to-video")
        self.assertIn("resolution", schema["request_schema"]["properties"])
        self.assertIn("last_frame", schema["video"]["roles"])
        self.assertNotIn("reference_audio", schema["video"]["roles"])
        self.assertIsNone(schema["selected_variant"])

    async def test_missing_model_schema_has_protocol_fields_for_the_ui_only(self):
        from unittest.mock import AsyncMock, patch

        with patch.object(self.client, "_metadata", new=AsyncMock(return_value=None)):
            schema = await self.client.model_schema("private-chat", "chat")
        self.assertFalse(schema["declared"])
        self.assertEqual(schema["request_schema"]["properties"], {})
        self.assertEqual(schema["form_schema"]["properties"]["temperature"]["type"], "number")
        self.assertEqual(schema["form_schema"]["properties"]["stream"]["type"], "boolean")

    async def test_speech_response_and_streamed_text_decode(self):
        response = await self.client.request("POST", "/v1/audio/speech", body={"model": "mock-audio", "input": "hello", "response_format": "wav"})
        decoded = await decode_response(response, self.client, self.directory / "out")
        self.assertGreater(decoded["audio"]["waveform"].shape[-1], 0)
        stream = await self.client.request("POST", "/v1/chat/completions", body={"model": "mock-text", "messages": [], "stream": True}, stream=True)
        text = await decode_response(stream, self.client, self.directory / "out")
        self.assertTrue(text["text"])
