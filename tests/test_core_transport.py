"""Behavior tests against loopback servers; no upstream credentials or network."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from easyapi_comfy.client import APIResponse, EasyAPIClient, EasyAPIError, UploadFile
from easyapi_comfy.profiles import ProfileStore
from easyapi_comfy.protocols import get_protocol, validate_path
from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes, require_management
from easyapi_comfy.tasks import TaskStore, TaskTimeout, create_task_handle, wait_for_task


class ProfileTests(unittest.TestCase):
    def test_keys_remain_private_and_blank_edits_preserve_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            store.save("first", "https://example.invalid/v1/", "private-test-key", "Primary")
            edited = store.save("first", "https://example.invalid", "", "Renamed")
            self.assertNotIn("private-test-key", json.dumps(edited))
            self.assertNotIn("api_key", json.dumps(store.list_public()))
            reopened = ProfileStore(directory)
            self.assertEqual(reopened.get("first")["api_key"], "private-test-key")
            self.assertEqual(reopened.get("first")["base_url"], "https://example.invalid")
            with self.assertRaises(ValueError):
                reopened.save("../escape", "https://example.invalid", "x")
            with self.assertRaises(ValueError):
                reopened.save("first", "https://user:secret@example.invalid", "x")

    def test_dynamic_creative_routes_are_allowed_without_admin_proxy(self):
        self.assertTrue(validate_path("/plugin-a/api/v3/contents/generations/tasks"))
        self.assertTrue(validate_path("/plugin-b/api/v1/tasks/job"))
        for path in ("https://other.invalid/v1/chat/completions", "//other.invalid/v1/models", "/api/user/self", "/admin/channels", "/v1/%2e%2e/admin", "/v1/..%5cadmin"):
            with self.assertRaises(ValueError, msg=path):
                validate_path(path)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.calls = []
        self.polls = 0
        self.app = web.Application()

        async def echo(request):
            self.calls.append((request.method, request.path, request.headers.get("Authorization")))
            if request.content_type.startswith("multipart/"):
                parts = []
                reader = await request.multipart()
                async for part in reader:
                    content = await part.read()
                    parts.append({"name": part.name, "filename": part.filename, "value": content.decode(errors="replace")})
                return web.json_response({"parts": parts})
            return web.json_response({"body": await request.json() if request.can_read_body else {}, "query": dict(request.query)})

        async def sse(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            data = 'event: delta\ndata: {"text":"你好"}\n\ndata: [DONE]\n\n'.encode()
            split = data.index("你".encode()) + 1
            await response.write(data[:split])
            await response.write(data[split:])
            await response.write_eof()
            return response

        async def poll(request):
            self.polls += 1
            if self.polls == 1:
                return web.json_response({"error": "temporary"}, status=503)
            return web.json_response({"data": {"status": "completed", "download_url": "/result.mp4"}})

        async def slow_poll(request):
            await asyncio.sleep(.6)
            return web.json_response({"data": {"status": "pending"}})

        async def error(request):
            return web.json_response({"error": {"message": "bad private-test-key"}}, status=400)

        async def binary(request):
            return web.Response(body=b"1234567890", content_type="audio/wav")

        async def error_json_200(request):
            return web.json_response({"error": {"message": "stream rejected private-test-key"}})

        async def error_sse_200(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'event: error\ndata: {"error":{"message":"stream rejected private-test-key"}}\n\n')
            await response.write_eof()
            return response

        self.app.router.add_route("*", "/v1/echo", echo)
        self.app.router.add_post("/v1/stream", sse)
        self.app.router.add_get("/v1/videos/test", poll)
        self.app.router.add_get("/v1/videos/slow", slow_poll)
        self.app.router.add_get("/v1/error", error)
        self.app.router.add_get("/v1/binary", binary)
        self.app.router.add_post("/v1/error-json-200", error_json_200)
        self.app.router.add_post("/v1/error-sse-200", error_sse_200)
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "private-test-key")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.temporary.cleanup()

    async def test_json_and_multipart_preserve_structures_and_repeat_files(self):
        response = await self.client.request("POST", "/v1/echo", body={"flag": False, "zero": 0, "nested": {"a": [1, 2]}}, query={"mode": "native"})
        self.assertEqual(response.data["body"], {"flag": False, "zero": 0, "nested": {"a": [1, 2]}})
        self.assertEqual(response.data["query"], {"mode": "native"})
        self.assertEqual(self.calls[-1][2], "Bearer private-test-key")
        file = self.directory / "reference.png"
        file.write_bytes(b"fixture")
        response = await self.client.request("POST", "/v1/echo", body={"flag": False, "nested": {"a": [1]}},
                                             files=[UploadFile("image", file), UploadFile("image", file)])
        self.assertEqual([p["name"] for p in response.data["parts"]], ["flag", "nested", "image", "image"])
        self.assertEqual(response.data["parts"][0]["value"], "false")
        self.assertEqual(json.loads(response.data["parts"][1]["value"]), {"a": [1]})

    async def test_sse_handles_utf8_network_boundaries(self):
        response = await self.client.request("POST", "/v1/stream", body={}, stream=True)
        self.assertEqual(response.data["events"][0]["data"]["text"], "你好")
        self.assertEqual(len(response.data["events"]), 1)

    async def test_errors_redact_secrets_and_binary_has_a_memory_limit(self):
        with self.assertRaises(EasyAPIError) as caught:
            await self.client.request("GET", "/v1/error")
        self.assertNotIn("private-test-key", str(caught.exception))
        self.client.max_response_bytes = 5
        with self.assertRaisesRegex(EasyAPIError, "memory limit"):
            await self.client.request("GET", "/v1/binary")

    async def test_http_200_error_json_and_sse_are_failures_not_empty_success(self):
        for path in ("/v1/error-json-200", "/v1/error-sse-200"):
            with self.subTest(path=path), self.assertRaises(EasyAPIError) as caught:
                await self.client.request("POST", path, body={}, stream=True)
            self.assertIn("rejected", str(caught.exception))
            self.assertNotIn("private-test-key", str(caught.exception))

    async def test_rotating_or_deleting_a_profile_disables_existing_client_snapshots(self):
        runtime = Runtime(self.directory / "profiles")
        runtime.profiles.save("test", str(self.server.make_url("")), "old-key")
        old = runtime.client("test")
        await old.request("POST", "/v1/echo", body={})
        runtime.profiles.save("test", str(self.server.make_url("")), "new-key")
        with self.assertRaises(EasyAPIError):
            await old.request("POST", "/v1/echo", body={})
        new = runtime.client("test")
        await new.request("POST", "/v1/echo", body={})
        self.assertEqual([call[2] for call in self.calls], ["Bearer old-key", "Bearer new-key"])
        runtime.profiles.delete("test")
        with self.assertRaises(EasyAPIError):
            await new.request("POST", "/v1/echo", body={})
        await runtime.close()
        self.assertFalse(old._sessions)
        self.assertFalse(new._sessions)

    async def test_download_redirect_never_forwards_gateway_auth(self):
        received = []
        async def public(request):
            received.append(request.headers.get("Authorization"))
            return web.Response(body=b"media-fixture")
        other = TestServer(web.Application())
        other.app.router.add_get("/media", public)
        await other.start_server()
        async def redirect(request):
            raise web.HTTPFound(str(other.make_url("/media")))
        # Add redirect on a separate server because aiohttp freezes started routers.
        gateway = TestServer(web.Application())
        gateway.app.router.add_get("/v1/media", redirect)
        await gateway.start_server()
        client = EasyAPIClient(str(gateway.make_url("")), "private-test-key")
        try:
            path = await client.download(str(gateway.make_url("/v1/media")), self.directory / "media.bin")
            self.assertEqual(path.read_bytes(), b"media-fixture")
            self.assertEqual(received, [None])
            with self.assertRaises(EasyAPIError):
                await client.download(str(other.make_url("/media")), self.directory / "too-large.bin", max_bytes=2)
            self.assertFalse((self.directory / "too-large.bin.partial").exists())
        finally:
            await client.close()
            await gateway.close()
            await other.close()

    async def test_empty_download_preserves_existing_file_and_removes_partial(self):
        app = web.Application()

        async def empty(request):
            return web.Response(body=b"", content_type="image/png")

        app.router.add_get("/result.png", empty)
        server = TestServer(app)
        await server.start_server()
        destination = self.directory / "result.png"
        destination.write_bytes(b"existing-file")
        try:
            with self.assertRaisesRegex(EasyAPIError, "[Ee]mpty"):
                await self.client.download(str(server.make_url("/result.png")), destination)
            self.assertEqual(destination.read_bytes(), b"existing-file")
            self.assertFalse(destination.with_name("result.png.partial").exists())
        finally:
            await server.close()

    async def test_wait_recovers_transient_queries_without_submit_and_persists_result(self):
        store = TaskStore(self.directory)
        handle = create_task_handle(APIResponse(data={"id": "test"}), "profile", get_protocol("video-generation"))
        store.save(handle)
        reopened = TaskStore(self.directory)
        response = await wait_for_task(self.client, reopened, reopened.get("test"), timeout=2, interval=.01)
        self.assertEqual(response.data["data"]["status"], "completed")
        self.assertEqual(reopened.get("test")["state"], "completed")
        self.assertEqual(self.polls, 2)
        self.assertFalse(self.calls)

    async def test_interrupt_preserves_handle_for_a_new_process(self):
        store = TaskStore(self.directory)
        handle = create_task_handle(APIResponse(data={"id": "test"}), "profile", get_protocol("video-generation"))
        with self.assertRaises(asyncio.CancelledError):
            await wait_for_task(self.client, store, handle, interrupt=lambda: True)
        self.assertEqual(TaskStore(self.directory).get("test")["state"], "paused")
        self.assertEqual(self.polls, 0)

    async def test_framework_interrupt_exception_also_preserves_a_resumable_handle(self):
        store = TaskStore(self.directory)
        handle = create_task_handle(APIResponse(data={"id": "test"}), "profile", get_protocol("video-generation"))
        class FrameworkInterrupted(Exception):
            pass
        def interrupted():
            raise FrameworkInterrupted()
        with self.assertRaises(FrameworkInterrupted):
            await wait_for_task(self.client, store, handle, interrupt=interrupted)
        self.assertEqual(TaskStore(self.directory).get("test")["state"], "paused")
        self.assertEqual(self.polls, 0)

    async def test_interrupt_cancels_an_inflight_poll_without_waiting_for_http_timeout(self):
        store = TaskStore(self.directory)
        handle = create_task_handle(APIResponse(data={"id": "slow"}), "profile", get_protocol("video-generation"))
        loop = asyncio.get_running_loop()
        start = loop.time()
        with self.assertRaises(asyncio.CancelledError):
            await wait_for_task(self.client, store, handle, timeout=2, interrupt=lambda: loop.time() - start > .05)
        self.assertLess(loop.time() - start, .35)
        self.assertEqual(TaskStore(self.directory).get("slow")["state"], "paused")


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_matching_never_uses_an_unrelated_first_variant(self):
        app = web.Application()
        async def docs(request):
            return web.json_response({"success": True, "data": {
                "endpoints": [{"key": "api-video", "path": "/v1/videos", "request_schema": {
                    "type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}],
                "variants": [{"key": "image-to-video", "path": "/v1/videos", "request_schema": {
                    "type": "object", "properties": {"image": {"type": "string"}}, "required": ["image"]}}]}})
        app.router.add_get("/pricing/docs", docs)
        server = TestServer(app)
        await server.start_server()
        client = EasyAPIClient(str(server.make_url("")), "mock-only-key")
        try:
            generic = await client.model_schema("custom-video", "video-generation")
            self.assertTrue(generic["matched"])
            self.assertEqual(generic["required"], ["prompt"])
            unrelated = await client.model_schema("custom-video", "native-kling-image")
            self.assertFalse(unrelated["matched"])
            self.assertFalse(unrelated["declared"])
            self.assertEqual(unrelated["required"], [])
            self.assertIsNotNone(unrelated["docs"])
        finally:
            await client.close()
            await server.close()

    async def test_detail_fallback_merges_endpoint_fields_and_nested_constraints(self):
        app = web.Application()
        async def absent(request):
            return web.json_response({"success": False}, status=404)
        async def detail(request):
            return web.json_response({"success": True, "data": {"category": "Image", "params": {"image": {
                "properties": {"prompt": {"type": "string", "required": True}, "seed": {"type": "integer", "min": 0},
                               "config": {"type": "object", "properties": {"mode": {"type": "string", "required": True}}}},
                "endpoint_params": {"image-edit": {"properties": {"seed": {"type": "integer", "min": 4}, "image": {"type": "string", "required": True}}}}
            }}}})
        app.router.add_get("/pricing/docs", absent)
        app.router.add_get("/pricing/schema", absent)
        app.router.add_get("/pricing/detail", detail)
        server = TestServer(app)
        await server.start_server()
        client = EasyAPIClient(str(server.make_url("")), "mock-only-key")
        try:
            data = await client.model_schema("custom-image", "image-edit")
            self.assertEqual(data["source"], "detail")
            schema = data["request_schema"]
            self.assertEqual(schema["properties"]["seed"]["minimum"], 4)
            self.assertCountEqual(schema["required"], ["prompt", "image"])
            self.assertEqual(schema["properties"]["config"]["required"], ["mode"])
        finally:
            await client.close()
            await server.close()


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_proxied_https_manage_profiles_with_separate_sessions(self):
        http_host = "comfy.test:3106"
        https_host = "comfy.test:3443"
        proxy_backend_host = "comfytv:8188"
        http_origin = f"http://{http_host}"
        https_origin = f"https://{https_host}"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"EASYAPI_PUBLIC_ORIGIN": "https://COMFY.TEST:3443/"},
        ):
            runtime = Runtime(directory)
            app = web.Application()
            app.add_routes(build_routes(runtime))
            try:
                async with TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar()) as client:
                    async def bootstrap(host, origin=None, cookies=None):
                        headers = {"Host": host}
                        if cookies:
                            headers["Cookie"] = cookies
                        if origin:
                            headers["Origin"] = origin
                            response = await client.post("/easyapi/bootstrap", headers=headers)
                        else:
                            response = await client.get("/easyapi/bootstrap", headers=headers)
                        self.assertEqual(response.status, 200)
                        self.assertEqual(len(response.cookies), 1)
                        cookie = next(iter(response.cookies.values()))
                        self.assertTrue(cookie["httponly"])
                        self.assertEqual(cookie["samesite"], "Strict")
                        self.assertEqual(cookie["path"], "/")
                        return cookie, (await response.json())["management_nonce"]

                    http_cookie, http_nonce = await bootstrap(http_host)
                    http_cookie_header = f"{http_cookie.key}={http_cookie.value}"
                    http_headers = {"Host": http_host, "Origin": http_origin, "Cookie": http_cookie_header,
                                    "X-EasyAPI-Management": http_nonce}
                    body = {"base_url": "https://example.invalid", "api_key": "fixture-key"}
                    path = "/easyapi/profiles/saved"
                    saved = await client.post(path, json=body, headers=http_headers)
                    self.assertEqual(saved.status, 200, await saved.text())
                    self.assertFalse(http_cookie["secure"])
                    self.assertEqual(runtime.profiles.get("saved")["api_key"], "fixture-key")
                    self.assertEqual(await bootstrap(http_host, http_origin, http_cookie_header), (http_cookie, http_nonce))

                    https_cookie, https_nonce = await bootstrap(https_host)
                    self.assertTrue(https_cookie["secure"])
                    self.assertNotEqual(http_cookie.key, https_cookie.key)
                    self.assertNotEqual(http_nonce, https_nonce)
                    https_cookie_header = f"{http_cookie_header}; {https_cookie.key}={https_cookie.value}"
                    self.assertEqual(await bootstrap(proxy_backend_host, https_origin, https_cookie_header), (https_cookie, https_nonce))
                    https_headers = {"Host": proxy_backend_host, "Origin": https_origin, "Cookie": https_cookie_header,
                                     "X-EasyAPI-Management": https_nonce}

                    for origin in ("https://attacker.invalid", "null", f"http://{https_host}"):
                        rejected = await client.delete(path, headers={**http_headers, "Origin": origin})
                        self.assertEqual(rejected.status, 403)
                        self.assertEqual(await rejected.text(), "Cross-origin EasyAPI request rejected")
                    missing_origin = {key: value for key, value in http_headers.items() if key != "Origin"}
                    self.assertEqual((await client.delete(path, headers=missing_origin)).status, 403)
                    self.assertEqual((await client.delete(path, headers={**https_headers,
                        "Cookie": http_cookie_header, "X-EasyAPI-Management": http_nonce})).status, 403)
                    self.assertEqual((await client.delete(path, headers={**https_headers,
                        "Cookie": f"{https_cookie.key}={http_cookie.value}", "X-EasyAPI-Management": http_nonce})).status, 403)
                    self.assertEqual((await client.delete(path, headers={**http_headers,
                        "X-EasyAPI-Management": https_nonce})).status, 403)
                    insecure_authorization = await client.post("/easyapi/auth/start", json={}, headers=http_headers)
                    self.assertEqual(insecure_authorization.status, 403)
                    self.assertIn("HTTPS or localhost", await insecure_authorization.text())

                    self.assertEqual((await client.post(path, json=body, headers=https_headers)).status, 200)
                    self.assertEqual((await client.delete(path, headers=http_headers)).status, 200)
                    self.assertEqual(runtime.profiles.list_public(), [])
            finally:
                await runtime.close()

    async def test_api_prefix_cookie_authenticates_mutations_and_websocket_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            extension = web.Application()
            extension.add_routes(build_routes(runtime))
            async def realtime(request):
                require_management(request)
                socket = web.WebSocketResponse()
                await socket.prepare(request)
                await socket.send_json({"type": "authenticated"})
                await socket.close()
                return socket
            extension.router.add_get("/easyapi/realtime", realtime)
            app = web.Application()
            app.add_subapp("/api", extension)
            client = TestClient(TestServer(app), cookie_jar=aiohttp.CookieJar(unsafe=True))
            await client.start_server()
            origin = str(client.make_url("")).rstrip("/")
            try:
                response = await client.get("/api/easyapi/bootstrap")
                bootstrap = await response.json()
                self.assertEqual(next(iter(response.cookies.values()))["path"], "/")
                headers = {"Origin": origin, "X-EasyAPI-Management": bootstrap["management_nonce"]}
                saved = await client.post("/api/easyapi/profiles/default", json={"base_url": "https://example.invalid", "api_key": "test-only-key"}, headers=headers)
                self.assertEqual(saved.status, 200)
                async with client.ws_connect("/api/easyapi/realtime?nonce=" + bootstrap["management_nonce"], headers={"Origin": origin}) as socket:
                    self.assertEqual((await socket.receive_json())["type"], "authenticated")
            finally:
                await client.close()
                await runtime.close()

    async def test_profiles_require_same_session_nonce_and_same_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            app = web.Application()
            app.add_routes(build_routes(runtime))
            client = TestClient(TestServer(app), cookie_jar=aiohttp.CookieJar(unsafe=True))
            await client.start_server()
            origin = str(client.make_url("")).rstrip("/")
            try:
                bootstrap = await (await client.get("/easyapi/bootstrap")).json()
                body = {"base_url": "https://example.invalid", "api_key": "private-test-key"}
                self.assertEqual((await client.post("/easyapi/profiles/default", json=body, headers={"Origin": origin})).status, 403)
                headers = {"Origin": origin, "X-EasyAPI-Management": bootstrap["management_nonce"]}
                saved = await client.post("/easyapi/profiles/default", json=body, headers=headers)
                self.assertEqual(saved.status, 200)
                self.assertNotIn("private-test-key", await saved.text())
                public = await (await client.get("/easyapi/profiles")).text()
                self.assertNotIn("private-test-key", public)
                headers["Origin"] = "https://attacker.invalid"
                self.assertEqual((await client.delete("/easyapi/profiles/default", headers=headers)).status, 403)
                self.assertTrue(runtime.profiles.get("default"))
            finally:
                await client.close()
                await runtime.close()
