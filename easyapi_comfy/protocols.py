"""Explicit user-side operations. No dashboard or administrator API proxy."""
from __future__ import annotations

import copy
import re
from urllib.parse import unquote, urlsplit


def _op(id, path, family, *, method="POST", transport="json", polling=None, **extra):
    value = {"id": id, "label": id.replace("-", " ").title(), "method": method,
             "path": path, "family": family, "transport": transport, **extra}
    if polling:
        value.update({"async": True, "polling": polling})
    return value


VIDEO_POLL = {"method": "GET", "path": "/v1/videos/{task_id}",
              "id_fields": ["id", "task_id", "data.task_id"], "status_field": "data.status"}
IMAGE_POLL = {"method": "GET", "path": "/v1/images/tasks/{task_id}",
              "id_fields": ["data.task_id", "task_id", "id"], "status_field": "data.status"}
MJ_POLL = {"method": "GET", "path": "/mj/task/{task_id}/fetch",
           "id_fields": ["result", "task_id", "id"], "status_field": "status"}
SUNO_POLL = {"method": "GET", "path": "/suno/fetch/{task_id}",
             "id_fields": ["data.task_id", "task_id", "data", "id"], "status_field": "data.status"}

PROTOCOLS = [
    _op("chat", "/v1/chat/completions", "text", streaming=True),
    _op("responses", "/v1/responses", "text", streaming=True),
    _op("responses-compact", "/v1/responses/compact", "text"),
    _op("messages", "/v1/messages", "text", streaming=True),
    _op("messages-count-tokens", "/v1/messages/count_tokens", "text"),
    _op("completions", "/v1/completions", "text", streaming=True),
    _op("edits", "/v1/edits", "image", transport="multipart", media_fields={"image": "image", "mask": "mask"}),
    _op("custom-user-request", "/v1/chat/completions", "utility"),
    _op("alpha-search", "/v1/alpha/search", "text"),
    _op("embeddings", "/v1/embeddings", "embedding"),
    _op("engine-embeddings", "/v1/engines/{model}/embeddings", "embedding"),
    _op("rerank", "/v1/rerank", "rerank"),
    _op("moderations", "/v1/moderations", "text"),
    _op("gemini-generate", "/v1beta/models/{model}:generateContent", "text"),
    _op("gemini-stream", "/v1beta/models/{model}:streamGenerateContent", "text", streaming=True, query={"alt": "sse"}),
    _op("gemini-compatible-generate", "/v1/models/{model}:generateContent", "text"),
    _op("gemini-compatible-stream", "/v1/models/{model}:streamGenerateContent", "text", streaming=True, query={"alt": "sse"}),
    _op("gemini-models", "/v1beta/models", "utility", method="GET"),
    _op("gemini-compatible-models", "/v1beta/openai/models", "utility", method="GET"),
    _op("image-generation", "/v1/images/generations", "image", media_fields={"image": "images"}),
    _op("image-edit", "/v1/images/edits", "image", transport="multipart", media_fields={"image": "image", "mask": "mask"}),
    _op("image-task", "/v1/images/tasks", "image", polling=IMAGE_POLL, media_fields={"image": "images"}),
    _op("image-task-edit", "/v1/images/tasks", "image", transport="multipart", polling=IMAGE_POLL, media_fields={"image": "image", "mask": "mask"}),
    _op("video-generation", "/v1/videos", "video", polling=VIDEO_POLL, media_fields={"image": "input_references", "audio": "input_references", "video": "input_references"}),
    _op("video-generations", "/v1/video/generations", "video", polling={**VIDEO_POLL, "path": "/v1/video/generations/{task_id}"}),
    _op("video-remix", "/v1/videos/{task_id}/remix", "video", polling=VIDEO_POLL),
    _op("video-fetch", "/v1/videos/{task_id}", "video", method="GET"),
    _op("video-generations-fetch", "/v1/video/generations/{task_id}", "video", method="GET"),
    _op("video-content", "/v1/videos/{task_id}/content", "video", method="GET", transport="binary"),
    _op("image-task-fetch", "/v1/images/tasks/{task_id}", "image", method="GET"),
    _op("image-task-fetch-post", "/v1/images/tasks/{task_id}", "image"),
    _op("audio-speech", "/v1/audio/speech", "audio"),
    _op("audio-transcriptions", "/v1/audio/transcriptions", "audio", transport="multipart", media_fields={"audio": "file"}),
    _op("audio-translations", "/v1/audio/translations", "audio", transport="multipart", media_fields={"audio": "file"}),
    _op("realtime", "/v1/realtime", "audio", method="GET", transport="websocket"),
    _op("media-upload", "/v1/media", "media", transport="multipart", media_fields={"file": "file"}),
    _op("media-fetch", "/v1/media/{media_id}", "media", method="GET"),
    _op("media-delete", "/v1/media/{media_id}", "media", method="DELETE"),
    _op("models", "/v1/models", "utility", method="GET"),
    _op("model", "/v1/models/{model}", "utility", method="GET"),
    _op("generation-bill", "/v1/generation", "utility", method="GET"),
]


