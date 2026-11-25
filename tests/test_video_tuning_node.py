"""Typed video tuning execution passes real context without submitting a video."""
import asyncio
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from easyapi_comfy.media import MediaSource
from easyapi_comfy.runtime import Runtime
from test_realtime_nodes import load_nodes


class VideoTuningNodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.nodes = load_nodes()
        # The shared lightweight Comfy stub predates Float inputs.
        if not hasattr(self.nodes.io, "Float"):
            self.nodes.io.Float = self.nodes.io.Int

    def test_schema_is_an_intermediate_node_with_prompt_analysis_and_result(self):
        node = self.nodes.EasyAPIVideoPromptTuner
        schema = node.define_schema()
        self.assertFalse(getattr(schema, "is_output_node", False))
        self.assertTrue(schema.has_intermediate_output)
        self.assertNotIn(self.nodes.OperationNode, node.__mro__)
        self.assertEqual([(entry.id, entry.display_name) for entry in schema.outputs],
                         [("prompt", "提示词"), ("analysis", "分析"), ("result", "完整结果")])
        names = {entry.id for entry in schema.inputs}
        self.assertTrue({"profile_id", "text_model", "analysis_model", "target_profile_id", "target_model",
                         "target_params_json", "prompt", "domain", "mode", "output", "analyze", "issue",
                         "issue_start", "issue_end", "scope_id", "revision", "references", "image", "video",
                         "audio", "diagnostic_video"} <= names)

    def test_fast_comfy_cache_rechecks_credentials_and_capabilities_without_random_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            runtime.profiles.save("fixture", "https://gateway.invalid", "mock-first-key", make_default=True)
            with patch.object(self.nodes, "get_runtime", return_value=runtime), patch("time.time", return_value=123456):
                first = self.nodes.EasyAPIVideoPromptTuner.fingerprint_inputs()
                self.assertEqual(first, self.nodes.EasyAPIVideoPromptTuner.fingerprint_inputs())
                self.assertEqual(len(first), 64)
                runtime.profiles.save("fixture", "https://gateway.invalid", "mock-second-key", make_default=True)
                rotated = self.nodes.EasyAPIVideoPromptTuner.fingerprint_inputs()
                self.assertNotEqual(first, rotated, "Credential changes must not reuse an old Comfy output")
                with patch("time.time", return_value=123456 + 301):
                    self.assertNotEqual(rotated, self.nodes.EasyAPIVideoPromptTuner.fingerprint_inputs(),
                                        "Capability refresh must reach the persistent service cache")

    async def test_execution_preserves_actual_inputs_and_returns_only_tuned_prompt_to_video(self):
        result = {"prompt": "Actor reads the page.", "analysis": "One clear action.", "changes": ["Simplified motion"],
                  "suggestedParams": {"duration": 8}, "risks": []}
        service = types.SimpleNamespace(run=AsyncMock(return_value=result))
        runtime = types.SimpleNamespace(_clients={}, _retired_clients=[])
        source = MediaSource("image", "/fixture/image.png", "first_frame", "image/png")
        diagnostic = MediaSource("video", Path("/fixture/result.mp4"), "reference_video", "video/mp4")
        encode = AsyncMock(side_effect=[[source], [diagnostic]])
        with patch.object(self.nodes, "get_runtime", return_value=runtime), patch.object(self.nodes, "_interrupt"), \
                patch.object(self.nodes, "_video_tuning_service", return_value=service), \
                patch.object(self.nodes.media, "encode_inputs", new=encode):
            output = await self.nodes.EasyAPIVideoPromptTuner.execute(
                profile_id="text-profile", text_model="chat-model", analysis_model="vision-model",
                target_profile_id="video-profile", target_model="wan3.0-video", target_params_json='{"duration":12,"seed":0,"watermark":false}',
                prompt="Read a page", domain="drama", mode="first-frame", output="diagnose", issue="Hand changed shape",
                issue_start=1.5, issue_end=3, locale="en", scope_id="node-scope", revision=7,
                references=[source], image="native-image", video="native-video", audio="native-audio", diagnostic_video="result-video")
        value = service.run.call_args.args[0]
        self.assertEqual(value["scope_id"], "node-scope")
        self.assertEqual(value["nonce"], 7)
        self.assertEqual(value["model"], "chat-model")
        self.assertEqual(value["target_model"], "wan3.0-video")
        self.assertEqual(value["params"], {"duration": 12, "seed": 0, "watermark": False})
        self.assertEqual(value["media"], [source])
        self.assertEqual(value["issue"], {"description": "Hand changed shape", "start": 1.5, "end": 3.0})
        self.assertEqual(value["resultMedia"][0]["role"], "result_video")
        self.assertTrue(value["resultMedia"][0]["analysisOnly"])
        self.assertEqual(value["resultMedia"][0]["path"], str(diagnostic.path))
        self.assertEqual(encode.call_args_list[0].kwargs,
                         {"image": "native-image", "video": "native-video", "audio": "native-audio", "references": [source]})
        self.assertEqual(encode.call_args_list[1].kwargs, {"video": "result-video"})
        self.assertEqual(output.args, (result["prompt"], result["analysis"], result))
        self.assertEqual(output.ui["easyapi_tuning"], [result])
        self.assertEqual(output.ui["easyapi_text"], [result["prompt"]])
        self.assertEqual(service.run.call_args.kwargs["locale"], "en")

    async def test_unset_range_stable_revision_and_result_redaction(self):
        secret = "private-fixture-key"
        runtime = types.SimpleNamespace(_clients={"fixture": types.SimpleNamespace(api_key=secret)}, _retired_clients=[])
        result = {"prompt": "Keep the scene", "analysis": {"facts": ["Known source"]}, "api_key": secret,
                  "debug": "No credentials: " + secret}
        service = types.SimpleNamespace(run=AsyncMock(return_value=result))
        with patch.object(self.nodes, "get_runtime", return_value=runtime), patch.object(self.nodes, "_interrupt"), \
                patch.object(self.nodes, "_video_tuning_service", return_value=service), \
                patch.object(self.nodes.media, "encode_inputs", new=AsyncMock(return_value=[])):
            first = await self.nodes.EasyAPIVideoPromptTuner.execute(text_model="chat", prompt="Scene", scope_id="scope", revision=2)
            second = await self.nodes.EasyAPIVideoPromptTuner.execute(text_model="chat", prompt="Scene", scope_id="scope", revision=2)
        self.assertEqual(service.run.call_args_list[0].args[0], service.run.call_args_list[1].args[0])
        self.assertIsNone(service.run.call_args.args[0]["issue"])
        self.assertNotIn(secret, str(first.args) + str(first.ui))
        self.assertNotIn("api_key", first.args[2])
        self.assertIn("Known source", first.args[1])
        self.assertEqual(first.args, second.args)

    async def test_invalid_scope_revision_or_diagnostic_range_never_calls_model(self):
        for value in ({"scope_id": ""}, {"revision": -1}, {"revision": True},
                      {"issue_start": 0, "issue_end": -1}, {"issue_start": 2, "issue_end": 1},
                      {"issue_start": float("nan")}, {"issue": {"text": "invalid"}}):
            with self.subTest(value=value), patch.object(self.nodes, "_video_tuning_service") as service:
                kwargs = {"scope_id": "scope", "prompt": "Scene", "text_model": "chat"} | value
                with self.assertRaises(ValueError):
                    await self.nodes.EasyAPIVideoPromptTuner.execute(**kwargs)
                service.assert_not_called()

    async def test_comfy_interrupt_cancels_its_tuning_waiter(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def run(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        def interrupt():
            if started.is_set():
                raise RuntimeError("fixture interrupted")

        with patch.object(self.nodes, "_video_tuning_service", return_value=types.SimpleNamespace(run=run)), \
                patch.object(self.nodes, "_interrupt", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "fixture interrupted"):
                await self.nodes._await_video_tuning(object(), {"scope_id": "scope"}, "en")
        self.assertTrue(cancelled.is_set())

    async def test_runtime_close_orders_jobs_service_and_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            order = []

            async def record(name):
                order.append(name)

            runtime._video_tuning_jobs = types.SimpleNamespace(close=lambda: record("jobs"))
            runtime._video_tuning_service = types.SimpleNamespace(close=lambda: record("service"))
            runtime._clients = {"fixture": types.SimpleNamespace(close=lambda: record("client"))}
            await runtime.close()
            self.assertEqual(order, ["jobs", "service", "client"])


if __name__ == "__main__":
    unittest.main()
