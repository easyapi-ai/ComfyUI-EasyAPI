"""Long remote operations must reach ComfyUI's native node progress channel."""
import asyncio
from contextvars import ContextVar
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from easyapi_comfy.client import APIResponse
from test_realtime_nodes import load_nodes


class NodeProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_execution_reports_native_progress_while_gateway_is_pending(self):
        nodes = load_nodes()
        requested, release = asyncio.Event(), asyncio.Event()
        decoding, finish_decoding = asyncio.Event(), asyncio.Event()
        updates = []

        class ProgressBar:
            def __init__(self, total):
                self.total = total

            def update_absolute(self, value, total=None, preview=None):
                updates.append((value, self.total if total is None else total))

        async def request(*args, **kwargs):
            requested.set()
            await release.wait()
            return APIResponse(data={"data": []})

        async def decode(*args, **kwargs):
            decoding.set()
            await finish_decoding.wait()
            return {"images": object()}

        client = types.SimpleNamespace(profile_id="offline", request=request)
        with tempfile.TemporaryDirectory() as directory:
            runtime = types.SimpleNamespace(client=lambda _: client, output_dir=Path(directory))
            comfy = types.ModuleType("comfy")
            utils = types.ModuleType("comfy.utils")
            utils.ProgressBar = ProgressBar
            comfy.utils = utils
            with patch.dict("sys.modules", {"comfy": comfy, "comfy.utils": utils}), \
                    patch.object(nodes, "get_runtime", return_value=runtime), \
                    patch.object(nodes, "_interrupt"), \
                    patch.object(nodes, "prepare_request", new=AsyncMock(return_value=("/v1/images/generations", {}, [], {}))), \
                    patch.object(nodes, "validate_request", new=AsyncMock()), \
                    patch.object(nodes.media, "encode_inputs", new=AsyncMock(return_value=[])), \
                    patch.object(nodes.media, "decode_response", new=decode):
                pending = asyncio.create_task(nodes.EasyAPIImages.execute(model="offline-image", operation="image-generation"))
                try:
                    await asyncio.wait_for(requested.wait(), timeout=1)
                    self.assertEqual(updates, [(0, 1)], "The running image node never reached native ComfyUI progress")
                    self.assertFalse(pending.done())
                    release.set()
                    await asyncio.wait_for(decoding.wait(), timeout=1)
                    self.assertEqual(updates, [(0, 1)], "Downloading and decoding must not report premature completion")
                    self.assertFalse(pending.done())
                finally:
                    release.set()
                    finish_decoding.set()
                    await pending
                self.assertEqual(updates, [(0, 1), (1, 1)])

    async def test_failed_or_cancelled_remote_work_never_reports_completion(self):
        from easyapi_comfy.progress import native_progress

        updates = []

        class ProgressBar:
            def __init__(self, total):
                self.total = total

            def update_absolute(self, value):
                updates.append((value, self.total))

        utils = types.ModuleType("comfy.utils")
        utils.ProgressBar = ProgressBar
        for error in (RuntimeError("download failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__), patch.dict("sys.modules", {"comfy.utils": utils}):
                updates.clear()

                @native_progress
                async def failed():
                    raise error

                with self.assertRaises(type(error)):
                    await failed()
                self.assertEqual(updates, [(0, 1)])

    async def test_native_elapsed_feedback_is_targeted_and_stops_on_every_exit(self):
        from easyapi_comfy import progress

        context = ContextVar("executing", default=None)
        messages, updates = [], []
        clock = [0]
        ticked = asyncio.Event()

        class ProgressBar:
            def __init__(self, total):
                self.total = total

            def update_absolute(self, value):
                updates.append(value)

        def send(text, node_id, sid):
            messages.append((text, node_id, sid))
            clock[0] += 1
            if "运行中" in text and "已用时 1s" in text:
                ticked.set()

        utils = types.ModuleType("comfy.utils")
        utils.ProgressBar = ProgressBar
        execution_utils = types.ModuleType("comfy_execution.utils")
        execution_utils.get_executing_context = context.get
        server_module = types.ModuleType("server")
        server = types.SimpleNamespace(client_id="requesting-browser", send_progress_text=send, last_node_id="wrong-node")
        server_module.PromptServer = types.SimpleNamespace(instance=server)
        modules = {"comfy.utils": utils, "comfy_execution.utils": execution_utils, "server": server_module}

        with patch.dict("sys.modules", modules), patch.object(progress, "_STATUS_INTERVAL", .001), \
                patch.object(progress, "monotonic", side_effect=lambda: clock[0]):
            for outcome, final_text in (("success", "已完成"), ("error", "未完成"), ("cancel", "已停止")):
                with self.subTest(outcome=outcome):
                    messages.clear()
                    updates.clear()
                    clock[0] = 0
                    ticked.clear()
                    release = asyncio.Event()
                    context.set(types.SimpleNamespace(node_id="image-node"))
                    server.client_id = "requesting-browser"

                    @progress.native_progress
                    async def execute():
                        await release.wait()
                        if outcome == "error":
                            raise RuntimeError("decode failed")
                        return "output"

                    pending = asyncio.create_task(execute())
                    try:
                        await asyncio.wait_for(ticked.wait(), timeout=1)
                        # Another node/client became globally active while this
                        # request waited. Status must remain scoped to its origin.
                        server.client_id = "different-browser"
                        context.set(types.SimpleNamespace(node_id="different-node"))
                        self.assertEqual(updates, [0])
                        self.assertTrue(all(node == "image-node" and sid == "requesting-browser" for _, node, sid in messages))
                        if outcome == "cancel":
                            pending.cancel()
                        else:
                            release.set()
                        if outcome == "success":
                            self.assertEqual(await pending, "output")
                        else:
                            with self.assertRaises(asyncio.CancelledError if outcome == "cancel" else RuntimeError):
                                await pending
                        self.assertIn(final_text, messages[-1][0])
                        self.assertTrue(all(node == "image-node" and sid == "requesting-browser" for _, node, sid in messages))
                        count = len(messages)
                        await asyncio.sleep(.01)
                        self.assertEqual(len(messages), count, "Elapsed ticker leaked after node exit")
                        self.assertEqual(updates, [0, 1] if outcome == "success" else [0])
                    finally:
                        if not pending.done():
                            pending.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await pending

            server.client_id = None
            self.assertIsNone(progress._text_reporter(), "Missing client ID must not broadcast status")

    async def test_status_send_failures_preserve_output_and_original_errors(self):
        from easyapi_comfy import progress

        messages = []
        ticked = asyncio.Event()

        def broken_send(text, node_id, sid):
            messages.append(text)
            if len(messages) >= 2:
                ticked.set()
            raise RuntimeError("status transport unavailable")

        utils = types.ModuleType("comfy.utils")
        utils.ProgressBar = lambda _: types.SimpleNamespace(update_absolute=lambda _: None)
        execution_utils = types.ModuleType("comfy_execution.utils")
        execution_utils.get_executing_context = lambda: types.SimpleNamespace(node_id="image-node")
        server_module = types.ModuleType("server")
        server_module.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
            client_id="requesting-browser", send_progress_text=broken_send))
        modules = {"comfy.utils": utils, "comfy_execution.utils": execution_utils, "server": server_module}

        with patch.dict("sys.modules", modules), patch.object(progress, "_STATUS_INTERVAL", .001):
            for outcome in ("success", "error", "cancel"):
                with self.subTest(outcome=outcome):
                    messages.clear()
                    ticked.clear()
                    release = asyncio.Event()
                    original_error = ValueError("original generation error")

                    @progress.native_progress
                    async def execute():
                        await release.wait()
                        if outcome == "error":
                            raise original_error
                        return "generated output"

                    pending = asyncio.create_task(execute())
                    try:
                        # Both the initial send and a heartbeat fail before the
                        # operation finishes. The final send will fail as well.
                        await asyncio.wait_for(ticked.wait(), timeout=1)
                        if outcome == "cancel":
                            pending.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await pending
                        elif outcome == "error":
                            release.set()
                            with self.assertRaises(ValueError) as caught:
                                await pending
                            self.assertIs(caught.exception, original_error)
                        else:
                            release.set()
                            self.assertEqual(await pending, "generated output")
                        self.assertIn({"success": "已完成", "error": "未完成", "cancel": "已停止"}[outcome], messages[-1])
                        count = len(messages)
                        await asyncio.sleep(.01)
                        self.assertEqual(len(messages), count, "Failing status transport leaked its ticker")
                    finally:
                        if not pending.done():
                            pending.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await pending


if __name__ == "__main__":
    unittest.main()