def basic_form_schema(operation: str | None) -> dict:
    """Fields defined by a gateway protocol, independent of a model's metadata."""
    text = {"temperature": {"type": "number"}, "stream": {"type": "boolean"}}
    image = {"size": {"type": "string"}, "n": {"type": "integer", "minimum": 1}}
    if operation in {"chat", "completions", "messages"}:
        fields = {**text, "max_tokens": {"type": "integer", "minimum": 1}}
    elif operation == "responses":
        fields = {**text, "max_output_tokens": {"type": "integer", "minimum": 1}}
    elif operation in {"gemini-generate", "gemini-stream"}:
        fields = {"generationConfig": {"type": "object", "x-ui-group": "common", "properties": {
            "temperature": {"type": "number"}, "maxOutputTokens": {"type": "integer", "minimum": 1},
        }}}
    elif operation in {"image-generation", "image-edit", "image-task", "image-task-edit"}:
        fields = image
    elif operation == "video-generation":
        fields = {**{key: {"type": "string"} for key in ("resolution", "aspect_ratio")},
                  "duration": {"type": "integer"}}
    elif operation == "audio-speech":
        fields = {"voice": {"type": "string"}, "speed": {"type": "number"}, "response_format": {"type": "string"}}
    elif operation in {"audio-transcriptions", "audio-translations"}:
        fields = {"temperature": {"type": "number"}, "response_format": {"type": "string"}}
        if operation == "audio-transcriptions":
            fields["language"] = {"type": "string"}
    elif operation == "realtime":
        fields = {"voice": {"type": "string"}, "instructions": {"type": "string"}}
    else:
        fields = {}
    return {"type": "object", "properties": fields}
for tool in ("remove-watermark", "remove-background", "upscale", "image-text", "motion-video"):
    PROTOCOLS.append(_op("tool-" + tool, "/v1/tools/" + tool,
                         "video" if tool == "motion-video" else "image", transport="json" if tool == "motion-video" else "multipart",
                         polling=VIDEO_POLL if tool == "motion-video" else None,
                         media_fields={"image": "image", "video": "video"} if tool == "motion-video" else {"image": "image"}))
for action in ("imagine", "action", "shorten", "modal", "change", "simple-change", "describe", "blend", "edits", "video", "upload-discord-images"):
    PROTOCOLS.append(_op("mj-" + action, "/mj/submit/" + action, "midjourney", polling=MJ_POLL))
