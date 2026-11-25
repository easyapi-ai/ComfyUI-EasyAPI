"""Browser polling jobs are owner scoped and independent of panel lifetime."""
import asyncio
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from easyapi_comfy.runtime import Runtime
from easyapi_comfy.server import build_routes
from easyapi_comfy.tuning_jobs import get_tuning_jobs


class VideoTuningRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Runtime(Path(self.directory.name))
        self.addAsyncCleanup(self.runtime.close)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.events = 3
        self.failure = None
        self.calls = []
        self.secret = "private-fixture-key"
        self.runtime._clients["fixture"] = types.SimpleNamespace(api_key=self.secret, close=AsyncMock())

        async def run(value, locale="zh-CN", emit=None):
            self.calls.append((value, locale))
            try:
                for index in range(self.events):
                    await emit({"phase": "tuning", "type": "delta", "text": str(index), "api_key": self.secret})
                self.started.set()
                await self.release.wait()
                if self.failure:
                    raise RuntimeError(self.failure)
                return {"prompt": "A clean video prompt", "analysis": "Known facts", "authorization": self.secret,
                        "debug": "Filtered " + self.secret}
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

        self.patch = patch("easyapi_comfy.tuning_jobs._service", return_value=types.SimpleNamespace(run=run))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.browser, self.headers = await self.browser_for_runtime()

    async def browser_for_runtime(self):
        app = web.Application()
        app.add_routes(build_routes(self.runtime))
        browser = await self.enterAsyncContext(TestClient(TestServer(app)))
        session = await (await browser.get("/easyapi/bootstrap")).json()
        origin = str(browser.make_url("/")).rstrip("/")
        return browser, {"Origin": origin, "X-EasyAPI-Management": session["management_nonce"]}

    async def create(self, browser=None, headers=None, value=None):
        response = await (browser or self.browser).post("/easyapi/video-tuning", headers=headers or self.headers,
                                                      json=value if value is not None else {"scope_id": "scope", "nonce": 1, "prompt": "Original", "locale": "en"})
        self.assertEqual(response.status, 202)
        return (await response.json())["run_id"]

    async def read(self, run_id, after=0):
        response = await self.browser.get(f"/easyapi/video-tuning/{run_id}?after={after}", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return await response.json()

    async def test_incremental_polling_finishes_and_never_exposes_credentials(self):
        run_id = await self.create()
        await self.started.wait()
        running = await self.read(run_id, after=1)
        self.assertEqual(running["state"], "running")
        self.assertEqual(running["cursor"], 3)
        self.assertEqual([event["sequence"] for event in running["events"]], [2, 3])
        self.assertNotIn(self.secret, str(running))
        self.assertEqual(self.calls[0][1], "en")
        self.release.set()
        for _ in range(20):
            completed = await self.read(run_id, after=3)
            if completed["state"] == "completed":
                break
            await asyncio.sleep(0)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["events"], [])
        self.assertEqual(completed["result"]["prompt"], "A clean video prompt")
        self.assertNotIn("authorization", completed["result"])
        self.assertNotIn(self.secret, str(completed))

    async def test_all_routes_require_management_and_cross_owner_is_not_found(self):
        response = await self.browser.post("/easyapi/video-tuning", headers={"Origin": self.headers["Origin"]}, json={})
        self.assertEqual(response.status, 403)
        run_id = await self.create()
        await self.started.wait()
        for method in ("get", "delete"):
            response = await getattr(self.browser, method)(f"/easyapi/video-tuning/{run_id}")
            self.assertEqual(response.status, 403)
        other, headers = await self.browser_for_runtime()
        for method in ("get", "delete"):
            response = await getattr(other, method)(f"/easyapi/video-tuning/{run_id}", headers=headers)
            self.assertEqual(response.status, 404)
        self.assertFalse(self.cancelled.is_set())

    async def test_panel_disconnection_does_not_cancel_background_job(self):
        run_id = await self.create()
        await self.started.wait()
        await self.browser.close()
        self.assertFalse(self.cancelled.is_set())
        self.release.set()
        job = get_tuning_jobs(self.runtime).jobs[run_id]
        await job.task
        self.assertEqual(job.state, "completed")

    async def test_delete_explicitly_cancels_and_runtime_close_cleans_other_jobs(self):
        run_id = await self.create()
        await self.started.wait()
        response = await self.browser.delete(f"/easyapi/video-tuning/{run_id}", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["state"], "cancelled")
        self.assertTrue(self.cancelled.is_set())
        self.started.clear()
        next_id = await self.create(value={"scope_id": "other", "nonce": 1})
        await self.started.wait()
        await self.runtime.close()
        self.assertEqual(get_tuning_jobs(self.runtime).jobs[next_id].state, "cancelled")
        self.assertTrue(get_tuning_jobs(self.runtime).jobs[next_id].task.done())

    async def test_failure_event_bound_and_invalid_request_or_cursor(self):
        self.events = 300
        self.failure = "Fixture failure with " + self.secret
        run_id = await self.create()
        await self.started.wait()
        snapshot = await self.read(run_id)
        self.assertEqual(snapshot["cursor"], 300)
        self.assertEqual(len(snapshot["events"]), 256)
        self.assertEqual(snapshot["events"][0]["sequence"], 45)
        self.assertEqual(len((await self.read(run_id, after=298))["events"]), 2)
        for after in ("-1", "invalid"):
            response = await self.browser.get(f"/easyapi/video-tuning/{run_id}?after={after}", headers=self.headers)
            self.assertEqual(response.status, 400)
        response = await self.browser.post("/easyapi/video-tuning", headers=self.headers, json=[])
        self.assertEqual(response.status, 400)
        self.release.set()
        await get_tuning_jobs(self.runtime).jobs[run_id].task
        failed = await self.read(run_id, after=300)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("Fixture failure", failed["error"])
        self.assertNotIn(self.secret, failed["error"])


if __name__ == "__main__":
    unittest.main()
