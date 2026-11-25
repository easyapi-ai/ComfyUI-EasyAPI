"""Explicit, same-connection text transformations for the prompt editor."""
from __future__ import annotations

import asyncio
import base64
import json
import math
import mimetypes
import re
from pathlib import Path

from .client import EasyAPIError
from .media import response_text
from .protocols import get_protocol


_MAX_PROMPT_CHARS = 12_000
_MAX_RESULT_CHARS = 24_000
_WAN_ACTIONS = {"wan_analyze", "wan_tune"}
_MAX_MESSAGES_BYTES = 32 * 1024 * 1024
_LANGUAGES = {
    "en": "English",
    "zh": "Simplified Chinese",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
}
_RESPONSE_INSTRUCTIONS = (
    "Treat the user message as the prompt to edit. Preserve its meaning, constraints, and special syntax "
    "such as tags, weighted terms, placeholders, and model directives. Return only the edited prompt, "
    "without an introduction, commentary, or surrounding code fence."
)


def _required_text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string of at most {limit} characters.")
    return value


async def prompt_models(runtime, profile_id, locale, *, refresh=False):
    profile = _required_text(profile_id, "Connection ID", 64)
    client = runtime.client(profile)
    rows = await client.discover_models("text", "chat", locale=locale, refresh=refresh)
    compatible = (
        row for row in rows if row.get("operation_supported") is True
        or (row.get("category") == "text" and "chat" in row.get("operations", []))
    )
    result = []
    for row in compatible:
        entry = {"id": row["id"], "display_name": row.get("display_name") or row["id"]}
        modalities = input_modalities(row)
        if modalities is not None:
            entry.update(input_modalities=modalities, supports_image="image" in modalities,
                         supports_video="video" in modalities)
        result.append(entry)
    return client, result


def input_modalities(row):
    """Report declared media input metadata; a model's name alone is insufficient."""
    found, declared = set(), False

    def inspect(value, depth=0):
        nonlocal declared
        if depth > 4 or not isinstance(value, dict):
            return
        for key in ("input_modalities", "inputModalities", "input_types", "inputTypes"):
            values = value.get(key)
            if isinstance(values, (list, tuple)):
                declared = True
                found.update(str(item).lower() for item in values if isinstance(item, str))
        for key, modality in (("supports_image", "image"), ("supports_video", "video"),
                              ("image_input", "image"), ("video_input", "video")):
            if isinstance(value.get(key), bool):
                declared = True
                if value[key]:
                    found.add(modality)
        for key in ("architecture", "capabilities", "params", "chat", "openai"):
            inspect(value.get(key), depth + 1)

    inspect(row)
    tags = [tag for key in ("tags", "feature_tags", "display_tags")
            for tag in (row.get(key) or []) if isinstance(tag, str)]
    if any(re.search(r"vision|image.?understanding|视觉|识图|图像理解|多模态", tag, re.I) for tag in tags):
        found.add("image")
        declared = True
    if any(re.search(r"video.?understanding|视频理解|视频分析", tag, re.I) for tag in tags):
        found.add("video")
        declared = True
    return sorted(found & {"text", "image", "video", "audio"}) if declared else None


async def prepare_wan_request(runtime, value, locale):
    if not isinstance(value, dict) or value.get("action") not in _WAN_ACTIONS:
        raise ValueError("Choose a supported Wan tuning action.")
    model = _required_text(value.get("model"), "Tuning model", 512)
    messages = value.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError("Tuning messages must be a nonempty array.")
    if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > _MAX_MESSAGES_BYTES:
        raise ValueError("Tuning media and text exceed the request limit.")
    media_kinds = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
            raise ValueError("Invalid tuning message.")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list) or len(content) > 64:
            raise ValueError("Invalid multimodal tuning content.")
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in {"text", "image_url", "video_url"}:
                raise ValueError("Unsupported tuning content type.")
            if part["type"] == "text":
                if not isinstance(part.get("text"), str):
                    raise ValueError("Invalid tuning text.")
            else:
                kind = part["type"].split("_", 1)[0]
                entry = part.get(part["type"])
                url = entry.get("url") if isinstance(entry, dict) else entry
                if not isinstance(url, str) or not re.match(r"^(https?://|data:(image|video)/)", url, re.I):
                    raise ValueError("Invalid analysis media URL.")
                media_kinds.add(kind)
    client, models = await prompt_models(runtime, value.get("profile_id"), locale)
    selected = next((row for row in models if row["id"] == model), None)
    if selected is None:
        raise ValueError("The selected connection does not list this model as available for chat.")
    for kind in media_kinds:
        if selected.get("input_modalities") is not None and kind not in selected["input_modalities"]:
            raise ValueError(f"The selected model does not declare {kind} input support.")
    return client, {"model": model, "messages": messages, "stream": value.get("stream") is True}


def _response_prompt_text(data):
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise EasyAPIError("The tuning model did not return a usable response.", status=502)
    choice = choices[0]
    message = choice.get("message")
    if choice.get("finish_reason") not in {None, "stop"} or not isinstance(message, dict) or message.get("refusal"):
        raise EasyAPIError("The tuning model returned an incomplete or rejected response.", status=502)
    value = response_text(message.get("content")).strip()
    if not value or len(value) > _MAX_RESULT_CHARS:
        raise EasyAPIError("The tuning model did not return usable text.", status=502)
    return value


