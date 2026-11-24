"""The dedicated image node selects sync routes from real image inputs."""
import base64
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from easyapi_comfy.client import EasyAPIClient
from easyapi_comfy.media import MediaSource
from test_realtime_nodes import load_nodes


class ImageNodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.nodes = load_nodes()
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        content = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(content, format="PNG")
        self.png = content.getvalue()
        self.local = self.directory / "reference.png"
        self.local.write_bytes(self.png)
        self.posts, self.downloads = [], []
        self.response = None
        app = web.Application()

        async def fixture(request):
            self.downloads.append((request.path, request.headers.get("Authorization")))
            return web.Response(body=self.png, content_type="image/png")

        async def generate(request):
            body, files = {}, []
            if request.content_type.startswith("multipart/"):
                reader = await request.multipart()
                async for part in reader:
                    if part.filename:
                        files.append((part.name, await part.read(), part.headers.get("Content-Type")))
                    else:
                        body[part.name] = await part.text()
            else:
                body = await request.json()
            self.posts.append((request.path, body, files))
            data = self.response if self.response is not None else {
                "data": [{"url": str(self.server.make_url("/result-1.png"))},
                         {"url": str(self.server.make_url("/result-2.png"))}], "usage": {"images": 2}}
            return web.json_response(data)

        app.router.add_get("/{name}.png", fixture)
        app.router.add_post("/v1/images/generations", generate)
        app.router.add_post("/v1/images/edits", generate)
        app.router.add_post("/v1/images/tasks", generate)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "offline-image-key")
        self.client.model_schema = AsyncMock(return_value={"declared": False})
        self.runtime = types.SimpleNamespace(client=lambda _: self.client,
                                             input_dir=self.directory / "input", output_dir=self.directory / "output")
        self.patches = [patch.object(self.nodes, "get_runtime", return_value=self.runtime),
                        patch("easyapi_comfy.runtime.get_runtime", return_value=self.runtime),
                        patch.object(self.nodes, "_interrupt")]
        for replacement in self.patches:
            replacement.start()

    async def asyncTearDown(self):
        for replacement in reversed(self.patches):
            replacement.stop()
        await self.client.close()
        await self.server.close()
        self.temp.cleanup()

    async def execute(self, **kwargs):
        return await self.nodes.EasyAPIImages.execute(model="offline-image", prompt="draw", **kwargs)

    def test_schema_exposes_only_image_inputs_and_three_named_outputs(self):
        schema = self.nodes.EasyAPIImages.define_schema()
        self.assertEqual([entry.id for entry in schema.inputs],
                         ["profile_id", "model", "prompt", "params_json", "image", "mask", "references"])
        self.assertEqual([(entry.id, entry.display_name) for entry in schema.outputs],
                         [("image", "image"), ("url", "url"), ("response", "响应")])
        self.assertTrue(schema.not_idempotent)

    async def test_no_reference_posts_sync_generation_and_keeps_all_urls_in_response(self):
        output = await self.execute(params_json=json.dumps({"seed": 0, "watermark": False, "n": 2, "stream": False}))
        path, body, files = self.posts[0]
        self.assertEqual(path, "/v1/images/generations")
        self.assertEqual(body, {"model": "offline-image", "prompt": "draw", "seed": 0, "watermark": False, "n": 2})
        self.assertEqual(files, [])
        self.assertEqual(len(output.args), 3)
        self.assertEqual(tuple(output.args[0].shape), (2, 3, 4, 3))
        self.assertEqual(output.args[1], str(self.server.make_url("/result-1.png")))
        self.assertEqual(len(output.args[2]["data"]), 2)
        self.assertEqual(output.args[2]["usage"], {"images": 2})
        self.assertTrue(all(auth is None for _, auth in self.downloads))

    async def test_model_parameters_and_custom_size_reach_both_sync_routes(self):
        import copy
        import torch
        metadata = {"declared": True, "matched": True, "request_schema": {
            "type": "object", "required": ["quality"], "properties": {
                "size": {"type": "string", "enum": ["auto"], "x-size-spec": {
                    "allow_custom": True, "multiple_of": 16, "max_pixels": 4194304}},
                "quality": {"type": "string", "enum": ["standard", "high"], "default": "standard"},
                "n": {"type": "integer", "minimum": 1, "maximum": 4},
                "seed": {"type": "integer", "minimum": 0}, "watermark": {"type": "boolean"},
            },
        }}
        original = copy.deepcopy(metadata)
        self.client.model_schema.return_value = metadata
        for edit in (False, True):
            with self.subTest(edit=edit):
                await self.execute(image=torch.zeros((1, 3, 4, 3)) if edit else None,
                                   params_json={"size": "1536*1024", "n": 2, "seed": 0, "watermark": False})
                path, body, files = self.posts[-1]
                self.assertEqual(path, "/v1/images/edits" if edit else "/v1/images/generations")
                self.assertEqual(body["size"], "1536x1024")
                self.assertEqual(body["quality"], "standard")
                self.assertEqual(body["seed"], "0" if edit else 0)
                self.assertEqual(body["watermark"], "false" if edit else False)
                self.assertEqual(body["n"], "2" if edit else 2)
                self.assertEqual(bool(files), edit)
                self.assertEqual(metadata, original)

    async def test_tensor_and_mask_post_actual_image_files_to_sync_edits(self):
        import torch
        await self.execute(image=torch.zeros((1, 3, 4, 3)), mask=torch.zeros((1, 3, 4)),
                           params_json={"seed": 0, "watermark": False, "size": "1024x1024", "negative_prompt": "blur"})
        path, body, files = self.posts[0]
        self.assertEqual(path, "/v1/images/edits")
        self.assertEqual({key: body[key] for key in ("seed", "watermark", "size", "negative_prompt")},
                         {"seed": "0", "watermark": "false", "size": "1024x1024", "negative_prompt": "blur"})
        self.assertEqual([field for field, _, _ in files], ["image", "mask"])
        for _, data, mime in files:
            with Image.open(io.BytesIO(data)) as actual:
                self.assertEqual(actual.size, (4, 3))
            self.assertEqual(mime, "image/png")

    async def test_local_and_url_reference_groups_are_merged_as_edit_files(self):
        references = [MediaSource("image", path=self.local),
                      {"kind": "image", "url": str(self.server.make_url("/reference.png"))}]
        await self.execute(references=references)
        self.assertEqual(self.posts[0][0], "/v1/images/edits")
        self.assertEqual([(field, data) for field, data, _ in self.posts[0][2]], [("image", self.png), ("image", self.png)])
        self.assertIn(("/reference.png", None), self.downloads)

    async def test_advanced_image_parameters_select_edits_and_merge_with_connected_images(self):
        inline = "data:image/png;base64," + base64.b64encode(self.png).decode()
        for params in ({"image": str(self.server.make_url("/reference.png"))},
                       {"images": [{"image_url": {"url": inline}}, str(self.local)]},
                       {"input_references": [{"type": "image", "url": str(self.server.make_url("/reference.png"))}]}):
            with self.subTest(params=list(params)):
                self.posts.clear()
                await self.execute(references=[MediaSource("image", path=self.local)], params_json=params)
                _, body, files = self.posts[0]
                self.assertEqual(self.posts[0][0], "/v1/images/edits")
                self.assertTrue(all(key not in body for key in ("image", "images", "input_references")))
                self.assertEqual(len(files), 1 + (2 if "images" in params else 1))
                self.assertTrue(all(field == "image" and data == self.png for field, data, _ in files))

    async def test_legacy_sync_labels_follow_actual_materials_and_async_settings_are_rejected(self):
        await self.execute(operation="image-edit", wait_for_result=True, timeout=60)
        self.assertEqual(self.posts[-1][0], "/v1/images/generations")
        await self.execute(operation="image-generation", references=[MediaSource("image", path=self.local)])
        self.assertEqual(self.posts[-1][0], "/v1/images/edits")
        for old in ({"operation": "image-task"}, {"operation": "image-task-edit"}, {"wait_for_result": False},
                    {"audio": {}}, {"video": "old.mp4"}, {"operation": "custom-user-request"}):
            with self.subTest(old=old):
                self.posts.clear()
                with self.assertRaises(ValueError):
                    await self.execute(**old)
                self.assertEqual(self.posts, [])

    async def test_invalid_or_non_image_inputs_fail_before_generation(self):
        import torch
        cases = [{"mask": torch.zeros((1, 3, 4))},
                 {"references": [MediaSource("audio", url="https://invalid.example/audio.wav")]},
                 {"params_json": {"input_references": [{"type": "video", "url": "https://invalid.example/video.mp4"}]}},
                 {"params_json": {"image": "data:text/plain;base64,aGVsbG8="}},
                 {"params_json": {"image": "data:image/png;base64,"}},
                 {"params_json": {"image": "data:image/png;base64,bm90LWFuLWltYWdl"}},
                 {"params_json": {"stream": True}}, {"params_json": {"stream": "true"}},
                 {"params_json": {"async": True}}, {"params_json": {"__path": "/v1/images/tasks"}},
                 {"params_json": {"image_url": "https://invalid.example/image.png"}}]
        for inputs in cases:
            with self.subTest(inputs=list(inputs)):
                self.posts.clear()
                with self.assertRaises(ValueError):
                    await self.execute(**inputs)
                self.assertEqual(self.posts, [])

    async def test_inline_only_response_has_no_invented_url_and_missing_images_are_errors(self):
        self.response = {"data": [{"b64_json": base64.b64encode(self.png).decode()}]}
        output = await self.execute()
        self.assertEqual(output.args[1], "")
        self.assertEqual(output.args[2], self.response)
        self.response = {"data": {"task_id": "unsupported-async-task", "status": "pending"}}
        with self.assertRaisesRegex(ValueError, "同步"):
            await self.execute()
        self.assertTrue(all(path != "/v1/images/tasks" for path, _, _ in self.posts))

    async def test_relative_result_url_uses_the_same_gateway_base_as_download(self):
        self.response = {"data": [{"url": "/result-1.png"}]}
        output = await self.execute()
        self.assertEqual(tuple(output.args[0].shape), (1, 3, 4, 3))
        self.assertEqual(output.args[1], self.client.base_url + "/result-1.png")
        self.assertEqual(output.args[2], self.response)

    async def test_inline_input_limit_rejects_before_post(self):
        inline = "data:image/png;base64," + base64.b64encode(self.png).decode()
        with patch.object(self.nodes, "MAX_MEDIA_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "100 MiB"):
                await self.execute(params_json={"image": inline})
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
