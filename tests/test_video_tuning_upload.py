"""Management-only streamed tuning uploads never use client-controlled file paths."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes
from tools.mock_gateway import video_bytes


class VideoTuningUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.dict(os.environ, {"EASYAPI_PUBLIC_ORIGIN": ""}))
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.runtime = Runtime(directory)
        self.addAsyncCleanup(self.runtime.close)
        app = web.Application()
        app.add_routes(build_routes(self.runtime))
        self.browser = await self.enterAsyncContext(TestClient(TestServer(app)))
        session = await (await self.browser.get("/easyapi/bootstrap")).json()
        self.headers = {"Origin": str(self.browser.make_url("/")).rstrip("/"),
                        "X-EasyAPI-Management": session["management_nonce"]}
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(buffer, "PNG")
        self.png = buffer.getvalue()

    def form(self, raw, kind="image", filename="reference.png", content_type="image/png", kind_first=True):
        form = FormData(quote_fields=False)
        if kind_first:
            form.add_field("kind", kind)
        form.add_field("file", raw, filename=filename, content_type=content_type)
        if not kind_first:
            form.add_field("kind", kind)
        return form

    def saved(self):
        return list(self.runtime.input_dir.iterdir()) if self.runtime.input_dir.exists() else []

    async def test_nonce_and_origin_required_before_writing_any_file(self):
        for headers in ({}, {"Origin": self.headers["Origin"]}, self.headers | {"Origin": "https://other.invalid"}):
            response = await self.browser.post("/easyapi/video-tuning/media", headers=headers, data=self.form(self.png))
            self.assertEqual(response.status, 403)
            self.assertEqual(self.saved(), [])

    async def test_image_upload_returns_local_descriptor_and_safe_random_filename(self):
        response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers,
                                           data=self.form(self.png, filename="../../private\\reference.png"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        record = (await response.json())["media"]
        self.assertEqual(record["kind"], "image")
        self.assertEqual(record["role"], "analysis_image")
        self.assertEqual(record["type"], "easyapi_input")
        self.assertTrue(record["analysisOnly"] and record["ready"])
        self.assertRegex(record["filename"], r"^[a-f0-9]{32}\.png$")
        self.assertEqual(record["name"], "reference.png")
        self.assertNotIn("path", record)
        self.assertNotIn(str(self.runtime.input_dir), str(record))
        self.assertEqual((self.runtime.input_dir / record["filename"]).read_bytes(), self.png)

    async def test_video_upload_accepts_kind_after_file_without_losing_original_bytes(self):
        raw = video_bytes()
        response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers,
                                           data=self.form(raw, "video", "clip.mp4", "video/mp4", kind_first=False))
        self.assertEqual(response.status, 200)
        record = (await response.json())["media"]
        self.assertEqual(record["role"], "result_video")
        self.assertEqual(record["mime"], "video/mp4")
        self.assertRegex(record["filename"], r"^[a-f0-9]{32}\.mp4$")
        self.assertEqual((self.runtime.input_dir / record["filename"]).read_bytes(), raw)

    async def test_oversized_image_and_video_remove_only_the_current_upload(self):
        self.runtime.input_dir.mkdir(parents=True)
        existing = self.runtime.input_dir / "existing.png"
        existing.write_bytes(self.png)
        cases = [("image", True), ("image", False), ("video", True)]
        for kind, kind_first in cases:
            with self.subTest(kind=kind, kind_first=kind_first), \
                    patch("easyapi_comfy.server._TUNING_IMAGE_UPLOAD_LIMIT", 32), \
                    patch("easyapi_comfy.server._TUNING_VIDEO_UPLOAD_LIMIT", 64):
                raw = b"x" * (40 if kind == "image" else 80)
                response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers,
                                                   data=self.form(raw, kind, "clip.png" if kind == "image" else "clip.mp4",
                                                                  "image/png" if kind == "image" else "video/mp4", kind_first))
                self.assertEqual(response.status, 413)
                self.assertEqual(self.saved(), [existing])
                self.assertEqual(existing.read_bytes(), self.png)

    async def test_invalid_kind_mime_empty_or_extra_file_cleans_temporary_file(self):
        for form in (self.form(self.png, "audio"), self.form(self.png, "video"), self.form(b"")):
            response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers, data=form)
            self.assertEqual(response.status, 400)
            self.assertEqual(self.saved(), [])
        form = self.form(self.png)
        form.add_field("file", self.png, filename="second.png", content_type="image/png")
        response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers, data=form)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.saved(), [])
        response = await self.browser.post("/easyapi/video-tuning/media", headers=self.headers, json={"path": "../../private"})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.saved(), [])


if __name__ == "__main__":
    unittest.main()