PROTOCOLS.extend([
    _op("mj-swap", "/mj/insight-face/swap", "midjourney", polling=MJ_POLL),
    _op("mj-fetch", "/mj/task/{task_id}/fetch", "midjourney", method="GET"),
    _op("mj-image-seed", "/mj/task/{task_id}/image-seed", "midjourney", method="GET"),
    _op("mj-list", "/mj/task/list-by-condition", "midjourney"),
    _op("mj-image", "/mj/image/{task_id}", "midjourney", method="GET", transport="binary"),
    _op("suno-action", "/suno/submit/{action}", "suno", polling=SUNO_POLL),
    _op("suno-fetch", "/suno/fetch", "suno"),
    _op("suno-fetch-id", "/suno/fetch/{task_id}", "suno", method="GET"),
    _op("native-kling-text", "/kling/v1/videos/text2video", "video", polling={**VIDEO_POLL, "path": "/kling/v1/videos/text2video/{task_id}"}),
    _op("native-kling-image", "/kling/v1/videos/image2video", "video", polling={**VIDEO_POLL, "path": "/kling/v1/videos/image2video/{task_id}"}),
    _op("native-seedance", "/seedance/api/v3/contents/generations/tasks", "video", polling={**VIDEO_POLL, "path": "/seedance/api/v3/contents/generations/tasks/{task_id}"}),
    _op("native-seedance-list", "/seedance/api/v3/contents/generations/tasks", "video", method="GET"),
    _op("native-seedance-fetch", "/seedance/api/v3/contents/generations/tasks/{task_id}", "video", method="GET"),
    _op("native-kling-text-fetch", "/kling/v1/videos/text2video/{task_id}", "video", method="GET"),
    _op("native-kling-image-fetch", "/kling/v1/videos/image2video/{task_id}", "video", method="GET"),
    _op("native-jimeng", "/jimeng/", "video", query={"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"}, polling={"method": "POST", "path": "/jimeng/", "query": {"Action": "CVSync2AsyncGetResult", "Version": "2022-08-31"}, "body": {"task_id": "{task_id}"}, "id_fields": ["data.task_id"]}),
    _op("native-jimeng-fetch", "/jimeng/", "video", query={"Action": "CVSync2AsyncGetResult", "Version": "2022-08-31"}),
    _op("native-dashscope", "/dashscope/api/v1/services/aigc/video-generation/video-synthesis", "video", polling={**VIDEO_POLL, "path": "/dashscope/api/v1/tasks/{task_id}", "id_fields": ["output.task_id", "data.task_id", "id"]}),
    _op("native-dashscope-fetch", "/dashscope/api/v1/tasks/{task_id}", "video", method="GET"),
])
for operation in list(PROTOCOLS):
    if operation["id"].startswith("mj-"):
        mode_operation = copy.deepcopy(operation)
        mode_operation["id"] = "mj-mode-" + operation["id"][3:]
        mode_operation["label"] = "Mode " + operation["label"]
        mode_operation["path"] = "/{mode}" + operation["path"]
        if mode_operation.get("polling"):
            mode_operation["polling"]["path"] = "/{mode}" + mode_operation["polling"]["path"]
        PROTOCOLS.append(mode_operation)


def get_protocol(id: str) -> dict:
    for operation in PROTOCOLS:
        if operation["id"] == id:
            return copy.deepcopy(operation)
    raise ValueError(f"Unknown EasyAPI operation: {id}")


def validate_path(path: str) -> str:
    parsed = urlsplit(str(path))
    decoded = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or not decoded.startswith("/") or decoded.startswith("//"):
        raise ValueError("Operation path must be relative to the selected gateway")
    if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        raise ValueError("Operation path cannot traverse directories")
    if parsed.fragment:
        raise ValueError("Operation path cannot contain a fragment")
    if decoded in {"/app-config", "/pricing", "/pricing/schema", "/pricing/detail", "/pricing/docs", "/usage/token"}:
        return str(path)
    allowed = ("/v1/", "/v1beta/", "/kling/", "/seedance/", "/suno/", "/mj/", "/jimeng/", "/dashscope/", "/api/v3/contents/generations/", "/api/v1/services/aigc/", "/api/v1/tasks/")
    dynamic = re.match(r"^/[A-Za-z0-9_-]+/(?:api/v3/contents/generations/|api/v1/(?:services/aigc/|tasks/)|mj/)", decoded)
    if decoded != "/jimeng" and not decoded.startswith(allowed) and not dynamic:
        raise ValueError("Path is not a user creative API route")
    return str(path)
