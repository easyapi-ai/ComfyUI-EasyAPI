"""Typed ComfyUI V3 nodes over the EasyAPI user-side operation catalog."""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import json
import mimetypes
import shutil
import uuid
import re
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from comfy_api.latest import io

from . import media
from .client import APIResponse, EasyAPIError, UploadFile
from .image_parameters import normalize_image_size
from .protocols import PROTOCOLS, get_protocol, validate_path
from .progress import native_progress
from .runtime import get_runtime
from .tasks import create_task_handle, wait_for_task
from .video import VIDEO_SLOTS, encode_video_sources, select_video_scenario

JSON = io.Custom("EASYAPI_JSON")
MEDIA = io.Custom("EASYAPI_MEDIA")
TASK = io.Custom("EASYAPI_TASK")
MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_REQUEST_BYTES = 100 * 1024 * 1024
MAX_VIDEO_INLINE_BYTES = 20 * 1024 * 1024
VIDEO_MULTIPART_JSON_FIELDS = {"input_references", "metadata", "generate_audio", "seed", "width", "height", "fps", "n"}
TASK_OPERATIONS = ("video-generation", "image-task", "image-task-edit", "tool-motion-video")


def parse_object(value, name="params_json") -> dict:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    try:
        result = json.loads(value or "{}")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 必须是有效 JSON：{exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{name} 必须是 JSON 对象")
    return result


def _interrupt():
    import comfy.model_management
    comfy.model_management.throw_exception_if_processing_interrupted()


def _output_dir():
    return get_runtime().output_dir


def _source_data(source) -> tuple[str, str]:
    if not source.path:
        raise ValueError("此协议需要本地素材，请连接本地 IMAGE/AUDIO/VIDEO，或使用支持 URL 的原生请求")
    if Path(source.path).stat().st_size > MAX_MEDIA_BYTES:
        raise ValueError("单个素材超过 100 MiB 上限，请缩小素材")
    return base64.b64encode(Path(source.path).read_bytes()).decode(), source.mime or "application/octet-stream"


def _source_uri(source):
    if source.url:
        return source.url
    data, mime = _source_data(source)
    return f"data:{mime};base64,{data}"


def _guard_sources(sources, allow_total=False):
    sizes = [Path(s.path).stat().st_size for s in sources if s.path]
    if any(size > MAX_MEDIA_BYTES for size in sizes):
        raise ValueError("单个素材超过 100 MiB 上限，请缩小素材")
    if not allow_total and sum(sizes) > MAX_REQUEST_BYTES:
        raise ValueError("本次素材总量超过 100 MiB 上限，请减少参考素材")


def _guard_body(body):
    if body is not None and len(json.dumps(body, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
        raise ValueError("编码后的请求超过 100 MiB，请缩小素材或为支持 URL 的协议连接已有素材 URL")
    return body


async def validate_request(client, model, protocol, body, files, *, apply_defaults=False, metadata=None):
    """Validate declared capabilities; absent metadata keeps advanced input usable."""
    if not model or body is None:
        return
    if metadata is None:
        try:
            metadata = await client.model_schema(model, protocol.get("effective_id", protocol["id"]))
        except EasyAPIError:
            return
    if metadata.get("matched") is False:
        return
    schema = metadata.get("request_schema") or metadata.get("schema") or {}
    if not metadata.get("declared", bool(schema.get("properties"))) or not schema.get("properties"):
        return
    from jsonschema import Draft202012Validator, SchemaError
    properties = schema.get("properties", {})
    if apply_defaults:
        for key in schema.get("required", []):
            definition = properties.get(key, {})
            if key not in body and "default" in definition and definition.get("format") != "binary" and key not in {
                    "image", "images", "mask", "file", "files", "input_reference", "input_references"}:
                body[key] = copy.deepcopy(definition["default"])
    candidate = copy.deepcopy(body)
    if protocol["id"] == "video-generation" and isinstance(candidate.get("input_references"), list):
        # Model docs describe materialized URL references. The unified gateway
        # resolves file indexes and asset IDs before dispatching the request.
        attachments = {upload.field for upload in files or []}
        image_references = []
        for ref in candidate["input_references"]:
            if not isinstance(ref, dict):
                continue
            index = ref.get("file_index")
            if f"input_references[{index}]" in attachments or ref.get("asset_id"):
                ref.pop("file_index", None)
                ref.pop("asset_id", None)
                ref.setdefault("url", "https://attached-media.invalid/input")
            if ref.get("type") == "image" and ref.get("url"):
                image_references.append(ref["url"])
        if image_references and "input_references" not in properties:
            if "images" in properties:
                candidate.setdefault("images", image_references)
            for key in ("image", "input_reference"):
                if key in properties:
                    candidate.setdefault(key, image_references[0])
    grouped = {}
    for upload in files or []:
        field = upload.field.split("[", 1)[0]
        grouped.setdefault(field, []).append(upload.filename or Path(upload.path).name)
    for field, filenames in grouped.items():
        if field in candidate:
            continue
        specification = properties.get(field, {})
        candidate[field] = filenames if specification.get("type") == "array" else filenames[0]
    size_definition = properties.get("size", {})
    size_spec = size_definition.get("x-size-spec") if isinstance(size_definition, dict) else None
    normalized_size = None
    if "size" in body and isinstance(size_spec, dict) and size_spec.get("allow_custom") is True:
        normalized_size = normalize_image_size(size_definition, body["size"])
        candidate["size"] = normalized_size
        # Extend only this request's size enum. The cached model contract and
        # all other JSON Schema constraints must remain unchanged.
        schema = copy.deepcopy(schema)
        size_definition = schema["properties"]["size"]
        if isinstance(size_definition.get("enum"), list) and normalized_size not in size_definition["enum"]:
            size_definition["enum"].append(normalized_size)
    try:
        Draft202012Validator.check_schema(schema)
        error = next(Draft202012Validator(schema).iter_errors(candidate), None)
    except SchemaError:
        return
    if error:
        location = ".".join(str(part) for part in error.absolute_path) or "请求"
        hints = {
            "type": f"类型应为 {error.validator_value}", "enum": "值不在模型允许的选项中",
            "minItems": f"素材或条目数量至少为 {error.validator_value}",
            "maxItems": f"素材或条目数量不能超过 {error.validator_value}",
            "minimum": f"值不能小于 {error.validator_value}", "maximum": f"值不能大于 {error.validator_value}",
            "required": "缺少必需字段：" + ", ".join(str(k) for k in error.validator_value if isinstance(error.instance, dict) and k not in error.instance)
                        if error.validator == "required" else "缺少必需字段",
        }
        raise ValueError(f"模型参数 {location}：{hints.get(error.validator, '不符合模型声明的参数约束')}")
    if normalized_size is not None:
        body["size"] = normalized_size


def _accepted_media(operation, family):
    if operation in {"chat"}:
        return {"image", "audio", "video"}
    if operation in {"responses", "responses-compact", "gemini-generate", "gemini-stream", "media-upload"}:
        return {"image", "audio", "video", "file"}
    if operation in {"messages", "messages-count-tokens", "native-kling-image", "native-jimeng"} or family == "midjourney":
        return {"image"}
    if operation in {"video-generation", "video-generations", "native-seedance", "native-dashscope"}:
        return {"image", "audio", "video"}
    if operation in {"audio-transcriptions", "audio-translations"}:
        return {"audio", "video"}
    if operation == "tool-motion-video":
        return {"image", "video"}
    if family == "image" or operation.startswith("tool-"):
        return {"image", "mask"}
    return set()


def _without_model_in_path(protocol, body, model, values):
    route_values = {**values, "model": model or values.get("model", "")}
    def replace(match):
        name = match.group(1)
        value = route_values.get(name)
        if value is None or value == "":
            raise ValueError(f"操作 {protocol['id']} 需要 params_json.{name}")
        return quote(str(value), safe="")
    path = re.sub(r"\{([a-z_]+)\}", replace, protocol["path"])
    if protocol.get("polling"):
        def expand(value):
            if isinstance(value, str):
                return re.sub(r"\{(?!task_id\})([a-z_]+)\}", replace, value)
            if isinstance(value, dict):
                return {k: expand(v) for k, v in value.items()}
            return value
        protocol["polling"] = expand(protocol["polling"])
    return path


async def prepare_request(client, protocol, model, prompt, params, sources, request_body=None):
    """Build native envelopes without changing the stable Comfy input schema."""
    body = parse_object(request_body, "request_body") if request_body is not None else copy.deepcopy(params)
    if protocol["id"] == "custom-user-request":
        protocol["path"] = validate_path(body.pop("__path", protocol["path"]))
        protocol["method"] = str(body.pop("__method", "POST")).upper()
        known = next((p for p in PROTOCOLS if p["id"] != "custom-user-request" and p["path"] == protocol["path"] and p["method"] == protocol["method"]), None)
        protocol["effective_id"] = known["id"] if known else "custom-user-request"
        if known:
            protocol["family"] = known["family"]
            if known.get("polling"):
                protocol["polling"] = copy.deepcopy(known["polling"])
        protocol["transport"] = body.pop("__transport", known["transport"] if known else "json")
        explicit_fields = body.pop("__media_fields", None)
        protocol["media_fields"] = explicit_fields if explicit_fields is not None else protocol.get("media_fields", {})
        if explicit_fields is not None:
            protocol["effective_id"] = "custom-user-request"
        if not protocol["media_fields"] and known:
            protocol["media_fields"] = known.get("media_fields", {})
        if protocol["method"] not in {"GET", "POST", "DELETE"} or protocol["transport"] not in {"json", "multipart", "binary"}:
            raise ValueError("原生创作请求仅支持 GET/POST/DELETE 和 json/multipart/binary")
        polling = body.pop("__polling", None)
        if polling:
            polling["path"] = validate_path(polling["path"])
            protocol["polling"] = polling
    native = request_body is not None
    if native:
        prompt = ""  # Native envelopes stay intact except explicit model and connected media.
    route_values = {**params, **body, **body.pop("__route_params", {})}
    query = {**protocol.get("query", {}), **body.pop("__query", {})}
    operation, family = protocol.get("effective_id", protocol["id"]), protocol["family"]
    if operation != "custom-user-request" and any(s.kind not in _accepted_media(operation, family) for s in sources):
        raise ValueError("所选协议不支持已连接的素材类型；请改选兼容协议，或在原生请求中通过 __media_fields 显式指定字段")
    video_tool = operation == "tool-motion-video"
    url_capable = video_tool or operation in {"video-generation", "video-generations", "native-dashscope", "image-generation", "image-task", "responses", "responses-compact", "gemini-generate", "gemini-stream", "native-seedance"} or (operation == "chat" and all(s.kind in {"image", "video"} for s in sources))
    _guard_sources(sources, allow_total=url_capable)
    if operation == "video-generation" and sources:
        for field in ("image", "images", "input_reference", "input_references"):
            body.pop(field, None)
    # The gateway's multipart parser only restores JSON types for these fields.
    # Use JSON with direct inline assets when other typed options are present.
    video_json = video_tool or operation == "video-generation" and any(
        not isinstance(value, str) and key not in VIDEO_MULTIPART_JSON_FIELDS
        for key, value in body.items()
    )
    local_sizes = {str(s.path): Path(s.path).stat().st_size for s in sources if s.path}
    local_size = sum(local_sizes[str(s.path)] for s in sources if s.path)
    encoding_factor = 1 if operation == "video-generation" and not video_json else 4 / 3
    estimated_payload = len(json.dumps(body).encode()) + local_size * encoding_factor + len(sources) * 2048
    if url_capable and estimated_payload > MAX_REQUEST_BYTES and sources:
        converted = []
        for source in sources:
            asset = {"url": source.url} if source.url else await client.upload_media(source.path, kind=source.kind, role=source.role, mime=source.mime)
            if not asset.get("url"):
                raise ValueError("大素材上传后缺少 URL，未提交生成任务")
            converted.append(media.MediaSource(source.kind, role=source.role, mime=source.mime, url=asset["url"]))
        sources = converted
    elif video_json:
        converted = []
        for source in sources:
            if source.url:
                converted.append(source)
                continue
            if local_sizes[str(source.path)] <= MAX_VIDEO_INLINE_BYTES:
                url = _source_uri(source)
            else:
                asset = await client.upload_media(source.path, kind=source.kind, role=source.role, mime=source.mime)
                url = asset.get("url")
                if not url:
                    raise ValueError("Media upload did not return a URL.")
            converted.append(media.MediaSource(source.kind, role=source.role, mime=source.mime, url=url))
        sources = converted
    path = _without_model_in_path(protocol, body, model, route_values)
    if protocol["transport"] == "websocket":
        raise ValueError("实时会话请使用 EasyAPI 实时对话节点中的连接按钮")
    if model:
        body["model"] = model
    files = []
    if protocol["method"] == "GET":
        query.update({k: v for k, v in body.items() if k not in {"task_id", "model", "action"}})
        return path, None, files, query
    if native and not sources:
        return path, _guard_body(body), files, query
    if video_tool:
        connected = set()
        for source in sources:
            if source.kind in connected:
                raise ValueError("Connect one image and one motion reference video.")
            connected.add(source.kind)
            body[source.kind] = source.url
        if any(not isinstance(body.get(key), str) or not body[key].strip() for key in ("image", "video")):
            raise ValueError("Connect an image and a motion reference video.")
        if prompt:
            body["prompt"] = prompt
        return path, _guard_body(body), files, query
    if operation == "custom-user-request" and sources:
        fields = protocol.get("media_fields", {})
        if not isinstance(fields, dict) or any(s.kind not in fields for s in sources):
            raise ValueError("未知原生协议的连接素材需要 request_body.__media_fields 指定字段；也可在原生请求中显式填写素材 URL")
        if protocol["transport"] != "multipart":
            grouped = {}
            for source in sources:
                grouped.setdefault(fields[source.kind], []).append(_source_uri(source))
            for name, values in grouped.items():
                target = body
                parts = str(name).split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = values[0] if len(values) == 1 else values
            return path, _guard_body(body), files, query

    if protocol["transport"] == "multipart" or (operation == "video-generation" and any(s.path for s in sources)):
        single_reference = len(sources) == 1 and sources[0].kind == "image" and sources[0].role in {None, "", "reference"}
        for index, source in enumerate(sources):
            if not source.path:
                if operation != "video-generation":
                    raise ValueError("文件上传接口需要本地素材；URL 素材请使用原生 JSON 协议")
                continue
            if operation == "video-generation":
                field = "input_reference" if single_reference else f"input_references[{index}]"
            elif operation in {"audio-transcriptions", "audio-translations"}:
                if source.kind not in {"audio", "video"} or files:
                    continue
                field = "file"
            elif operation == "media-upload":
                field = "file"
            else:
                field = protocol.get("media_fields", {}).get(source.kind, source.kind)
            files.append(UploadFile(field, str(source.path), content_type=source.mime))
        if operation == "video-generation" and not single_reference:
            body["input_references"] = [{**({"file_index": index} if source.path else {"url": source.url}),
                                          "type": source.kind, "role": source.role or "reference"}
                                        for index, source in enumerate(sources)]
        if prompt:
            body["prompt"] = prompt
        if operation in {"audio-transcriptions", "audio-translations"} and not files:
            raise ValueError("请连接 AUDIO 或包含音轨的 VIDEO 输入")
        if operation in {"image-edit", "image-task-edit", "edits"} and not any(s.kind == "image" for s in sources):
            raise ValueError("图片编辑需要 IMAGE 输入")
        return path, _guard_body(body), files, query

    uploaded = []
    url_only = operation in {"video-generations", "native-dashscope"}
    if sources and url_only:
        for source in sources:
            asset = {"url": source.url} if source.url else await client.upload_media(source.path, kind=source.kind, role=source.role, mime=source.mime)
            if not asset.get("url"):
                raise ValueError("素材上传成功但未返回可引用的 URL")
            uploaded.append({"url": asset["url"], "type": source.kind, "role": source.role or "reference"})

    if operation == "chat":
        content = [{"type": "text", "text": prompt}] if prompt else []
        for source in sources:
            if source.kind == "image":
                content.append({"type": "image_url", "image_url": {"url": _source_uri(source)}})
            elif source.kind == "audio":
                data, mime = _source_data(source)
                content.append({"type": "input_audio", "input_audio": {"data": data, "format": "wav"}})
            elif source.kind == "video":
                content.append({"type": "video_url", "video_url": {"url": _source_uri(source)}})
        if prompt or sources or not body.get("messages"):
            body.setdefault("messages", []).append({"role": "user", "content": content if sources else prompt})
    elif operation in {"messages", "messages-count-tokens"}:
        content = [{"type": "text", "text": prompt}] if prompt else []
        for source in sources:
            if source.kind != "image":
                raise ValueError("Messages 协议原生多模态只接受图片；音频/视频请选 Chat、Gemini 或原生请求")
            if source.url:
                content.append({"type": "image", "source": {"type": "url", "url": source.url}})
            else:
                data, mime = _source_data(source)
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        if prompt or sources or not body.get("messages"):
            body.setdefault("messages", []).append({"role": "user", "content": content})
        if operation == "messages":
            body.setdefault("max_tokens", 1024)
    elif operation in {"gemini-generate", "gemini-stream"}:
        parts = [{"text": prompt}] if prompt else []
        for source in sources:
            if source.url:
                parts.append({"file_data": {"mime_type": source.mime, "file_uri": source.url}})
            else:
                data, mime = _source_data(source)
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
        if prompt or sources or not body.get("contents"):
            body.setdefault("contents", []).append({"role": "user", "parts": parts})
        body.pop("model", None)
    elif operation in {"responses", "responses-compact"}:
        parts = [{"type": "input_text", "text": prompt}] if prompt else []
        for source in sources:
            if source.kind == "image":
                parts.append({"type": "input_image", "image_url": _source_uri(source)})
            else:
                parts.append({"type": "input_file", **({"file_url": source.url} if source.url else
                              {"file_data": _source_uri(source), "filename": Path(source.path).name})})
        if prompt or sources or not body.get("input"):
            new = {"role": "user", "content": parts}
            existing = body.get("input")
            body["input"] = ([{"role": "user", "content": existing}] if isinstance(existing, str) else list(existing or [])) + [new]
    elif operation == "audio-speech":
        if prompt:
            body["input"] = prompt
        body.setdefault("response_format", "wav")
    elif operation in {"embeddings", "engine-embeddings", "moderations"}:
        if prompt:
            body["input"] = prompt
    elif operation == "rerank":
        if prompt:
            body["query"] = prompt
        if not body.get("documents"):
            raise ValueError("重排序需要 params_json.documents 文本数组")
    elif operation == "native-kling-image":
        images = [s for s in sources if s.kind == "image"]
        if images:
            body["image"] = images[0].url or _source_data(images[0])[0]
        if len(images) > 1:
            body["image_tail"] = images[1].url or _source_data(images[1])[0]
        if prompt:
            body["prompt"] = prompt
    elif operation == "native-seedance":
        content = [p for p in body.get("content", []) if p.get("type") == "text"] if sources else list(body.get("content", []))
        if prompt:
            content.append({"type": "text", "text": prompt})
        for source in sources:
            field = source.kind + "_url"
            content.append({"type": field, field: {"url": _source_uri(source)}, "role": source.role or "reference"})
        body["content"] = content
    elif operation == "native-dashscope":
        inputs = body.setdefault("input", {})
        if prompt:
            inputs["prompt"] = prompt
        for source in uploaded:
            field = {"image": "img_url", "audio": "audio_url", "video": "video_url"}.get(source["type"])
            if field:
                inputs[field] = source["url"]
    elif operation == "native-jimeng":
        if prompt:
            body["prompt"] = prompt
        if sources:
            body["binary_data_base64"] = [_source_data(s)[0] for s in sources if s.kind == "image"]
    else:
        if prompt:
            body["query" if operation == "alpha-search" else "prompt"] = prompt
        if sources:
            if family == "midjourney":
                body["base64Array"] = [_source_uri(s) for s in sources if s.kind == "image"]
            elif operation == "video-generations":
                body["input_references"] = uploaded
            elif operation == "video-generation":
                body["input_references"] = [{"url": s.url, "type": s.kind, "role": s.role or "reference"} for s in sources]
            elif family == "image":
                field = protocol.get("media_fields", {}).get("image", "images")
                body[field] = [_source_uri(s) for s in sources if s.kind == "image"]
                masks = [s for s in sources if s.kind == "mask"]
                if masks:
                    body["mask"] = _source_uri(masks[0])
    return path, _guard_body(body), files, query


def output_schema():
    return [io.String.Output(display_name="文本"), JSON.Output(display_name="JSON"),
            io.Image.Output(display_name="图片"), io.Mask.Output(display_name="蒙版"),
            io.Audio.Output(display_name="音频"), io.Video.Output(display_name="视频"),
            TASK.Output(display_name="任务"), MEDIA.Output(display_name="素材")]


def _pack(decoded, handle=None):
    decoded = decoded or {}
    sources = []
    for path in decoded.get("files", []):
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        kind = mime.split("/", 1)[0]
        sources.append(media.MediaSource(kind if kind in {"image", "audio", "video"} else "file", str(path), mime=mime))
    if not sources:
        sources = [media.MediaSource("file", url=url) for url in decoded.get("urls", [])]
    return io.NodeOutput(str(decoded.get("text") or ""), decoded.get("json"),
                         decoded.get("images"), decoded.get("mask"), decoded.get("audio"),
                         decoded.get("video"), handle,
                         sources,
                         ui={"easyapi_text": [str(decoded.get("text") or "")], "easyapi_task": [handle or {}]})


def _pack_video(decoded, handle=None):
    return io.NodeOutput(decoded.get("video"), decoded.get("video_url") or "", decoded.get("json"),
                         ui={"easyapi_text": [str(decoded.get("text") or "")], "easyapi_task": [handle or {}]})


class OperationNode(io.ComfyNode):
    NODE_ID = "EasyAPIOperation"
    DISPLAY = "EasyAPI 创作"
    OPS = ()

    @classmethod
    def define_schema(cls):
        operations = [p["id"] for p in PROTOCOLS if not cls.OPS or p["id"] in cls.OPS]
        inputs = [io.String.Input("profile_id", default="", tooltip="连接配置别名；点击节点中的连接设置管理凭据"),
                  io.String.Input("model", default="", tooltip="可手填模型名称，也可从实时刷新的模型列表选择"),
                  io.Combo.Input("operation", options=operations),
                  io.String.Input("prompt", default="", multiline=True),
                  io.String.Input("params_json", default="{}", multiline=True, tooltip="JSON 对象；显式模型、提示词和连接素材优先"),
                  io.Boolean.Input("wait_for_result", default=True, tooltip="关闭后仅提交异步任务，可从任务节点恢复查询"),
                  io.Int.Input("timeout", default=1800, min=1, max=86400),
                  io.Image.Input("image", optional=True), io.Mask.Input("mask", optional=True),
                  io.Audio.Input("audio", optional=True), io.Video.Input("video", optional=True),
                  MEDIA.Input("references", optional=True)]
        return io.Schema(node_id=cls.NODE_ID, display_name=cls.DISPLAY, category="EasyAPI/创作",
                         inputs=inputs, outputs=output_schema(), not_idempotent=True,
                         description="调用 EasyAPI 创作接口。异步任务可恢复，模型与高级参数在运行时刷新。")

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # A queued creative action is explicit. Do not silently reuse a previous paid generation.
        return float("nan")

    @classmethod
    @native_progress
    async def execute(cls, profile_id="", model="", prompt="", params_json="{}", operation="chat",
                      wait_for_result=True, timeout=1800, image=None, mask=None, audio=None,
                      video=None, references=None):
        params = parse_object(params_json)
        if operation not in cls.OPS:
            raise ValueError(f"Operation is not supported by this node: {operation}.")
        if not model:
            raise ValueError("Select an EasyAPI model before running this node.")
        protocol = get_protocol(operation)
        runtime = get_runtime()
        client = runtime.client(profile_id)
        _interrupt()
        sources = await media.encode_inputs(image=image, mask=mask, audio=audio, video=video, references=references)
        return await cls.submit(client, runtime, model, prompt, params,
                                protocol, sources, wait_for_result, timeout)

    @classmethod
    async def submit(cls, client, runtime, model, prompt, params,
                     protocol, sources, wait_for_result, timeout, metadata=None, output_builder=None):
        _interrupt()
        path, body, files, query = await prepare_request(client, protocol, model, prompt, params, sources)
        await validate_request(client, model, protocol, body, files, apply_defaults=True, metadata=metadata)
        response = await client.request(protocol["method"], path, body=body, files=files or None,
                                        query=query or None, stream=bool(params.get("stream", False)))
        handle = create_task_handle(response, client.profile_id, protocol)
        if handle:
            runtime.tasks.save(handle)
            if wait_for_result:
                response = await wait_for_task(client, runtime.tasks, handle, timeout=timeout, interrupt=_interrupt)
            else:
                if output_builder is not None:
                    return output_builder({"json": response.data, "text": "任务已提交：" + handle["task_id"]}, handle)
                return io.NodeOutput("任务已提交：" + handle["task_id"], response.data, None, None,
                                     None, None, handle, {"files": [], "urls": []})
        decoded = await media.decode_response(response, client, _output_dir())
        return (output_builder or _pack)(decoded, handle)


def _operation_class(name, display, ops):
    return type(name, (OperationNode,), {
        "NODE_ID": name, "DISPLAY": display, "OPS": tuple(ops),
        "__module__": __name__,
    })


EasyAPIChat = _operation_class("EasyAPIChat", "EasyAPI 对话与多模态", ["chat", "responses", "responses-compact", "messages", "completions", "gemini-generate", "gemini-stream"])
EasyAPISpeech = _operation_class("EasyAPISpeech", "EasyAPI 语音合成", ["audio-speech"])
EasyAPITranscribe = _operation_class("EasyAPITranscribe", "EasyAPI 转写与翻译", ["audio-transcriptions", "audio-translations"])
EasyAPICountTokens = _operation_class("EasyAPICountTokens", "EasyAPI Token 计数", ["messages-count-tokens"])


def _image_parameter_references(params):
    references = []

    def add(value, kind):
        if value is None or value == "":
            return
        if isinstance(value, list):
            for entry in value:
                add(entry, kind)
            return
        if isinstance(value, media.MediaSource):
            if value.kind != kind:
                raise ValueError("图片节点仅接受图片参考和蒙版。")
            references.append(copy.copy(value))
            return
        if isinstance(value, dict):
            declared_kind = value.get("kind") or value.get("type") or kind
            if declared_kind not in {kind, "image_url" if kind == "image" else "mask"}:
                raise ValueError("图片节点仅接受图片参考和蒙版，不支持音频或视频参考。")
            location = value.get("url") or value.get("image_url") or value.get("path")
            if isinstance(location, dict):
                location = location.get("url")
            if not location:
                raise ValueError("图片参考需要 url 或本地 path；不支持未解析的素材 ID。")
            add(location, kind)
            return
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("图片参考必须是图片 URL、本地路径或图片参考记录。")
        location = str(value).strip()
        scheme = urlsplit(location).scheme.lower()
        if scheme in {"http", "https", "data"}:
            references.append(media.MediaSource(kind, url=location))
        elif scheme and not (len(scheme) == 1 and len(location) > 2 and location[1] == ":"):
            raise ValueError("图片参考仅支持 http(s)、data:image 或本地图片路径。")
        else:
            references.append(media.MediaSource(kind, path=location))

    for field in ("image", "images", "input_references"):
        add(params.pop(field, None), "image")
    add(params.pop("mask", None), "mask")
    return references


async def _materialize_image_references(client, runtime, sources):
    """The sync edits route requires actual image files, including URL inputs."""
    if any(source.kind not in {"image", "mask"} for source in sources):
        raise ValueError("图片节点仅接受图片参考和蒙版，不支持音频或视频参考。")
    has_image = any(source.kind == "image" for source in sources)
    if not has_image and any(source.kind == "mask" for source in sources):
        raise ValueError("蒙版需要参考图片，请连接 image 或图片参考组。")
    if not has_image:
        return []

    def verify(path):
        from PIL import Image
        if not 0 < Path(path).stat().st_size <= MAX_MEDIA_BYTES:
            raise ValueError("参考图片必须非空，且不能超过 100 MiB。")
        try:
            with Image.open(path) as image:
                mime = Image.MIME.get(image.format, "image/png")
                image.verify()
        except (OSError, ValueError) as error:
            raise ValueError("参考素材不是有效图片；尚未提交生成请求。") from error
        return mime

    result = []
    for source in sources:
        source = copy.copy(source)
        if not source.path:
            location = str(source.url or "")
            directory = Path(runtime.input_dir)
            directory.mkdir(parents=True, exist_ok=True)
            if location.startswith("data:"):
                header, separator, payload = location.partition(",")
                mime = header[5:].split(";", 1)[0].lower()
                if not separator or not mime.startswith("image/") or ";base64" not in header:
                    raise ValueError("图片参考必须使用 data:image/...;base64 编码。")
                if len(payload) > ((MAX_MEDIA_BYTES + 2) // 3) * 4:
                    raise ValueError("单个参考图片超过 100 MiB 上限。")
                try:
                    raw = base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise ValueError("图片参考的 base64 编码无效。") from error
                if not raw or len(raw) > MAX_MEDIA_BYTES:
                    raise ValueError("参考图片必须非空，且不能超过 100 MiB。")
                path = directory / (uuid.uuid4().hex + (mimetypes.guess_extension(mime) or ".png"))
                await asyncio.to_thread(path.write_bytes, raw)
            else:
                if not location.startswith(("http://", "https://")):
                    raise ValueError("图片参考需要有效的本地图片或 http(s) URL。")
                suffix = Path(urlsplit(location).path).suffix
                path = directory / (uuid.uuid4().hex + (suffix if 1 < len(suffix) <= 7 else ".png"))
                await client.download(location, path, max_bytes=MAX_MEDIA_BYTES)
            source.path = str(path)
        source.mime = await asyncio.to_thread(verify, source.path)
        result.append(source)
    _guard_sources(result)
    return result


class EasyAPIImages(OperationNode):
    NODE_ID = "EasyAPIImages"
    DISPLAY = "EasyAPI 图片生成与编辑"
    OPS = ("image-generation", "image-edit")

    @classmethod
    def define_schema(cls):
        return io.Schema(node_id=cls.NODE_ID, display_name=cls.DISPLAY, category="EasyAPI/创作",
                         inputs=[io.String.Input("profile_id", default="", tooltip="连接配置别名"),
                                 io.String.Input("model", default="", tooltip="选择支持当前文生图或图生图操作的模型"),
                                 io.String.Input("prompt", default="", multiline=True),
                                 io.String.Input("params_json", default="{}", multiline=True),
                                 io.Image.Input("image", optional=True), io.Mask.Input("mask", optional=True),
                                 MEDIA.Input("references", optional=True)],
                         outputs=[io.Image.Output(id="image", display_name="image"),
                                  io.String.Output(id="url", display_name="url", tooltip="首个真实上游图片 URL；多图的全部 URL 保留在响应中。仅返回 base64 时为空。"),
                                  JSON.Output(id="response", display_name="响应")],
                         not_idempotent=True,
                         description="同步生成图片；有参考图片时自动图生图，无参考图片时自动文生图。")

    @classmethod
    @native_progress
    async def execute(cls, profile_id="", model="", prompt="", params_json="{}",
                      image=None, mask=None, references=None, **legacy):
        if set(legacy) - {"operation", "wait_for_result", "timeout", "audio", "video"}:
            raise ValueError("图片节点包含已不支持的输入，请重新创建新版图片节点。")
        if legacy.get("operation") not in {None, "", "image-generation", "image-edit"}:
            raise ValueError("新版图片节点仅支持同步生成；请重新创建图片节点，移除旧异步操作。")
        if legacy.get("wait_for_result", True) is not True:
            raise ValueError("新版图片节点始终同步等待图片，请移除 wait_for_result=False 或重新创建节点。")
        if legacy.get("audio") is not None or legacy.get("video") is not None:
            raise ValueError("图片节点不支持音频或视频输入，请移除旧连线。")
        if not model:
            raise ValueError("Select an EasyAPI image model before running this node.")
        params = parse_object(params_json)
        if params.pop("stream", False) not in (None, False):
            raise ValueError("图片节点仅支持同步非流式请求，请移除 stream=true。")
        if any(field in params for field in ("image_url", "input_image", "audio", "video", "reference_audio", "reference_video")):
            raise ValueError("图片参考请使用 image、images 或 input_references；图片节点不支持音视频参数。")
        if any(field in params for field in ("__path", "__method", "__transport", "__polling")):
            raise ValueError("图片节点的同步路由由参考图片自动选择，不能覆盖路由或轮询配置。")
        if any(params.get(field) not in (None, False) for field in ("async", "async_mode")):
            raise ValueError("图片节点仅支持同步生成，请移除异步参数。")
        params.pop("async", None)
        params.pop("async_mode", None)
        parameter_sources = _image_parameter_references(params)
        runtime = get_runtime()
        client = runtime.client(profile_id)
        _interrupt()
        sources = await media.encode_inputs(image=image, mask=mask, references=references)
        sources = await _materialize_image_references(client, runtime, [*sources, *parameter_sources])
        operation = "image-edit" if any(source.kind == "image" for source in sources) else "image-generation"
        protocol = get_protocol(operation)
        path, body, files, query = await prepare_request(client, protocol, model, prompt, params, sources)
        await validate_request(client, model, protocol, body, files, apply_defaults=True)
        _interrupt()
        response = await client.request("POST", path, body=body, files=files or None, query=query or None, stream=False)
        decoded = await media.decode_response(response, client, _output_dir())
        if decoded.get("images") is None:
            if decoded.get("files"):
                raise ValueError("图片尺寸不一致或响应没有可用的图片张量，请分别生成；原文件已保留。")
            raise ValueError("同步图片接口未返回可用图片；请确认模型支持同步图片生成。")
        url = next((client.base_url + value if value.startswith("/") else value
                    for value in decoded.get("urls", []) if value.startswith(("http://", "https://", "/"))), "")
        return io.NodeOutput(decoded["images"], url, decoded.get("json"))


class EasyAPIVideo(OperationNode):
    NODE_ID = "EasyAPIVideo"
    DISPLAY = "EasyAPI 视频生成"
    OPS = ("video-generation",)

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.outputs = [io.Video.Output(id="video", display_name="视频"),
                          io.String.Output(id="url", display_name="URL", tooltip="首个输出视频对应的真实上游 URL；仅返回二进制或内联视频时为空。"),
                          JSON.Output(id="response", display_name="响应")]
        generic_ports = {"image", "mask", "audio", "video"}
        schema.inputs = [entry for entry in schema.inputs if entry.id not in generic_ports]
        schema.inputs.append(io.String.Input("mode", default="auto"))
        types = {"image": io.Image, "audio": io.Audio, "video": io.Video}
        schema.inputs.extend(types[kind].Input(name, optional=True) for name, kind, _ in VIDEO_SLOTS)
        return schema

    @classmethod
    @native_progress
    async def execute(cls, profile_id="", model="", prompt="", params_json="{}", operation="video-generation",
                      wait_for_result=True, timeout=1800, mode="auto", references=None, **slots):
        if operation != "video-generation":
            raise ValueError("This node uses the EasyAPI unified video endpoint.")
        if not model:
            raise ValueError("Select an EasyAPI video model before running this node.")
        unsupported = {name for name, value in slots.items() if value is not None} - {name for name, _, _ in VIDEO_SLOTS}
        if unsupported:
            raise ValueError("Reconnect legacy video inputs using the named video reference slots.")
        params = parse_object(params_json)
        runtime = get_runtime()
        client = runtime.client(profile_id)
        _interrupt()
        sources = await encode_video_sources(slots, references)
        try:
            metadata = await client.model_schema(model, operation)
        except EasyAPIError:
            metadata = {}
        roles = {source.role for source in sources}
        if not sources:
            declared_refs = params.get("input_references")
            roles = {
                str(ref.get("role") or f"reference_{ref.get('type', 'image')}")
                for ref in (declared_refs if isinstance(declared_refs, list) else [])
                if isinstance(ref, dict)
            }
            if not roles and (params.get("images") or params.get("image") or params.get("input_reference")):
                allowed = (metadata.get("video") or {}).get("roles") or []
                roles.add("reference_image" if "reference_image" in allowed and "first_frame" not in allowed else "first_frame")
        video = metadata.get("video") or {}
        scenario = select_video_scenario(mode, roles, video, model=model)
        variant = (video.get("variants") or {}).get(scenario)
        if variant and variant.get("request_schema"):
            schema = copy.deepcopy(variant["request_schema"])
            if variant.get("required"):
                schema["required"] = variant["required"]
            metadata = {**metadata, "request_schema": schema}
        return await cls.submit(client, runtime, model, prompt, params, get_protocol(operation),
                                sources, wait_for_result, timeout, metadata=metadata, output_builder=_pack_video)


class EasyAPIMedia(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="EasyAPIMedia", display_name="EasyAPI 素材与参考组", category="EasyAPI/工具",
                         inputs=[io.Image.Input("image", optional=True), io.Mask.Input("mask", optional=True),
                                 io.Audio.Input("audio", optional=True), io.Video.Input("video", optional=True),
                                 MEDIA.Input("references", optional=True),
                                 io.String.Input("url", default="", tooltip="可填已有 http(s) 素材 URL，避免重复上传"),
                                 io.Combo.Input("kind", options=["image", "audio", "video", "file"]),
                                 io.String.Input("role", default="reference", tooltip="如 first_frame、last_frame、reference_image、reference_video、reference_audio")],
                         outputs=[MEDIA.Output(display_name="素材")])

    @classmethod
    async def execute(cls, image=None, mask=None, audio=None, video=None, references=None, role="reference", url="", kind="image"):
        sources = await media.encode_inputs(image=image, mask=mask, audio=audio, video=video)
        for source in sources:
            source.role = role
        if url:
            if not url.startswith(("http://", "https://", "data:")):
                raise ValueError("素材 URL 必须以 http://、https:// 或 data: 开头")
            sources.append(media.MediaSource(kind, role=role, url=url, mime=mimetypes.guess_type(url)[0]))
        previous = await media.encode_inputs(references=references)
        return io.NodeOutput(previous + sources)


class EasyAPIJSON(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="EasyAPIJSON", display_name="EasyAPI JSON 工具", category="EasyAPI/工具",
                         inputs=[io.Combo.Input("operation", options=["parse", "get", "merge", "set", "append", "stringify"]),
                                 io.String.Input("params_json", default="{}", multiline=True),
                                 io.String.Input("path", default="", tooltip="点号路径；数组可用数字索引"),
                                 JSON.Input("value", optional=True), JSON.Input("other", optional=True)],
                         outputs=[JSON.Output(display_name="JSON"), io.String.Output(display_name="文本")])

    @classmethod
    def execute(cls, operation="parse", params_json="{}", path="", value=None, other=None):
        if value is None:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", params_json.strip())
            try:
                value = json.loads(text)
            except ValueError as exc:
                raise ValueError(f"JSON 解析失败：{exc}") from exc
        value = copy.deepcopy(value)
        keys = [int(k) if k.isdecimal() else k for k in path.split(".") if k != ""]
        if operation == "get":
            for key in keys:
                value = value[key]
        elif operation == "merge":
            if not isinstance(value, dict) or not isinstance(other, dict):
                raise ValueError("合并需要两个 JSON 对象")
            value.update(other)
        elif operation == "set":
            if not keys:
                value = other
            else:
                target = value
                for key in keys[:-1]:
                    target = target.setdefault(key, {}) if isinstance(target, dict) else target[key]
                target[keys[-1]] = other
        elif operation == "append":
            target = value
            for key in keys:
                target = target[key]
            if not isinstance(target, list):
                raise ValueError("追加目标必须是 JSON 数组")
            target.append(other)
        return io.NodeOutput(value, json.dumps(value, ensure_ascii=False, indent=2))


class TaskNode(io.ComfyNode):
    NODE_ID = "EasyAPITaskQuery"
    DISPLAY = "EasyAPI 查询任务"
    WAIT = False

    @classmethod
    def define_schema(cls):
        return io.Schema(node_id=cls.NODE_ID, display_name=cls.DISPLAY, category="EasyAPI/任务",
                         inputs=[io.String.Input("profile_id", default=""), io.String.Input("task_id", default=""),
                                 io.Combo.Input("operation", options=list(TASK_OPERATIONS)),
                                 io.Int.Input("timeout", default=1800, min=1, max=86400), TASK.Input("task", optional=True)],
                         outputs=output_schema())

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    @native_progress
    async def execute(cls, profile_id="", task_id="", operation="video-generation", timeout=1800, task=None):
        runtime = get_runtime()
        handle = copy.deepcopy(task) if task else runtime.tasks.get(task_id, profile_id or None)
        if not handle:
            if not task_id:
                raise ValueError("请连接任务或填写任务 ID")
            if operation not in TASK_OPERATIONS:
                raise ValueError("Choose a supported EasyAPI task type.")
            protocol = get_protocol(operation)
            polling = protocol["polling"]
            handle = {"profile_id": profile_id, "task_id": task_id,
                      "poll_path": polling["path"].replace("{task_id}", quote(task_id, safe="")),
                      "poll_method": polling.get("method", "GET"),
                      "state": "submitted", "operation": operation,
                      "status_field": polling.get("status_field", "data.status")}
        client = runtime.client(handle.get("profile_id") or profile_id)
        handle["profile_id"] = client.profile_id
        if cls.WAIT:
            response = await wait_for_task(client, runtime.tasks, handle, timeout=timeout, interrupt=_interrupt)
        else:
            _interrupt()
            response = await client.request(handle.get("poll_method", "GET"), handle["poll_path"],
                                            body=handle.get("poll_body") or None, query=handle.get("poll_query") or None)
            from .tasks import response_record, task_state
            handle["latest"] = response_record(response)
            handle["state"] = task_state(response.data, handle)
            runtime.tasks.save(handle)
        decoded = await media.decode_response(response, client, _output_dir())
        return _pack(decoded, handle)


EasyAPITaskQuery = type("EasyAPITaskQuery", (TaskNode,), {"__module__": __name__})
EasyAPITaskWait = type("EasyAPITaskWait", (TaskNode,), {"NODE_ID": "EasyAPITaskWait", "DISPLAY": "EasyAPI 等待任务结果", "WAIT": True, "__module__": __name__})


class EasyAPIResultDownload(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="EasyAPIResultDownload", display_name="EasyAPI 下载与解码结果", category="EasyAPI/任务",
                         inputs=[io.String.Input("profile_id", default=""), io.String.Input("params_json", default="{}", multiline=True),
                                 io.String.Input("task_id", default=""), TASK.Input("task", optional=True), JSON.Input("result", optional=True)],
                         outputs=output_schema())

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    @native_progress
    async def execute(cls, profile_id="", params_json="{}", task_id="", task=None, result=None):
        runtime = get_runtime()
        handle = task or (runtime.tasks.get(task_id, profile_id or None) if task_id else None)
        data = result if result is not None else (handle.get("latest") if handle else parse_object(params_json))
        if result is None and handle and isinstance(data, dict) and "data" in data and "content_type" in data:
            data = data["data"]
        client = runtime.client((handle or {}).get("profile_id") or profile_id)
        response = APIResponse(data=data, status=200, headers={}, content_type="application/json", content=b"")
        return _pack(await media.decode_response(response, client, _output_dir()), handle)


class EasyAPIRealtime(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="EasyAPIRealtime", display_name="EasyAPI 实时语音对话", category="EasyAPI/实时",
                         inputs=[io.String.Input("profile_id", default=""), io.String.Input("model", default=""),
                                 io.String.Input("params_json", default="{}", multiline=True,
                                                 tooltip="可配置 voice、instructions、input_sample_rate/output_sample_rate、session 与服务端 VAD"),
                                 io.String.Input("snapshot_id", default="", tooltip="结束或冻结对话后自动填入；工作流只读取这份快照")],
                         outputs=[io.String.Output(display_name="对话文本"), io.Audio.Output(display_name="助手音频"),
                                  JSON.Output(display_name="对话记录")],
                         description="先在节点中连接并对话，再冻结或结束。执行工作流读取快照，不占用实时会话。")

    @classmethod
    def execute(cls, snapshot_id="", **kwargs):
        from .realtime import get_snapshot
        if not snapshot_id:
            raise ValueError("请先在节点中开始对话并点击「冻结本轮」或「结束并应用」")
        record = get_snapshot(snapshot_id)
        audio = record.pop("audio", None)
        return io.NodeOutput(record["text"], audio, record)


class EasyAPIPreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="EasyAPIPreview", display_name="EasyAPI 预览与输出", category="EasyAPI/工具",
                         is_output_node=True,
                         inputs=[io.String.Input("text", default="", multiline=True, optional=True),
                                 JSON.Input("json_value", optional=True), io.Image.Input("image", optional=True),
                                 io.Mask.Input("mask", optional=True), io.Audio.Input("audio", optional=True),
                                 io.Video.Input("video", optional=True), MEDIA.Input("references", optional=True)],
                         outputs=[])

    @classmethod
    async def execute(cls, text="", json_value=None, image=None, mask=None, audio=None, video=None, references=None):
        from comfy_api.latest import ui
        import folder_paths
        rendered = text or (json.dumps(json_value, ensure_ascii=False, indent=2) if json_value is not None else "")
        output = {"easyapi_text": [rendered], "easyapi_media": []}

        def append_saved(kind, records):
            for record in records:
                output["easyapi_media"].append({"kind": kind, "url": "/view?" + urlencode({
                    "filename": record["filename"], "subfolder": record.get("subfolder", ""),
                    "type": record.get("type", "temp")})})

        # Nodes 2.0 interprets a native images+audio UI bundle as a combined
        # audiovisual result. Keep independent media explicitly typed in our
        # DOM widget instead of making unrelated image/audio look like a video.
        if image is not None:
            append_saved("image", ui.PreviewImage(image).as_dict()["images"])
        elif mask is not None:
            append_saved("image", ui.PreviewMask(mask).as_dict()["images"])
        if audio is not None:
            append_saved("audio", ui.PreviewAudio(audio).as_dict()["audio"])
        if video is not None:
            filename = "easyapi_" + uuid.uuid4().hex + ".mp4"
            target = Path(folder_paths.get_temp_directory()) / filename
            source = video.get_stream_source() if hasattr(video, "get_stream_source") else None
            if isinstance(source, str):
                await asyncio.to_thread(shutil.copyfile, source, target)
            else:
                await asyncio.to_thread(video.save_to, str(target))
            output["easyapi_media"].append({"kind": "video", "url": f"/view?filename={filename}&type=temp"})
        sources = await media.encode_inputs(references=references)
        root = get_runtime().output_dir.resolve()
        for source in sources:
            if source.url:
                url = source.url
            elif source.path and Path(source.path).resolve().parent == root:
                url = "/easyapi/files/" + quote(Path(source.path).name)
            else:
                continue
            output["easyapi_media"].append({"kind": source.kind, "url": url})
        return io.NodeOutput(ui=output)


NODE_CLASSES = [EasyAPIChat, EasyAPIImages, EasyAPIVideo, EasyAPISpeech, EasyAPITranscribe,
                EasyAPICountTokens,
                EasyAPIMedia, EasyAPIJSON, EasyAPITaskQuery, EasyAPITaskWait,
                EasyAPIResultDownload, EasyAPIRealtime, EasyAPIPreview]
