import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyapi_comfy.client import APIResponse
from easyapi_comfy.media import decode_response, encode_inputs, response_text
from easyapi_comfy.runtime import Runtime


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_and_mask_use_real_tensors_and_openai_alpha_convention(self):
        import numpy as np
        import torch
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            image = torch.zeros((1, 3, 4, 3), dtype=torch.float32)
            image[:, :, :, 0] = 1
            mask = torch.zeros((1, 3, 4))
            mask[:, 0, :] = 1
            with patch("easyapi_comfy.runtime.get_runtime", return_value=runtime):
                sources = await encode_inputs(image=image, mask=mask)
            self.assertEqual([s.kind for s in sources], ["image", "mask"])
            with Image.open(sources[1].path) as rgba:
                self.assertEqual(np.asarray(rgba)[0, 0, 3], 0)
                self.assertEqual(np.asarray(rgba)[1, 0, 3], 255)
            data = base64.b64encode(Path(sources[0].path).read_bytes()).decode()
            decoded = await decode_response(APIResponse(data={"data": [{"b64_json": data}]}), None, Path(directory) / "out")
            self.assertEqual(tuple(decoded["images"].shape), (1, 3, 4, 3))
            self.assertTrue(torch.all(decoded["images"][0, :, :, 0] == 1))
            self.assertTrue(Path(decoded["files"][0]).is_file())

    async def test_waveform_round_trip_preserves_channels_rate_and_sample_count(self):
        import torch
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory)
            waveform = torch.stack([torch.linspace(-.5, .5, 800), torch.linspace(.5, -.5, 800)]).unsqueeze(0)
            with patch("easyapi_comfy.runtime.get_runtime", return_value=runtime):
                source = (await encode_inputs(audio={"waveform": waveform, "sample_rate": 16000}))[0]
            response = APIResponse(content=Path(source.path).read_bytes(), content_type="audio/wav")
            decoded = await decode_response(response, None, Path(directory) / "out")
            self.assertEqual(decoded["audio"]["sample_rate"], 16000)
            self.assertEqual(tuple(decoded["audio"]["waveform"].shape), (1, 2, 800))
            self.assertTrue(torch.allclose(waveform, decoded["audio"]["waveform"], atol=1e-4))

    async def test_mixed_image_sizes_keep_files_without_fabricating_a_tensor(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            data = []
            for size in ((3, 4), (4, 5)):
                content = io.BytesIO()
                Image.new("RGB", size).save(content, format="PNG")
                data.append({"b64_json": base64.b64encode(content.getvalue()).decode()})
            decoded = await decode_response(APIResponse(data={"data": data}), None, directory)
            self.assertIsNone(decoded["images"])
            self.assertEqual(len(decoded["files"]), 2)
            self.assertTrue(decoded["warnings"])

    async def test_response_text_includes_streamed_native_deltas(self):
        value = {"events": [{"data": {"choices": [{"delta": {"content": "a"}}]}},
                            {"data": {"delta": {"text": "b"}}},
                            {"data": {"candidates": [{"content": {"parts": [{"text": "c"}]}}]}}]}
        self.assertEqual(response_text(value), "abc")
