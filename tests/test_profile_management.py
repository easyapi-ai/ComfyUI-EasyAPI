import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from easyapi_comfy.authorization import AuthorizationBroker, AuthorizationError
from easyapi_comfy.profiles import ENV_PROFILE_ID, OFFICIAL_GATEWAY, ProfileStore
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes


class ProfileCredentialsTests(unittest.TestCase):
    def test_removes_only_the_legacy_installer_seed(self):
        seeded = {"id": "mock", "name": "离线测试（不计费）", "base_url": "http://comfytv-easyapi-mock:3199",
                  "api_key": "mock-only-key"}
        manual = {"id": "manual", "name": "Manual gateway", "base_url": OFFICIAL_GATEWAY,
                  "api_key": "sk-connection-fixture"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "EASYAPI_API_KEY": "", "EASYAPI_API_KEY_FILE": "",
        }):
            store = ProfileStore(directory)
            for versioned in (False, True):
                with self.subTest(versioned=versioned):
                    entries = {"mock": seeded, "manual": manual}
                    payload = {"version": 2, "profiles": entries, "default_profile_id": "mock"} if versioned else entries
                    store.path.write_text(json.dumps(payload), encoding="utf-8")

                    self.assertEqual([profile["id"] for profile in store.list_public()], ["manual"])
                    self.assertIsNone(store.default_profile_id())
                    self.assertEqual(store.get(), manual)
                    persisted = json.loads(store.path.read_text(encoding="utf-8"))
                    self.assertEqual(persisted["profiles"] if versioned else persisted, {"manual": manual})
                    if versioned:
                        self.assertIsNone(persisted["default_profile_id"])

            store.save("mock", seeded["base_url"], seeded["api_key"], seeded["name"], make_default=True)
            reopened = ProfileStore(directory)
            self.assertEqual(reopened.get()["id"], "mock")
            self.assertEqual(reopened.get()["auth_method"], "manual")

    def test_official_credential_can_be_relabelled_but_not_sent_to_another_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            issued_key = "sk-private-test-one"
            store.save("one", OFFICIAL_GATEWAY, issued_key, auth_method="official", client_id="bound-instance",
                       credential_expires_at=1920000000, token_id=8)
            public = store.save("one", OFFICIAL_GATEWAY + "/v1", "", "New label")
            self.assertEqual(public["auth_method"], "official")
            self.assertEqual(public["key_hint"], "••••-one")
            self.assertNotIn(issued_key, json.dumps(public))
            self.assertNotIn("client_id", public)
            self.assertNotIn("token_id", public)
            with self.assertRaisesRegex(ValueError, "official gateway"):
                store.save("one", "https://custom.invalid", "")
            self.assertEqual(store.get("one")["api_key"], issued_key)
            replaced = store.save("one", "https://custom.invalid", "sk-new-manual-key")
            self.assertEqual(replaced["auth_method"], "manual")
            self.assertNotIn("client_id", store.get("one"))
            revision = store.revision("one")
            store.save("one", "https://custom.invalid", "sk-replaced-by-user")
            with self.assertRaisesRegex(ValueError, "authorization was pending"):
                store.save("one", OFFICIAL_GATEWAY, issued_key, auth_method="official", expected_revision=revision)
            self.assertEqual(store.get("one")["api_key"], "sk-replaced-by-user")

    def test_env_file_is_read_only_and_never_stored_in_profile_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "EASYAPI_API_KEY": "", "EASYAPI_BASE_URL": OFFICIAL_GATEWAY,
            "EASYAPI_API_KEY_FILE": str(Path(directory) / "docker-secret"),
        }):
            Path(os.environ["EASYAPI_API_KEY_FILE"]).write_text("sk-test-environment-only\n", encoding="utf-8")
            store = ProfileStore(directory)
            self.assertEqual(store.resolve_id(), ENV_PROFILE_ID)
            self.assertTrue(store.list_public()[0]["read_only"])
            store.set_default(ENV_PROFILE_ID)
            self.assertEqual(ProfileStore(directory).get()["auth_method"], "environment")
            self.assertNotIn("sk-test-environment-only", store.path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "server environment"):
                store.save(ENV_PROFILE_ID, OFFICIAL_GATEWAY, "sk-other-key")
            with self.assertRaisesRegex(ValueError, "server environment"):
                store.delete(ENV_PROFILE_ID)


