"""Model parameters come from the selected key's document contract."""
import asyncio
import copy
import unittest
from urllib.parse import parse_qs, quote, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestServer

from easyapi_comfy.client import EasyAPIClient, EasyAPIError


def image_docs(model="hidden-image", *, empty=False):
    fields = {} if empty else {
        "model": {"type": "string"}, "prompt": {"type": "string"},
        "size": {"type": "string", "enum": ["auto"], "x-size-spec": {"allow_custom": True}},
        "n": {"type": "integer", "minimum": 1, "maximum": 4},
        "quality": {"type": "string", "enum": ["standard", "high"]},
        "seed": {"type": "integer", "default": 0},
        "watermark": {"type": "boolean", "default": False},
        "negative_prompt": {"type": "string"}, "prompt_extend": {"type": "boolean"},
        "prompt_extend_mode": {"type": "string"}, "enable_thinking": {"type": "boolean"},
        "output_format": {"type": "string", "enum": ["png", "jpeg"]},
        "background": {"type": "string"}, "style": {"type": "string"},
        "guidance_scale": {"type": "number"}, "steps": {"type": "integer"},
    }
    edit = copy.deepcopy(fields)
    if not empty:
        edit["size"]["enum"] = ["512x512", "1024x1024"]
        edit["image"] = {"type": "string", "format": "binary"}
    return {"model": model, "category": "image", "docs_version": "model-docs/2", "endpoints": [
        {"key": "image-generation", "path": "/v1/images/generations", "method": "POST",
         "transport": "json", "request_schema": {"type": "object", "properties": fields}},
        {"key": "image-edit", "path": "/v1/images/edits", "method": "POST", "transport": "multipart",
         "request_schema": {"type": "object", "properties": edit, "required": [] if empty else ["image"]}},
    ]}


class AuthenticatedModelDocsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.docs = {"hidden-image": image_docs()}
        self.public_docs = {}
        self.status = 200
        self.calls = []
        self.allowed = [{"id": "hidden-image"}]
        self.redirect_url = None
        self.hold = None
        self.started = asyncio.Event()
        app = web.Application()

        async def authorized(request):
            model = request.match_info.get("model") or request.query.get("model_name")
            self.calls.append(("authenticated", model,
                               request.headers.get("Accept-Language"), bool(request.headers.get("Authorization")),
                               request.raw_path))
            data = copy.deepcopy(self.docs.get(model, {}))
            hold = self.hold
            self.started.set()
            if hold is not None:
                await hold.wait()
            if self.redirect_url:
                return web.Response(status=302, headers={"Location": self.redirect_url})
            if self.status != 200:
                return web.json_response({"success": False, "message": "metadata rejected"}, status=self.status)
            return web.json_response({"success": True, "data": data})

        async def models(request):
            self.calls.append(("allowed", None, request.headers.get("Accept-Language"),
                               bool(request.headers.get("Authorization")), request.raw_path))
            return web.json_response({"data": copy.deepcopy(self.allowed)})

        async def public(request):
            self.calls.append((request.path, request.query.get("model_name"),
                               request.headers.get("Accept-Language"), bool(request.headers.get("Authorization")),
                               request.raw_path))
            if request.path == "/pricing/docs" and request.query.get("model_name") in self.public_docs:
                return web.json_response({"success": True, "data": copy.deepcopy(self.public_docs[request.query["model_name"]])})
            return web.json_response({"success": False}, status=404)

        app.router.add_get("/v1/models/{model:.+}/docs", authorized)
        app.router.add_get("/v1/model-docs", authorized)
        app.router.add_get("/v1/models", models)
        for path in ("/pricing", "/pricing/docs", "/pricing/schema", "/pricing/detail"):
            app.router.add_get(path, public)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "loopback-document-fixture")

    async def asyncTearDown(self):
        if self.hold is not None:
            self.hold.set()
        await self.client.close()
        await self.server.close()

    async def test_private_sixteen_fields_and_endpoint_overrides_do_not_need_public_metadata(self):
        generated = await self.client.model_schema("hidden-image", "image-generation", locale="en")
        edited = await self.client.model_schema("hidden-image", "image-edit", locale="en")
        self.assertEqual(len(generated["request_schema"]["properties"]), 16)
        self.assertEqual(len(generated["form_schema"]["properties"]), 14)
        self.assertTrue(generated["declared"])
        self.assertTrue(generated["matched"])
        self.assertEqual(generated["source"], "docs")
        self.assertEqual(generated["form_schema"]["properties"]["size"]["enum"], ["auto"])
        self.assertEqual(edited["form_schema"]["properties"]["size"]["enum"], ["512x512", "1024x1024"])
        self.assertEqual(edited["request_schema"]["required"], ["image"])
        self.assertTrue(all(call[0] == "authenticated" and call[3] for call in self.calls))
        self.assertEqual(len(self.calls), 1, "operations share key-scoped model docs")

    async def test_hidden_model_is_classified_for_both_image_operations_within_key_allow_set(self):
        for operation in ("image-generation", "image-edit"):
            with self.subTest(operation=operation):
                rows = await self.client.discover_models("image", operation)
                self.assertEqual([row["id"] for row in rows], ["hidden-image"])
                self.assertEqual(rows[0]["category"], "image")
                self.assertEqual(rows[0]["operations"], ["image-edit", "image-generation"])
                self.assertEqual(rows[0]["capability_status"], "declared")
        self.assertFalse(any(call[0] == "/pricing/docs" for call in self.calls))
        self.allowed = []
        self.assertEqual(await self.client.discover_models("image", "image-edit", refresh=True), [])

    async def test_unauthorized_metadata_clears_cached_schemas_and_never_falls_back_anonymously(self):
        await self.client.model_schema("hidden-image", "image-generation")
        await self.client.discover_models("image", "image-generation")
        for status in (401, 403):
            with self.subTest(status=status):
                self.status = status
                self.calls.clear()
                self.public_docs["hidden-image"] = image_docs()
                with self.assertRaises(EasyAPIError) as caught:
                    await self.client.model_schema("hidden-image", "image-generation", refresh=True)
                self.assertEqual(caught.exception.status, status)
                self.assertIn("authorization", str(caught.exception).lower())
                self.assertTrue(all(call[0] == "authenticated" for call in self.calls))
                self.assertFalse(any(key[0] in {"schema", "authenticated-model-docs", "metadata", "models", "allowed-models"}
                                     for key in self.client._cache))
                with self.assertRaises(EasyAPIError):
                    await self.client.model_schema("hidden-image", "image-generation")

    async def test_unsupported_new_endpoint_preserves_old_public_docs_chain(self):
        for model in ("hidden-image", "owner/private-image"):
            for status in (404, 405, 501):
                with self.subTest(model=model, status=status):
                    self.status = status
                    self.calls.clear()
                    self.public_docs[model] = image_docs(model)
                    result = await self.client.model_schema(model, "image-generation", refresh=True)
                    self.assertEqual(len(result["request_schema"]["properties"]), 16)
                    self.assertEqual(self.calls[0][0], "authenticated")
                    self.assertTrue(self.calls[0][3])
                    self.assertTrue(any(call[0] == "/pricing/docs" for call in self.calls))
                    self.assertFalse(any(call[3] for call in self.calls if call[0].startswith("/pricing")))

    async def test_gateway_and_network_failures_are_loading_errors_without_public_downgrade(self):
        self.public_docs["hidden-image"] = image_docs()
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.status = status
                self.calls.clear()
                with self.assertRaises(EasyAPIError) as caught:
                    await self.client.model_schema("hidden-image", "image-generation", refresh=True)
                self.assertEqual(caught.exception.status, status)
                self.assertIn("metadata", str(caught.exception).lower())
                self.assertTrue(all(call[0] == "authenticated" for call in self.calls))
        await self.server.close()
        with self.assertRaises(EasyAPIError) as caught:
            await self.client.model_schema("hidden-image", "image-generation", refresh=True)
        self.assertIn("metadata", str(caught.exception).lower())

    async def test_refresh_refetches_empty_contract_and_locale_and_client_caches_are_isolated(self):
        self.docs["hidden-image"] = image_docs(empty=True)
        empty = await self.client.model_schema("hidden-image", "image-generation", locale="en")
        self.assertEqual(empty["form_schema"]["properties"], {})
        self.assertEqual([call[0] for call in self.calls], ["authenticated"])
        self.docs["hidden-image"] = image_docs()
        cached = await self.client.model_schema("hidden-image", "image-generation", locale="en")
        self.assertEqual(cached["form_schema"]["properties"], {})
        fresh = await self.client.model_schema("hidden-image", "image-generation", locale="en", refresh=True)
        self.assertEqual(len(fresh["form_schema"]["properties"]), 14)
        fresh["form_schema"]["properties"].clear()
        self.assertEqual(len((await self.client.model_schema("hidden-image", "image-generation", locale="en"))["form_schema"]["properties"]), 14)
        await self.client.model_schema("hidden-image", "image-edit", locale="zh-TW")
        other = EasyAPIClient(str(self.server.make_url("")), "another-loopback-document-fixture")
        try:
            await other.model_schema("hidden-image", "image-generation", locale="en")
        finally:
            await other.close()
        self.assertEqual([call[2] for call in self.calls], ["en", "en", "zh-TW", "en"])

    async def test_model_path_is_encoded_and_redirect_never_receives_credentials(self):
        for name in ("owner/private +image?/% variant", "private +image?% variant"):
            with self.subTest(name=name):
                self.docs[name] = image_docs(name)
                result = await self.client.model_schema(name, "image-generation")
                self.assertEqual(result["model"], name)
                call = self.calls[-1]
                self.assertEqual(call[1], name)
                if "/" in name:
                    self.assertEqual(urlsplit(call[4]).path, "/v1/model-docs")
                    self.assertEqual(parse_qs(urlsplit(call[4]).query), {"model_name": [name]})
                else:
                    self.assertIn(quote(name, safe=""), call[4])
        received = []
        app = web.Application()

        async def redirected(request):
            received.append(bool(request.headers.get("Authorization")))
            return web.json_response({"data": image_docs()})

        app.router.add_get("/docs", redirected)
        destination = TestServer(app)
        await destination.start_server()
        self.redirect_url = str(destination.make_url("/docs"))
        try:
            with self.assertRaises(EasyAPIError):
                await self.client.model_schema("hidden-image", "image-generation", refresh=True)
            self.assertEqual(received, [])
        finally:
            await destination.close()

    async def test_concurrent_operations_share_docs_without_losing_a_forced_refresh(self):
        await asyncio.gather(*(self.client.model_schema("hidden-image", operation)
                               for operation in ("image-generation", "image-edit")))
        self.assertEqual([call[0] for call in self.calls], ["authenticated"])
        self.client._cache.clear()
        self.calls.clear()
        self.started.clear()
        self.docs["hidden-image"] = image_docs(empty=True)
        hold = self.hold = asyncio.Event()
        older = asyncio.create_task(self.client.model_schema("hidden-image", "image-generation"))
        await self.started.wait()
        self.hold = None
        self.docs["hidden-image"] = image_docs()
        try:
            fresh = await self.client.model_schema("hidden-image", "image-generation", refresh=True)
            self.assertEqual(len(fresh["form_schema"]["properties"]), 14)
        finally:
            hold.set()
            await older
        cached = await self.client.model_schema("hidden-image", "image-generation")
        self.assertEqual(len(cached["form_schema"]["properties"]), 14,
                         "an older request must not overwrite refreshed metadata")


if __name__ == "__main__":
    unittest.main()
