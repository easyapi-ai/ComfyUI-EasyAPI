"""Permission-aware discovery and metadata forms, using loopback-only gateways."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from jsonschema import Draft202012Validator

from easyapi_comfy.client import EasyAPIClient, EasyAPIError
from easyapi_comfy.profiles import ProfileStore
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes


def endpoint(key, path, *, transport="json", method="POST", schema=None, variants=None):
    return {"key": key, "path": path, "method": method, "transport": transport,
            "request_schema": schema or {"type": "object", "properties": {"prompt": {"type": "string"}}},
            "variants": variants or []}


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.authorized = True
        self.allowed = [
            {"id": "text-multi", "category": "text", "supported_endpoint_types": ["openai", "audio-speech", "api-video"]},
            {"id": "image-known", "supported_endpoint_types": ["openai", "image-generation", "audio-speech"]},
            {"id": "speech", "category": "audio", "supported_endpoint_types": ["audio-speech", "realtime", "audio-transcriptions"]},
            {"id": "asr", "category": "audio", "supported_endpoint_types": ["audio-speech", "realtime", "audio-transcriptions"]},
            {"id": "realtime", "category": "audio", "supported_endpoint_types": ["audio-speech", "realtime", "audio-transcriptions"]},
            {"id": "private-video", "category": "video"},
            {"id": "unknown"},
        ]
        self.docs = {
            "text-multi": {"category": "text", "endpoints": [endpoint("openai", "/v1/chat/completions"), endpoint("openai-response", "/v1/responses"), endpoint("anthropic", "/v1/messages")]},
            "image-known": {"category": "image", "endpoints": [endpoint("image-generation", "/v1/images/generations"), endpoint("image-edit", "/v1/images/edits", transport="multipart")]},
            "speech": {"category": "audio", "endpoints": [endpoint("audio-speech", "/v1/audio/speech")]},
            "asr": {"category": "audio", "endpoints": [endpoint("audio-transcriptions", "/v1/audio/transcriptions", transport="multipart")]},
            "realtime": {"category": "audio", "endpoints": [endpoint("realtime", "/v1/realtime", method="GET", transport="websocket")]},
        }
        self.schemas = {"image-known": {"model": "image-known", "category": "image", "params_declared": False,
                                        "endpoints": [{"type": "image-generation", "path": "/v1/images/generations", "method": "POST"}],
                                        "request": {"type": "object", "properties": {}}}}
        app = web.Application()

        async def models(request):
            self.calls.append((request.path, dict(request.query), request.headers.get("Accept-Language"), request.headers.get("Authorization")))
            if not self.authorized or request.headers.get("Authorization") != "Bearer mock-private-key":
                return web.json_response({"error": {"message": "invalid mock key"}}, status=401)
            return web.json_response({"data": self.allowed})

        async def metadata(request):
            self.calls.append((request.path, dict(request.query), request.headers.get("Accept-Language"), request.headers.get("Authorization")))
            name = request.query.get("model_name")
            if request.path == "/pricing":
                return web.json_response({"success": True, "data": [{"model_name": "public-only", "category": "Video"}, {"model_name": "image-known", "category": "Image"}]})
            if request.path == "/pricing/docs":
                data = self.docs.get(name)
            elif request.query.get("all") == "1":
                data = {"items": list(self.schemas.values())}
            else:
                data = self.schemas.get(name)
            if data is None:
                return web.json_response({"success": False, "message": "no metadata"}, status=404)
            data = copy.deepcopy(data)
            # A visible locale marker makes accidental cross-locale cache hits fail.
            data["locale_marker"] = request.headers.get("Accept-Language")
            return web.json_response({"success": True, "data": data})

        app.router.add_get("/v1/models", models)
        for path in ("/pricing", "/pricing/docs", "/pricing/schema", "/pricing/detail"):
            app.router.add_get(path, metadata)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "mock-private-key")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_permission_set_never_gains_public_models_and_missing_category_is_enriched(self):
        rows = await self.client.discover_models("image", "image-edit", locale="en")
        self.assertIn("image-known", [row["id"] for row in rows])
        self.assertNotIn("public-only", [row["id"] for row in rows])
        self.assertNotIn("text-multi", [row["id"] for row in rows])
        image = next(row for row in rows if row["id"] == "image-known")
        self.assertEqual(image["category"], "image")
        self.assertEqual(image["operations"], ["image-edit", "image-generation"])
        unknown = next(row for row in rows if row["id"] == "unknown")
        self.assertEqual(unknown["capability_status"], "unknown")
        self.assertIsNone(unknown["operation_supported"])
        self.assertTrue(all(auth is None for path, _, _, auth in self.calls if path.startswith("/pricing")))

    async def test_audio_operations_do_not_mix_channel_union_capabilities(self):
        for operation, expected in (("audio-speech", "speech"), ("audio-transcriptions", "asr"), ("realtime", "realtime")):
            with self.subTest(operation=operation):
                rows = await self.client.discover_models("audio", operation)
                known = {row["id"] for row in rows if row["operation_supported"] is True}
                self.assertEqual(known, {expected})

    async def test_model_docs_override_stale_category_and_explicit_multi_family_endpoints(self):
        self.allowed.append({"id": "mixed", "category": "image"})
        self.docs["mixed"] = {"category": "text", "endpoints": [endpoint("openai", "/v1/chat/completions"), endpoint("image-generation", "/v1/images/generations")]}
        text = await self.client.discover_models("text", "chat")
        images = await self.client.discover_models("image", "image-generation")
        self.assertIn("mixed", [row["id"] for row in text])
        self.assertIn("mixed", [row["id"] for row in images])

    async def test_text_multi_protocol_and_image_async_transport_are_exact(self):
        for operation in ("chat", "responses", "messages"):
            rows = await self.client.discover_models("text", operation)
            self.assertIn("text-multi", [row["id"] for row in rows])
        rows = await self.client.discover_models("text", "responses-compact")
        self.assertNotIn("text-multi", [row["id"] for row in rows])
        # A synchronous edit endpoint alone does not prove async edit support.
        rows = await self.client.discover_models("image", "image-task-edit")
        self.assertNotIn("image-known", [row["id"] for row in rows])
        self.docs["image-known"]["endpoints"].append(endpoint("image-task", "/v1/images/tasks", transport="json"))
        self.docs["image-known"]["endpoints"].append(endpoint("image-task-edit", "/v1/images/tasks", transport="multipart"))
        rows = await self.client.discover_models("image", "image-task-edit", refresh=True)
        self.assertIn("image-known", [row["id"] for row in rows])
        schema = await self.client.model_schema("image-known", "image-task-edit")
        self.assertEqual(schema["schema_operation"], "image-task-edit")

    async def test_empty_and_401_catalogs_never_fall_back_to_public(self):
        self.allowed = []
        self.assertEqual(await self.client.discover_models("video"), [])
        self.assertEqual([path for path, _, _, _ in self.calls], ["/v1/models"])
        self.authorized = False
        with self.assertRaises(EasyAPIError) as error:
            await self.client.discover_models("video", refresh=True)
        self.assertEqual(error.exception.status, 401)
        self.assertTrue(all(path == "/v1/models" for path, _, _, _ in self.calls))

    async def test_rejected_refresh_discards_previous_allowed_scope_for_other_operations(self):
        rows = await self.client.discover_models("text", "chat")
        self.assertIn("text-multi", [row["id"] for row in rows])
        self.authorized = False
        with self.assertRaises(EasyAPIError):
            await self.client.discover_models("text", "responses", refresh=True)
        with self.assertRaises(EasyAPIError) as error:
            await self.client.discover_models("text", "chat")
        self.assertEqual(error.exception.status, 401)

    async def test_variant_and_locale_caches_are_separate_and_video_roles_are_scene_specific(self):
        def scene(key, roles):
            props = {"prompt": {"type": "string"}}
            if roles:
                props["input_references"] = {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string", "enum": roles}}}}
            return {"key": key, "path": "/v1/videos", "method": "POST", "request_schema": {"type": "object", "properties": props}}
        variants = [scene("text-to-video", []), scene("image-to-video", ["first_frame", "last_frame"]), scene("reference-to-video", ["reference_image", "reference_video", "reference_audio"])]
        self.docs["video"] = {"category": "video", "endpoints": [endpoint("api-video", "/v1/videos", variants=variants)], "variants": variants}
        image = await self.client.model_schema("video", "video-generation", locale="en", variant="image-to-video")
        reference = await self.client.model_schema("video", "video-generation", locale="ja", variant="reference-to-video")
        self.assertEqual(image["docs"]["locale_marker"], "en")
        self.assertEqual(reference["docs"]["locale_marker"], "ja")
        self.assertEqual(image["video"]["variants"]["image-to-video"]["roles"], ["first_frame", "last_frame"])
        self.assertEqual(reference["video"]["variants"]["reference-to-video"]["roles"], ["reference_image", "reference_video", "reference_audio"])
        self.assertEqual(image["selected_variant"], "image-to-video")
        self.assertEqual(reference["selected_variant"], "reference-to-video")
        before = len(self.calls)
        image["request_schema"]["properties"].clear()
        cached = await self.client.model_schema("video", "video-generation", locale="en", variant="image-to-video")
        self.assertTrue(cached["request_schema"]["properties"])
        self.assertEqual(len(self.calls), before)

    async def test_evidenced_schema_repairs_are_limited_and_preserve_video_alias_metadata(self):
        self.schemas["seedance-2.0-mini"] = {"category": "video", "request": {"type": "object", "properties": {
            "duration": {"type": "integer", "minimum": 4, "maximum": 15}, "service_tier": {"type": "string", "enum": ["default", "flex"]}}}}
        mini = await self.client.model_schema("seedance-2.0-mini", "video-generation")
        validator = Draft202012Validator(mini["request_schema"])
        self.assertTrue(validator.is_valid({"duration": -1, "service_tier": "default"}))
        self.assertFalse(validator.is_valid({"duration": 1}))
        self.assertFalse(validator.is_valid({"service_tier": "flex"}))
        self.schemas["qwen-audio-3.0-tts-plus"] = {"request": {"type": "object", "properties": {
            "language_hints": {"type": "array", "enum": ["zh", "en"], "items": {"type": "string"}},
            "aigc_propagator": {"type": "string", "default": "阿里云UID"}}}}
        tts = await self.client.model_schema("qwen-audio-3.0-tts-plus", "audio-speech")
        self.assertTrue(Draft202012Validator(tts["request_schema"]).is_valid({"language_hints": ["zh"]}))
        self.assertNotIn("default", tts["request_schema"]["properties"]["aigc_propagator"])
        objects = {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "url": {"type": "string"}}}}
        self.docs["happyhorse-1.1-r2v"] = {"category": "video", "endpoints": [endpoint("api-video", "/v1/videos", schema={"type": "object", "properties": {"images": objects, "prompt": {"type": "string"}}, "required": ["images", "prompt"]}, variants=[{"key": "image-to-video", "path": "/v1/videos", "request_schema": {"type": "object", "properties": {"images": objects}, "required": ["images"]}}])]}
        happy = await self.client.model_schema("happyhorse-1.1-r2v", "video-generation", variant="image-to-video")
        self.assertEqual(happy["request_schema"]["properties"]["images"]["items"]["type"], "string")
        self.assertEqual(happy["video"]["scenarios"], ["image-to-video"])
        self.assertEqual(happy["video"]["variants"]["image-to-video"]["roles"], ["reference_image"])
        self.assertEqual(happy["video"]["variants"]["image-to-video"]["media_field"], "images")
        missing = await self.client.model_schema("no-metadata", "chat")
        self.assertFalse(missing["declared"])
        self.assertEqual(missing["fallback"], "basic-and-advanced-json")


class DefaultProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_default_is_atomic_private_and_never_selects_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            # A legacy store migrates without inventing a default.
            path.write_text(json.dumps({"one": {"id": "one", "name": "One", "base_url": "https://one.invalid", "api_key": "mock-secret"}}))
            store = ProfileStore(path)
            self.assertIsNone(store.default_profile_id())
            self.assertEqual(store.resolve_id(), "one")
            store.save("two", "https://two.invalid", "mock-other")
            with self.assertRaises(ValueError):
                store.resolve_id()
            store.save("two", "https://two.invalid", "mock-other", make_default=True)
            self.assertEqual(ProfileStore(path).get()["id"], "two")
            self.assertNotIn("mock-other", json.dumps(store.list_public()))
            store.delete("two")
            self.assertIsNone(store.default_profile_id())
            self.assertEqual([item["id"] for item in store.list_public()], ["one"])

    async def test_default_mutation_uses_management_nonce_and_models_preserve_401(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            runtime.profiles.save("one", "https://one.invalid", "mock-secret")
            app = web.Application()
            app.add_routes(build_routes(runtime))
            browser = TestClient(TestServer(app))
            await browser.start_server()
            try:
                response = await browser.get("/easyapi/profiles")
                self.assertIsNone((await response.json())["default_profile_id"])
                origin = str(browser.server.make_url("")).rstrip("/")
                self.assertEqual((await browser.post("/easyapi/default-profile", json={"profile_id": "one"}, headers={"Origin": origin})).status, 403)
                bootstrap = await (await browser.get("/easyapi/bootstrap")).json()
                headers = {"Origin": origin, "X-EasyAPI-Management": bootstrap["management_nonce"]}
                selected = await browser.post("/easyapi/default-profile", json={"profile_id": "one"}, headers=headers)
                self.assertEqual((await selected.json())["default_profile_id"], "one")
                self.assertEqual(runtime.client().profile_id, "one")
                saved = await browser.post("/easyapi/profiles/two", json={"base_url": "https://two.invalid", "api_key": "mock-two", "make_default": True}, headers=headers)
                self.assertEqual((await saved.json())["default_profile_id"], "two")
                self.assertEqual(runtime.client().profile_id, "two")
                runtime.profiles.save("empty-key", "https://missing.invalid", "", make_default=True)
                unauthorized = await browser.get("/easyapi/models?operation=chat&family=text&locale=ja")
                self.assertEqual(unauthorized.status, 401)
                self.assertEqual((await unauthorized.json())["code"], "gateway_authentication")
            finally:
                await runtime.close()
                await browser.close()
