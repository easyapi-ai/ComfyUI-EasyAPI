"""Local-first media conversion; torch, numpy and av are imported only on demand."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import mimetypes
import os
import shutil
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .client import APIResponse


@dataclass
class MediaSource:
    kind: str
    path: str | Path | None = None
    role: str | None = None
    mime: str | None = None
    url: str | None = None


def _new_path(directory, suffix):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (uuid.uuid4().hex + suffix)


async def encode_inputs(image=None, mask=None, audio=None, video=None, references=None):
    from .runtime import get_runtime
    directory = get_runtime().input_dir
    sources = []
    if image is not None:
        def save_images():
            import numpy as np
            from PIL import Image
            array = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
            if array.ndim == 3:
                array = array[None]
            if array.ndim != 4 or array.shape[-1] not in {1, 3, 4}:
                raise ValueError("IMAGE must be a [batch, height, width, channels] tensor")
            result = []
            for item in array:
                path = _new_path(directory, ".png")
                pixels = np.clip(item * 255.0, 0, 255).round().astype(np.uint8)
                if pixels.shape[-1] == 1:
                    pixels = pixels[:, :, 0]
                Image.fromarray(pixels).save(path)
                result.append(MediaSource("image", str(path), "reference_image", "image/png"))
            return result
        sources.extend(await asyncio.to_thread(save_images))
    if mask is not None:
        def save_masks():
            import numpy as np
            from PIL import Image
            array = mask.detach().cpu().numpy() if hasattr(mask, "detach") else np.asarray(mask)
            if array.ndim == 2:
                array = array[None]
            if array.ndim != 3:
                raise ValueError("MASK must be a [batch, height, width] tensor")
            result = []
            for item in array:
                path = _new_path(directory, ".png")
                pixels = np.full((*item.shape, 4), 255, dtype=np.uint8)
                pixels[:, :, 3] = np.clip((1 - item) * 255, 0, 255).round().astype(np.uint8)
                Image.fromarray(pixels).save(path)
                result.append(MediaSource("mask", str(path), None, "image/png"))
            return result
        sources.extend(await asyncio.to_thread(save_masks))
    if audio is not None:
        def save_audio():
            import numpy as np
            waveform = audio["waveform"]
            array = waveform.detach().cpu().numpy() if hasattr(waveform, "detach") else np.asarray(waveform)
            if array.ndim == 2:
                array = array[None]
            if array.ndim != 3 or not int(audio.get("sample_rate", 0)) > 0:
                raise ValueError("AUDIO requires waveform [batch, channels, samples] and sample_rate")
            result = []
            for item in array:
                path = _new_path(directory, ".wav")
                samples = (np.clip(item.T, -1, 1) * 32767).round().astype("<i2")
                with wave.open(str(path), "wb") as stream:
                    stream.setnchannels(item.shape[0])
                    stream.setsampwidth(2)
                    stream.setframerate(int(audio["sample_rate"]))
                    stream.writeframes(samples.tobytes())
                result.append(MediaSource("audio", str(path), "reference_audio", "audio/wav"))
            return result
        sources.extend(await asyncio.to_thread(save_audio))
    if video is not None:
        path = _new_path(directory, ".mp4")
        if isinstance(video, (str, Path)):
            await asyncio.to_thread(shutil.copyfile, str(video), path)
        elif hasattr(video, "save_to"):
            await asyncio.to_thread(video.save_to, str(path))
        else:
            raise ValueError("VIDEO must be a ComfyUI video object or local path")
        sources.append(MediaSource("video", str(path), "reference_video", "video/mp4"))
    if references:
        if isinstance(references, (MediaSource, dict, str, Path)):
            references = [references]
        for entry in references:
            if isinstance(entry, MediaSource):
                sources.append(entry)
            elif isinstance(entry, dict):
                normalized = {k: entry[k] for k in ("kind", "path", "role", "mime", "url") if k in entry}
                normalized.setdefault("kind", entry.get("type", "file"))
                sources.append(MediaSource(**normalized))
            elif isinstance(entry, (str, Path)):
                value = str(entry)
                mime = mimetypes.guess_type(value)[0] or "application/octet-stream"
                kind = mime.split("/", 1)[0] if mime.split("/", 1)[0] in {"image", "audio", "video"} else "file"
                sources.append(MediaSource(kind, None if value.startswith(("https://", "http://", "data:")) else value,
                                           "reference_" + kind, mime, value if value.startswith(("https://", "http://", "data:")) else None))
            else:
                raise ValueError("Reference must be a MediaSource, record or path")
    return sources


def _media_kind(url, hint=None):
    ext = Path(urlsplit(str(url)).path).suffix.lower()
    if ext in {".png", ".jpeg", ".jpg", ".webp", ".gif", ".bmp", ".avif"}:
        return "image"
    if ext in {".mp4", ".webm", ".mov", ".mkv", ".m4v"}:
        return "video"
    if ext in {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".pcm"}:
        return "audio"
    return hint or "file"


def _collect_media(value):
    found = []
    def add(url, kind=None):
        if isinstance(url, str) and url.startswith(("http://", "https://", "/", "data:")):
            found.append((url, _media_kind(url, kind)))
    def walk(node, hint=None):
        if isinstance(node, list):
            for child in node:
                walk(child, hint)
        elif isinstance(node, dict):
            type_hint = str(node.get("type") or "").lower()
            if "image" in type_hint:
                hint = "image"
            elif "audio" in type_hint:
                hint = "audio"
            elif "video" in type_hint:
                hint = "video"
            # URL responses also carry an empty b64_json placeholder.
            if isinstance(node.get("b64_json"), str) and node["b64_json"].strip():
                found.append(("data:image/png;base64," + node["b64_json"], "image"))
            if hint == "audio" and isinstance(node.get("data"), str) and node["data"].strip():
                fmt = str(node.get("format") or "mp3")
                found.append((f"data:audio/{fmt};base64," + node["data"], "audio"))
            for key, child in node.items():
                lower = key.lower()
                child_hint = "image" if lower in {"image", "images", "image_url", "imageurl", "image_urls", "imageurl"} else \
                    "audio" if lower in {"audio", "audio_url", "audiourl", "audio_urls"} else \
                    "video" if lower in {"video", "videos", "video_url", "videourl", "video_urls", "download_url"} else hint
                if lower in {"url", "download_url", "imageurl", "videourl", "audiourl", "image_url", "video_url", "audio_url"}:
                    if isinstance(child, str):
                        add(child, child_hint)
                    elif isinstance(child, dict):
                        walk(child, child_hint)
                elif lower in {"images", "image_urls", "video_urls", "audio_urls"} and isinstance(child, list):
                    for item in child:
                        add(item, child_hint) if isinstance(item, str) else walk(item, child_hint)
                elif lower not in {"b64_json", "request", "input", "input_references", "prompt"}:
                    walk(child, child_hint)
    walk(value)
    seen = set()
    return [(u, k) for u, k in found if not (u in seen or seen.add(u))]


def response_text(value):
    fragments = []
    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key in {"text", "output_text", "transcript"} and isinstance(child, str):
                    fragments.append(child)
                elif key == "content" and isinstance(child, str):
                    fragments.append(child)
                elif key not in {"request", "input", "input_references", "prompt", "usage"}:
                    walk(child)
    walk(value)
    if isinstance(value, str):
        return value
    return "".join(fragments)


def _decode_image_files(paths):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    images, masks = [], []
    for path in paths:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
            alpha = np.asarray(image.convert("RGBA"), dtype=np.float32)[:, :, 3] / 255
            images.append(torch.from_numpy(rgb.copy()).unsqueeze(0))
            masks.append(torch.from_numpy((1 - alpha).copy()).unsqueeze(0))
    if images and any(item.shape != images[0].shape for item in images):
        return None, None
    return (torch.cat(images), torch.cat(masks)) if images else (None, None)


def _decode_audio_file(path, sample_rate=None, channels=1):
    import av
    import numpy as np
    import torch
    options = {"sample_rate": str(sample_rate), "channels": str(channels)} if sample_rate else None
    with av.open(str(path), format="s16le" if sample_rate and Path(path).suffix == ".pcm" else None, options=options) as container:
        stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if stream is None:
            raise ValueError("Response file contains no audio stream")
        sample_rate = stream.rate or 24000
        resampler = av.AudioResampler(format="fltp", layout=stream.layout, rate=sample_rate)
        chunks = []
        for frame in container.decode(stream):
            chunks.extend(item.to_ndarray() for item in resampler.resample(frame))
        chunks.extend(item.to_ndarray() for item in resampler.resample(None))
        if not chunks:
            raise ValueError("Response audio is empty")
        waveform = torch.from_numpy(np.concatenate(chunks, axis=1).copy()).unsqueeze(0)
        return {"waveform": waveform, "sample_rate": sample_rate}


def _native_video(path):
    try:
        from comfy_api.latest import InputImpl
        return InputImpl.VideoFromFile(str(path))
    except ImportError:
        try:
            from comfy_api.latest._input_impl.video_types import VideoFromFile
        except ImportError:
            return None
    return VideoFromFile(str(path))


async def decode_response(response, client, output_dir):
    if not isinstance(response, APIResponse):
        response = APIResponse(data=response)
    result = {"images": None, "mask": None, "audio": None, "video": None, "video_url": "", "files": [], "urls": [],
              "text": response_text(response.data), "json": response.data, "warnings": []}
    assets = _collect_media(response.data)
    image_paths, audio_paths, video_paths = [], [], []
    if response.content:
        mime = response.content_type.split(";", 1)[0]
        kind = mime.split("/", 1)[0]
        path = _new_path(output_dir, mimetypes.guess_extension(mime) or ".bin")
        await asyncio.to_thread(path.write_bytes, response.content)
        result["files"].append(str(path))
        (image_paths if kind == "image" else audio_paths if kind == "audio" else video_paths if kind == "video" else []).append(path)
    for url, kind in assets:
        if url.startswith("data:"):
            header, separator, payload = url.partition(",")
            if not separator or ";base64" not in header:
                raise ValueError("Unsupported inline media encoding")
            try:
                raw = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid base64 media response") from exc
            if not raw:
                raise ValueError("Empty inline media response")
            suffix = mimetypes.guess_extension(header[5:].split(";", 1)[0]) or ".bin"
            path = _new_path(output_dir, suffix)
            await asyncio.to_thread(path.write_bytes, raw)
        else:
            result["urls"].append(url)
            suffix = Path(urlsplit(url).path).suffix
            if not suffix or len(suffix) > 7:
                suffix = {"image": ".png", "audio": ".mp3", "video": ".mp4"}.get(kind, ".bin")
            path = _new_path(output_dir, suffix)
            await client.download(url, path)
        result["files"].append(str(path))
        if kind == "video" and not video_paths and not url.startswith("data:"):
            result["video_url"] = client.base_url + url if url.startswith("/") else url
        (image_paths if kind == "image" else audio_paths if kind == "audio" else video_paths if kind == "video" else []).append(path)
    if image_paths:
        result["images"], result["mask"] = await asyncio.to_thread(_decode_image_files, image_paths)
        if result["images"] is None:
            result["warnings"].append("Images have different dimensions; original files are available without resizing")
    if audio_paths:
        audio_data = response.data.get("audio", {}) if isinstance(response.data, dict) else {}
        rate = audio_data.get("sample_rate") if isinstance(audio_data, dict) else None
        if audio_paths[0].suffix == ".pcm" and not rate:
            result["warnings"].append("Raw PCM has no sample rate; original audio is available in files")
        else:
            result["audio"] = await asyncio.to_thread(_decode_audio_file, audio_paths[0], rate)
        if len(audio_paths) > 1:
            result["warnings"].append("AUDIO output contains the first clip; all clips are available in files")
    if video_paths:
        result["video"] = _native_video(video_paths[0])
        if result["video"] is None:
            result["warnings"].append("ComfyUI video API unavailable; original video files remain available")
    return result
