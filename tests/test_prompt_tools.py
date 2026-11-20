"""Prompt tools exercise the same-origin routes and a loopback-only gateway."""
import os
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes


class PromptToolRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.dict(os.environ, {"EASYAPI_PUBLIC_ORIGIN": ""}))
        self.completions = []
        self.model_requests = []
        self.reply = {"choices": [{"message": {"content": "refined prompt"}, "finish_reason": "stop"}]}

        async def models(request):
            key = request.headers["Authorization"]
            self.model_requests.append(key)
            if key == "Bearer fixture-other-key":
                return web.json_response({"data": [{"id": "other-chat", "category": "text",
                                                   "supported_endpoint_types": ["openai"]}]})
            self.assertEqual(key, "Bearer fixture-text-key")
            return web.json_response({"data": [
                {"id": "chat", "name": "Fixture Chat", "category": "text", "supported_endpoint_types": ["openai"]},
                {"id": "video", "category": "video", "supported_endpoint_types": ["openai"]},
                {"id": "undocumented"},
            ]})

        async def pricing(request):
            self.assertNotIn("Authorization", request.headers)
            return web.json_response({"data": [{"model_name": "public-only", "category": "text"}]})

        async def chat(request):
            self.completions.append((request.headers["Authorization"], request.headers["Accept-Language"], await request.json()))
            return web.json_response(self.reply)

        gateway_app = web.Application()
        gateway_app.router.add_get("/v1/models", models)
        gateway_app.router.add_get("/pricing", pricing)
        gateway_app.router.add_post("/v1/chat/completions", chat)
        gateway = await self.enterAsyncContext(TestServer(gateway_app))
        directory = self.enterContext(tempfile.TemporaryDirectory())
        runtime = Runtime(directory)
        self.addAsyncCleanup(runtime.close)
        gateway_url = str(gateway.make_url("/")).rstrip("/")
        runtime.profiles.save("writer", gateway_url, "fixture-text-key")
        runtime.profiles.save("other", gateway_url, "fixture-other-key", make_default=True)
        app = web.Application()
        app.add_routes(build_routes(runtime))
        self.browser = await self.enterAsyncContext(TestClient(TestServer(app)))
        self.origin = str(self.browser.make_url("/")).rstrip("/")
        self.value = {"profile_id": "writer", "model": "chat", "action": "optimize",
                      "prompt": "一艘船\n<span:1.2>", "locale": "zh"}

    async def management_headers(self):
        session = await (await self.browser.get("/easyapi/bootstrap")).json()
        return {"Origin": self.origin, "X-EasyAPI-Management": session["management_nonce"]}

    async def test_catalog_and_guards_do_not_submit_generation(self):
        response = await self.browser.get("/easyapi/prompt/models?profile_id=writer&locale=zh")
        self.assertEqual(await response.json(), {"models": [{"id": "chat", "display_name": "Fixture Chat"}]})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.model_requests, ["Bearer fixture-text-key"])
        headers = {"Origin": self.origin}
        denied = await self.browser.post("/easyapi/prompt/transform", headers=headers, json=self.value)
        self.assertEqual(denied.status, 403)
        headers = dict(await self.management_headers(), Origin="https://different.invalid")
        denied = await self.browser.post("/easyapi/prompt/transform", headers=headers, json=self.value)
        self.assertEqual(denied.status, 403)

        headers = await self.management_headers()
        for overrides in ({"model": "public-only"}, {"model": "video"}, {"model": "undocumented"},
                          {"profile_id": ""}, {"profile_id": "other"}, {"prompt": " "},
                          {"action": "translate", "target_language": "invented"},
                          {"prompt": "x" * 12_001}):
            with self.subTest(overrides=tuple(overrides)):
                invalid = await self.browser.post("/easyapi/prompt/transform", headers=headers, json=self.value | overrides)
                self.assertEqual(invalid.status, 400)
        self.assertEqual(self.completions, [])

    async def test_explicit_actions_only_send_prompt_and_return_editable_text(self):
        headers = await self.management_headers()
        response = await self.browser.post("/easyapi/prompt/transform", headers=headers, json=self.value)
        self.assertEqual(await response.json(), {"text": "refined prompt"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        key, locale, payload = self.completions[0]
        self.assertEqual(key, "Bearer fixture-text-key")
        self.assertEqual(locale, "zh-CN")
        self.assertEqual(payload["model"], "chat")
        self.assertFalse(payload["stream"])
        self.assertIn("Keep the original language", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1], {"role": "user", "content": self.value["prompt"]})

        self.reply = {"choices": [{"message": {"content": [{"type": "text", "text": "一艘船"}]}}]}
        response = await self.browser.post("/easyapi/prompt/transform", headers=headers,
                                           json=self.value | {"action": "translate", "target_language": "zh-TW"})
        self.assertEqual(await response.json(), {"text": "一艘船"})
        self.assertIn("Traditional Chinese", self.completions[1][2]["messages"][0]["content"])
        self.assertEqual(len(self.completions), 2)

    async def test_missing_and_incomplete_editor_responses_fail(self):
        headers = await self.management_headers()
        for choice in ({"message": {"content": ""}}, {"message": {"content": "partial"}, "finish_reason": "length"},
                       {"message": {"refusal": "blocked", "content": "irrelevant"}}):
            self.reply = {"choices": [choice]}
            response = await self.browser.post("/easyapi/prompt/transform", headers=headers, json=self.value)
            self.assertEqual(response.status, 502)
            self.assertNotIn("text", (await response.json()).keys())
