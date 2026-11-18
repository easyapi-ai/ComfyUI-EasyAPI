"""Browser consent is mocked; no test registers a real authorization or key."""
import asyncio
import copy
import hashlib
import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from easyapi_comfy.authorization import AuthorizationBroker, AuthorizationError, KEY_LIFETIME, OFFICIAL_GATEWAY
from easyapi_comfy.client import EasyAPIClient
from easyapi_comfy.profiles import ENV_PROFILE_ID, ProfileStore
from easyapi_comfy.realtime import RealtimeManager


class MockAuthorization:
    def __init__(self):
        self.calls = []
        self.state = self.verifier_hash = None
        self.ready = False
        self.tokens = []
        self.keys = {}
        self.user_id = 42
        self.account_token = "account-token-never-persist-or-return"
        self.ticket = "one-time-ticket-never-return"
        self.raw_key = "generation-key-server-storage-only"
        self.next_id = 100
        self.rate_limit = False
        self.usage_legacy = False
        self.usage_valid = True
        self.pause = False
        self.entered, self.resume = asyncio.Event(), asyncio.Event()

    async def __call__(self, method, url, *, body=None, query=None, headers=None):
        path = urlsplit(url).path
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(query), dict(headers or {})))
        if path == "/cli/session":
            self.state, self.verifier_hash = body["state"], body["verifier_hash"]
            self.assert_empty_headers(headers)
            data = {"expires_in": 600}
        elif path == "/cli/ticket":
            assert query["state"] == self.state
            assert hashlib.sha256(query["verifier"].encode()).hexdigest() == self.verifier_hash
            if self.rate_limit:
                self.rate_limit = False
                raise AuthorizationError("rate_limited", "Authorization is temporarily rate limited", status=429, retry_after=8)
            if self.pause:
                self.entered.set()
                await self.resume.wait()
            data = {"status": "ready", "ticket": self.ticket} if self.ready else {"status": "pending"}
        elif path == "/cli/exchange":
            assert body == {"ticket": self.ticket}
            data = {"access_token": self.account_token, "user_id": self.user_id}
        elif path == "/tokens":
            self.assert_account(headers)
            data = {"items": self.tokens}
        elif path == "/token":
            self.assert_account(headers)
            self.next_id += 1
            token = {"id": self.next_id, "user_id": self.user_id, "status": 1, **body}
            self.tokens.append(token)
            self.keys[self.next_id] = self.raw_key
            data = {}
        elif path.startswith("/token/") and path.endswith("/key"):
            self.assert_account(headers)
            token_id = int(path.split("/")[2])
            data = {"key": self.keys.get(token_id, self.raw_key)}
        elif path == "/usage/token":
            assert headers["Authorization"] == "Bearer sk-" + self.raw_key
            assert len(headers["X-EasyAPI-Client-Id"]) == 64
            return {"success" if self.usage_legacy else "code": self.usage_valid, "message": "ok", "data": {"total_available": 100}}
        else:
            raise AssertionError("unexpected mocked path")
        return {"success": True, "data": data}

    @staticmethod
    def assert_empty_headers(headers):
        assert not headers

    def assert_account(self, headers):
        assert headers["Authorization"] == "Bearer " + self.account_token
        assert headers["EasyAPI-User"] == str(self.user_id)

    def count(self, path):
        return sum(item[1] == path for item in self.calls)


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, {"EASYAPI_API_KEY": "", "EASYAPI_API_KEY_FILE": ""})
        self.environment.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProfileStore(self.root / "profiles.json")
        self.transport = MockAuthorization()
        self.now = 1000.0
        self.wall = 1700000000
        self.broker = AuthorizationBroker(self.store, self.root, transport=self.transport,
            gateway_base="https://mock-gateway.invalid", console_base="https://mock-console.invalid",
            clock=lambda: self.now, wall_time=lambda: self.wall)

    async def asyncTearDown(self):
        await self.broker.close()
        self.temp.cleanup()
        self.environment.stop()

    async def begin(self):
        result = await self.broker.start("management-owner", "studio", "Studio", make_default=True)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["retry_after"], 2)
        self.now += 2
        return result

    async def test_poll_consent_creates_bound_30_day_key_and_never_exposes_account_secrets(self):
        self.assertFalse((self.root / "auth-instance.json").exists())
        started = await self.begin()
        query = parse_qs(urlsplit(started["authorize_url"]).query)
        self.assertEqual(query["state"], [self.transport.state])
        self.assertNotIn("redirect_uri", query)
        self.assertNotIn("verifier", query)
        flow = self.broker._flows[started["flow_id"]]
        verifier = flow.verifier
        pending = await self.broker.poll("management-owner", flow.id)
        self.assertEqual(pending["status"], "pending")
        self.transport.ready = True
        self.now += 2
        result = await self.broker.poll("management-owner", flow.id)
        self.assertEqual(result["status"], "authorized")
        self.assertEqual(result["profile"]["auth_method"], "official")
        self.assertEqual(self.store.default_profile_id(), "studio")
        saved = self.store.get("studio")
        self.assertEqual(saved["base_url"], OFFICIAL_GATEWAY)
        self.assertEqual(saved["api_key"], "sk-" + self.transport.raw_key)
        self.assertEqual(saved["client_id"], flow.client_id)
        self.assertEqual(saved["credential_expires_at"], self.wall + KEY_LIFETIME)
        serialized = json.dumps(result)
        private_disk = (self.root / "profiles.json").read_text()
        for secret in (self.transport.account_token, self.transport.ticket, verifier):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, private_disk)
        self.assertNotIn(saved["api_key"], serialized)
        self.assertEqual((flow.access_token, flow.state, flow.verifier), ("", "", ""))
        self.assertEqual(self.transport.count("/token"), 1)

    async def test_ownership_throttling_idempotent_start_and_concurrent_poll(self):
        started = await self.begin()
        self.assertEqual((await self.broker.start("management-owner", "studio", "Studio"))["flow_id"], started["flow_id"])
        self.assertEqual(self.transport.count("/cli/session"), 1)
        foreign = await self.broker.poll("another-owner", started["flow_id"])
        self.assertEqual(foreign["code"], "authorization_not_found")
        self.assertEqual((await self.broker.cancel("another-owner", started["flow_id"]))["code"], "authorization_not_found")
        await self.broker.poll("management-owner", started["flow_id"])
        await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(self.transport.count("/cli/ticket"), 1)
        self.now += 2
        self.transport.ready = True
        results = await asyncio.gather(*(self.broker.poll("management-owner", started["flow_id"]) for _ in range(3)))
        self.assertTrue(all(result["status"] == "authorized" for result in results))
        self.assertEqual(self.transport.count("/cli/exchange"), 1)
        self.assertEqual(self.transport.count("/token"), 1)

    async def test_cancel_during_inflight_ticket_never_exchanges_or_saves(self):
        started = await self.begin()
        self.transport.pause = self.transport.ready = True
        task = asyncio.create_task(self.broker.poll("management-owner", started["flow_id"]))
        await self.transport.entered.wait()
        cancelled = await self.broker.cancel("management-owner", started["flow_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.transport.resume.set()
        self.assertEqual((await task)["status"], "cancelled")
        self.assertEqual(self.transport.count("/cli/exchange"), 0)
        self.assertFalse((self.root / "profiles.json").exists())

    async def test_expiry_rate_limit_and_instance_identity_survive_restart(self):
        started = await self.begin()
        self.transport.rate_limit = True
        limited = await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(limited["status"], "pending")
        self.assertEqual(limited["retry_after"], 8)
        await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(self.transport.count("/cli/ticket"), 1)
        identity = self.broker._flows[started["flow_id"]].client_id
        self.now += 301
        self.assertEqual((await self.broker.poll("management-owner", started["flow_id"]))["status"], "expired")
        restarted = AuthorizationBroker(self.store, self.root, transport=MockAuthorization())
        self.assertEqual(restarted._instance_client_id(), identity)
        await restarted.close()

    async def test_reuse_only_current_account_same_instance_active_key_and_never_enable_disabled(self):
        started = await self.begin()
        flow = self.broker._flows[started["flow_id"]]
        row = {"id": 90, "user_id": 42, "name": self.broker._key_name(flow), "client_id": flow.client_id,
               "status": 1, "expired_time": self.wall + 3600, "unlimited_quota": True}
        self.transport.tokens = [row]
        self.transport.ready = True
        result = await self.broker.poll("management-owner", flow.id)
        self.assertEqual(result["status"], "authorized")
        self.assertEqual(self.transport.count("/token"), 0)
        self.assertEqual(self.store.get("studio")["token_id"], 90)
        # A fresh authorization explicitly checks, but never re-enables, the
        # disabled dedicated key left by a user in the platform console.
        started = await self.broker.start("management-owner", "studio", "Studio")
        self.now += 2
        row["status"] = 2
        disabled = await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(disabled["status"], "failed")
        self.assertEqual(disabled["code"], "dedicated_key_disabled")
        self.assertEqual(self.transport.count("/token"), 0)

    async def test_switch_account_never_reads_or_revokes_the_previous_accounts_token(self):
        started = await self.begin()
        flow = self.broker._flows[started["flow_id"]]
        self.transport.tokens = [{"id": 7, "user_id": 42, "name": self.broker._key_name(flow), "client_id": flow.client_id,
                                  "status": 1, "expired_time": -1, "unlimited_quota": True}]
        self.transport.user_id = 88
        self.transport.ready = True
        result = await self.broker.poll("management-owner", flow.id)
        self.assertEqual(result["status"], "authorized")
        self.assertNotIn("/token/7/key", [call[1] for call in self.transport.calls])
        self.assertFalse(any(call[0] in {"DELETE", "PUT"} for call in self.transport.calls))
        self.assertEqual(self.transport.count("/token"), 1)

    async def test_reauthorization_does_not_reuse_unlimited_or_long_lived_dedicated_key(self):
        for expires in (-1, self.wall + KEY_LIFETIME * 3):
            with self.subTest(expires=expires):
                started = await self.broker.start("owner", "renew" + str(abs(expires)), "Renew")
                self.now += 2
                flow = self.broker._flows[started["flow_id"]]
                old_id = self.transport.next_id + 500
                self.transport.tokens = [{"id": old_id, "user_id": 42, "name": self.broker._key_name(flow), "client_id": flow.client_id,
                                          "status": 1, "expired_time": expires, "unlimited_quota": True}]
                self.transport.ready = True
                result = await self.broker.poll("owner", flow.id)
                self.assertEqual(result["status"], "authorized")
                self.assertEqual(result["profile"]["credential_expires_at"], self.wall + KEY_LIFETIME)
                self.assertNotIn("/token/%s/key" % old_id, [call[1] for call in self.transport.calls])

    async def test_parallel_manual_profile_edit_prevents_token_provision_and_overwrite(self):
        started = await self.begin()
        self.store.save("studio", OFFICIAL_GATEWAY, "manually-selected-mock-key", "Manual")
        self.transport.ready = True
        result = await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "profile_changed")
        self.assertEqual(self.transport.count("/token"), 0)
        self.assertEqual(self.store.get("studio")["name"], "Manual")

    async def test_profile_cas_catches_edit_during_final_profile_save(self):
        started = await self.begin()
        original_save = self.store.save
        def concurrent_save(*args, **kwargs):
            original_save("studio", OFFICIAL_GATEWAY, "manual-key-before-cas", "Manual winner")
            return original_save(*args, **kwargs)
        self.transport.ready = True
        with patch.object(self.store, "save", side_effect=concurrent_save):
            result = await self.broker.poll("management-owner", started["flow_id"])
        self.assertEqual(result["code"], "profile_changed")
        self.assertEqual(self.store.get("studio")["name"], "Manual winner")

    async def test_readonly_environment_profile_is_rejected_before_any_authorization_request(self):
        with patch.dict(os.environ, {"EASYAPI_API_KEY": "mock-environment-key"}):
            result = await self.broker.start("owner", ENV_PROFILE_ID, "Environment")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "profile_read_only")
        self.assertEqual(self.transport.calls, [])

    async def test_usage_contract_accepts_legacy_success_and_rejects_false_code(self):
        for legacy, valid in ((True, True), (False, False)):
            with self.subTest(legacy=legacy, valid=valid):
                started = await self.broker.start("owner", "usage" + str(legacy), "Usage")
                self.now += 2
                self.transport.usage_legacy, self.transport.usage_valid = legacy, valid
                self.transport.ready = True
                result = await self.broker.poll("owner", started["flow_id"])
                self.assertEqual(result["status"], "authorized" if valid else "failed")

    async def test_network_errors_are_sanitized_and_constructor_cannot_select_untrusted_gateway(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("PRIVATE_TICKET PRIVATE_ACCESS_TOKEN PRIVATE_VERIFIER")
        broker = AuthorizationBroker(self.store, self.root, transport=fail)
        result = await broker.start("owner", "other", "Other")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertNotIn("authorize_url", result)
        with self.assertRaises(ValueError):
            AuthorizationBroker(self.store, self.root, gateway_base="https://untrusted.invalid")
        await broker.close()


class BindingTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_binding_header_is_gateway_only_and_anonymous_metadata_has_no_credentials(self):
        received = []
        external = web.Application()
        async def media(request):
            received.append(dict(request.headers))
            return web.Response(body=b"media")
        external.router.add_get("/media", media)
        other = TestServer(external)
        await other.start_server()
        seen = []
        gateway = web.Application()
        async def models(request):
            seen.append(dict(request.headers))
            return web.json_response({"data": []})
        async def redirect(request):
            raise web.HTTPFound(str(other.make_url("/media")))
        gateway.router.add_get("/v1/models", models)
        gateway.router.add_get("/pricing", models)
        gateway.router.add_get("/v1/media/redirect", redirect)
        server = TestServer(gateway)
        await server.start_server()
        client = EasyAPIClient({"base_url": str(server.make_url("")), "api_key": "mock-bound-key", "client_id": "a" * 64})
        try:
            await client.request("GET", "/v1/models")
            await client.request("GET", "/pricing", authenticated=False)
            self.assertEqual(seen[0]["X-EasyAPI-Client-Id"], "a" * 64)
            self.assertNotIn("Authorization", seen[1])
            self.assertNotIn("X-EasyAPI-Client-Id", seen[1])
            with tempfile.TemporaryDirectory() as directory:
                await client.download(str(server.make_url("/v1/media/redirect")), Path(directory) / "out")
            self.assertNotIn("Authorization", received[0])
            self.assertNotIn("X-EasyAPI-Client-Id", received[0])
        finally:
            await client.close()
            await server.close()
            await other.close()

    async def test_realtime_binding_header_and_cross_origin_redirect_refusal(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected), tempfile.TemporaryDirectory() as directory:
                leaked, upstream_headers = [], []
                external = web.Application()
                async def foreign(request):
                    leaked.append(dict(request.headers))
                    return web.Response()
                external.router.add_get("/foreign", foreign)
                other = TestServer(external)
                await other.start_server()
                gateway = web.Application()
                async def upstream(request):
                    upstream_headers.append(dict(request.headers))
                    if redirected:
                        raise web.HTTPFound(str(other.make_url("/foreign")))
                    socket = web.WebSocketResponse()
                    await socket.prepare(request)
                    async for _ in socket:
                        pass
                    return socket
                gateway.router.add_get("/v1/realtime", upstream)
                server = TestServer(gateway)
                await server.start_server()
                profile = {"id": "bound", "base_url": str(server.make_url("")), "api_key": "mock-ws-key", "client_id": "b" * 64}
                runtime = types.SimpleNamespace(state_dir=directory, profiles=types.SimpleNamespace(get=lambda _: profile))
                manager = RealtimeManager(runtime, Path(directory) / "snapshots")
                app = web.Application()
                app.router.add_get("/easyapi/realtime", manager.handle)
                browser = TestClient(TestServer(app))
                await browser.start_server()
                try:
                    ws = await browser.ws_connect("/easyapi/realtime?profile_id=bound&model=mock-realtime")
                    result = await ws.receive_json(timeout=3)
                    self.assertEqual(result["type"], "easyapi.error" if redirected else "easyapi.connected")
                    self.assertEqual(upstream_headers[0]["X-EasyAPI-Client-Id"], "b" * 64)
                    self.assertEqual(leaked, [])
                    await ws.close()
                finally:
                    await browser.close()
                    await server.close()
                    await other.close()