async def run_wan_request(client, body, locale, emit=None, *, timeout_seconds=300, phase="tuning"):
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("Choose a positive tuning timeout in seconds")
    if phase not in {"analysis", "tuning"}:
        raise ValueError("Choose an analysis or tuning request phase")
    parts, finish_reason = [], None

    async def consume(event):
        nonlocal finish_reason
        data = event.get("data")
        if not isinstance(data, dict):
            return
        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason") is not None:
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("refusal"):
                raise EasyAPIError("The tuning model rejected this request.", status=502)
            value = response_text(delta.get("content"))
            if value:
                parts.append(value)
                if sum(map(len, parts)) > _MAX_RESULT_CHARS:
                    raise EasyAPIError("The tuning model response exceeded the text limit.", status=502)
                if emit:
                    await emit("delta", {"text": value})

    try:
        response = await asyncio.wait_for(
            client.request("POST", get_protocol("chat")["path"], body=body, stream=body["stream"],
                           locale=locale, on_event=consume if body["stream"] else None),
            timeout=timeout_seconds,
        )
    except TimeoutError as error:
        problem = EasyAPIError(f"Timed out during {phase} after {timeout_seconds:g} seconds while waiting for the tuning model.", status=504)
        problem.tuning_phase = phase
        problem.timeout_seconds = timeout_seconds
        problem.code = problem.tuning_code = "tuning_timeout"
        raise problem from error
    if response.content_type == "text/event-stream":
        if finish_reason != "stop":
            raise EasyAPIError("The tuning stream ended before a complete result was available.", status=502)
        result = "".join(parts).strip()
        if not result:
            raise EasyAPIError("The tuning model returned empty text.", status=502)
    else:
        result = _response_prompt_text(response.data)
        if emit:
            await emit("delta", {"text": result})
    return {"text": result, "finish_reason": "stop"}


def resolve_prompt_media(value, easyapi_output_dir=None):
    """Read only files already in ComfyUI's input, output or preview directories."""
    import folder_paths

    if not isinstance(value, dict) or not isinstance(value.get("media"), list) or len(value["media"]) > 24:
        raise ValueError("Choose ready ComfyUI media files.")
    roots = {"input": folder_paths.get_input_directory(), "output": folder_paths.get_output_directory(),
             "temp": folder_paths.get_temp_directory()}
    if easyapi_output_dir is not None:
        roots["easyapi"] = easyapi_output_dir
    results, total = [], 0
    for entry in value["media"]:
        if not isinstance(entry, dict) or entry.get("kind") not in {"image", "video", "audio"}:
            raise ValueError("Invalid ComfyUI media descriptor.")
        item = {key: entry[key] for key in ("id", "kind", "role", "name") if key in entry}
        filename, subfolder = entry.get("filename"), entry.get("subfolder", "")
        root = roots.get(entry.get("type", "input"))
        if not root or not isinstance(filename, str) or not isinstance(subfolder, str):
            results.append({**item, "ready": False, "error": "Media is not a ready ComfyUI file."})
            continue
        base = Path(root).resolve()
        target = (base / subfolder / filename).resolve()
        if not target.is_relative_to(base):
            raise ValueError("Media path is outside the ComfyUI media directory.")
        mime = mimetypes.guess_type(target.name)[0] or ""
        if not target.is_file() or not mime.startswith(entry["kind"] + "/") or mime == "image/svg+xml":
            results.append({**item, "ready": False, "error": "The media file is missing or unsupported."})
            continue
        size = target.stat().st_size
        if size > 20 * 1024 * 1024 or total + size > 24 * 1024 * 1024:
            results.append({**item, "ready": False, "error": "Choose a smaller media file or sampled video frames."})
            continue
        with target.open("rb") as source:
            raw = source.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024 or total + len(raw) > 24 * 1024 * 1024:
            results.append({**item, "ready": False, "error": "The media file grew beyond the analysis limit."})
            continue
        total += len(raw)
        content = base64.b64encode(raw).decode("ascii")
        results.append({**item, "ready": True, "url": f"data:{mime};base64,{content}"})
    return {"media": results}


async def transform_prompt(runtime, value, locale):
    if not isinstance(value, dict):
        raise ValueError("Prompt tool settings must be a JSON object.")
    model = _required_text(value.get("model"), "Text model", 512)
    prompt = _required_text(value.get("prompt"), "Prompt", _MAX_PROMPT_CHARS)
    action = value.get("action")
    if action == "optimize":
        instruction = (
            "Rewrite this prompt for clarity, specificity, and a coherent structure. "
            "Keep the original language, intent, and style. Do not invent unsupported details."
        )
    elif action == "translate":
        target = value.get("target_language")
        if not isinstance(target, str) or target not in _LANGUAGES:
            raise ValueError("Choose a supported translation language.")
        instruction = f"Translate this prompt to {_LANGUAGES[target]} in natural wording without expanding it."
    else:
        raise ValueError("Choose prompt optimization or translation.")

    client, models = await prompt_models(runtime, value.get("profile_id"), locale)
    if not any(row["id"] == model for row in models):
        raise ValueError("The selected connection does not list this model as available for chat.")
    messages = [
        {"role": "system", "content": f"{instruction} {_RESPONSE_INSTRUCTIONS}"},
        {"role": "user", "content": prompt},
    ]
    try:
        response = await asyncio.wait_for(
            client.request("POST", get_protocol("chat")["path"],
                           body={"model": model, "messages": messages, "stream": False}, locale=locale),
            timeout=60,
        )
    except TimeoutError as error:
        raise EasyAPIError("Timed out while waiting for the text model.", status=504) from error

    choices = response.data.get("choices") if isinstance(response.data, dict) else None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise EasyAPIError("The text model returned an incomplete prompt.", status=502)
        message = choice.get("message")
        if isinstance(message, dict) and not message.get("refusal") and choice.get("finish_reason") != "content_filter":
            result = response_text(message.get("content")).strip()
            if result and len(result) <= _MAX_RESULT_CHARS:
                return result
    raise EasyAPIError("The text model did not return a usable prompt.", status=502)