class ConnectionTestRoute(unittest.IsolatedAsyncioTestCase):
    async def test_abandoned_throttled_authorization_clears_account_credential_at_deadline(self):
        async def gateway(method, url, **kwargs):
            if url.endswith("/cli/session"):
                return {"success": True, "data": {"expires_in": 600}}
            if url.endswith("/cli/ticket"):
                return {"success": True, "data": {"status": "ready", "ticket": "mock-ticket"}}
            if url.endswith("/cli/exchange"):
                return {"success": True, "data": {"access_token": "mock-intermediate-account-token", "user_id": 12}}
            if url.endswith("/tokens"):
                raise AuthorizationError("rate_limited", "Retry", status=429)
            self.fail("Unexpected upstream action")

        with tempfile.TemporaryDirectory() as directory:
            broker = AuthorizationBroker(ProfileStore(directory), directory, transport=gateway, ttl=.1, poll_interval=0)
            started = await broker.start("browser", "profile", "Test")
            self.assertEqual((await broker.poll("browser", started["flow_id"]))["status"], "pending")
            flow = broker._flows[started["flow_id"]]
            self.assertTrue(flow.access_token)
            await asyncio.sleep(.15)
            self.assertEqual(flow.status, "expired")
            self.assertEqual((flow.access_token, flow.verifier), ("", ""))

    async def test_only_checks_models_and_never_echoes_a_credential(self):
        calls = []
        key = "sk-private-test-token"

        async def models(request):
            auth = request.headers.get("Authorization")
            calls.append((request.method, request.path))
            if auth != f"Bearer {key}":
                return web.json_response({"error": {"message": auth}}, status=401)
            return web.json_response({"data": [{"id": "fixture"}]})

        upstream_app = web.Application()
        upstream_app.router.add_get("/v1/models", models)
        async with TestServer(upstream_app) as gateway:
            with tempfile.TemporaryDirectory() as directory:
                runtime = Runtime(directory)
                base = str(gateway.make_url("/")).rstrip("/")
                runtime.profiles.save("test", base, key)
                app = web.Application()
                app.add_routes(build_routes(runtime))
                async with TestClient(TestServer(app)) as browser:
                    headers = {"Origin": str(browser.make_url("/")).rstrip("/")}
                    response = await browser.post("/easyapi/profiles/test/test", json={}, headers=headers)
                    self.assertEqual(response.status, 403)
                    self.assertEqual(calls, [])
                    session = await (await browser.get("/easyapi/bootstrap")).json()
                    headers["X-EasyAPI-Management"] = session["management_nonce"]
                    result = await (await browser.post("/easyapi/profiles/test/test", json={}, headers=headers)).json()
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["model_count"], 1)
                    self.assertEqual(result["profile"]["connection_status"], "ok")
                    self.assertNotIn(key, json.dumps(result))
                    runtime.profiles.save("test", base, "sk-invalid-test-token")
                    result = await browser.post("/easyapi/profiles/test/test", json={}, headers=headers)
                    self.assertEqual(result.status, 401)
                    self.assertNotIn("sk-invalid-test-token", await result.text())
                    self.assertEqual(calls, [("GET", "/v1/models"), ("GET", "/v1/models")])
                await runtime.close()

    async def test_presets_and_authorization_use_management_session(self):
        flow = {"flow_id": "mock-flow", "status": "pending", "authorize_url": "https://www.easyapi.com/cli/authorize?state=mock"}
        broker = AsyncMock()
        broker.start.return_value = flow
        with tempfile.TemporaryDirectory() as directory, patch(
                "easyapi_comfy.authorization.AuthorizationBroker", return_value=broker):
            runtime = Runtime(directory)
            runtime.profiles.save("test", OFFICIAL_GATEWAY, "sk-private-test-token")
            app = web.Application()
            app.add_routes(build_routes(runtime))
            async with TestClient(TestServer(app)) as browser:
                headers = {"Origin": str(browser.make_url("/")).rstrip("/")}
                value = {"profile_id": "test", "operation": "chat", "model": "fixture",
                         "name": "Draft", "params": {"temperature": 0, "stream": False}, "make_default": True}
                self.assertEqual((await browser.post("/easyapi/presets", json=value, headers=headers)).status, 403)
                self.assertEqual((await browser.post("/easyapi/auth/start", json={"profile_id": "test"}, headers=headers)).status, 403)
                broker.start.assert_not_awaited()
                session = await (await browser.get("/easyapi/bootstrap")).json()
                headers["X-EasyAPI-Management"] = session["management_nonce"]
                created = await (await browser.post("/easyapi/presets", json=value, headers=headers)).json()
                records = await (await browser.get("/easyapi/presets?profile_id=test&operation=chat")).json()
                preset_id = created["preset"]["id"]
                self.assertEqual(records["default_preset_ids"], {"chat": preset_id})
                self.assertEqual(records["presets"][0]["params"], value["params"])
                self.assertNotIn("sk-private-test-token", json.dumps(records))
                started = await browser.post("/easyapi/auth/start", json={"profile_id": "test"}, headers=headers)
                self.assertEqual(await started.json(), flow)
                self.assertEqual(started.headers["Cache-Control"], "no-store")
                self.assertEqual(broker.start.await_args.args[1], "test")
