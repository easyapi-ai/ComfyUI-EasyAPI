"""Asynchronous transport, discovery and streamed media IO for EasyAPI."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import codecs
import inspect
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from .profiles import normalize_base_url
from .protocols import PROTOCOLS, basic_form_schema, get_protocol, validate_path

MODEL_CATALOG_TTL = 300


@dataclass
class APIResponse:
    data: object = None
    status: int = 200
    headers: dict = field(default_factory=dict)
    content_type: str = "application/json"
    content: bytes = b""


@dataclass
class UploadFile:
    field: str
    path: str | Path
    filename: str | None = None
    content_type: str | None = None


class EasyAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, response: APIResponse | None = None, retryable=False):
        super().__init__(message)
        self.status = status
        self.response = response
        self.retryable = retryable


def redact(value, secret: str = ""):
    if isinstance(value, dict):
        return {k: ("[redacted]" if str(k).lower() in {"api_key", "authorization", "access_token"} else redact(v, secret))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secret) for v in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[redacted]")
    return value


def origin(url: str) -> tuple:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def credential_headers(api_key: str, client_id: str = "") -> dict:
    if not api_key:
        return {}
    if any(char in api_key for char in "\r\n"):
        raise ValueError("API key contains invalid characters")
    headers = {"Authorization": "Bearer " + api_key}
    if client_id:
        if not re.fullmatch(r"[a-f0-9]{64}", client_id):
            raise ValueError("Connection client_id must be 64 lowercase hexadecimal characters")
        headers["X-EasyAPI-Client-Id"] = client_id
    return headers


def normalize_parameter_schema(value):
    """Normalize legacy model.params constraints without dropping nested metadata."""
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    for old, new in (("min", "minimum"), ("max", "maximum"), ("min_length", "minLength"), ("max_length", "maxLength")):
        if old in result and new not in result:
            result[new] = result[old]
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {name: normalize_parameter_schema(prop) for name, prop in properties.items()}
        required = result.get("required") if isinstance(result.get("required"), list) else []
        required = list(required)
        for name, prop in properties.items():
            if isinstance(prop, dict) and prop.get("required") is True and name not in required:
                required.append(name)
            if isinstance(result["properties"][name], dict) and isinstance(result["properties"][name].get("required"), bool):
                result["properties"][name].pop("required")
        if required:
            result["required"] = required
    if isinstance(result.get("items"), dict):
        result["items"] = normalize_parameter_schema(result["items"])
    return result


VIDEO_ROLES = ("first_frame", "last_frame", "reference_image", "reference_video", "reference_audio")
VIDEO_SCENARIOS = ("text-to-video", "image-to-video", "reference-to-video")
_ENDPOINT_ALIASES = {
    "openai": "chat", "openai-response": "responses", "openai-response-compact": "responses-compact",
    "openai-alpha-search": "alpha-search", "anthropic": "messages", "gemini": "gemini-generate",
    "api-video": "video-generation", "openai-video": "video-generation", "video": "video-generation",
    "jina-rerank": "rerank", "audio-transcription": "audio-transcriptions",
}


def normalize_locale(value=None):
    value = str(value or "zh-CN").strip().replace("_", "-")
    aliases = {"zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-TW", "zh-hans": "zh-CN", "zh-hant": "zh-TW"}
    value = aliases.get(value.lower(), value)
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
        raise ValueError("Invalid interface locale")
    return value


def canonical_family(value):
    value = str(value or "").strip().lower()
    aliases = {"文本": "text", "文字": "text", "画像": "image", "图像": "image", "圖片": "image", "图片": "image",
               "视频": "video", "影片": "video", "動画": "video", "vidéo": "video", "видео": "video",
               "音频": "audio", "音訊": "audio", "音声": "audio", "аудио": "audio", "texte": "text", "текст": "text"}
    value = aliases.get(value, value)
    return value if value in {"text", "image", "video", "audio", "embedding", "rerank", "midjourney", "suno", "media", "utility"} else None


def _path_shape(path):
    path = str(path or "").split("?", 1)[0].rstrip("/")
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return re.sub(r"/:[A-Za-z_][A-Za-z0-9_]*", "/{}", path)


def _entry_matches(entry, operation):
    key = entry.get("key") or entry.get("type") or entry.get("id")
    path = entry.get("path")
    if path:
        if _path_shape(path) != _path_shape(operation["path"]):
            return False
    elif _ENDPOINT_ALIASES.get(key, key) != operation["id"]:
        return False
    transport = entry.get("transport")
    if not transport and key in {"image-task-edit", "async-image-edit"}:
        transport = "multipart"
    elif not transport and key in {"image-task", "async-image-generation"}:
        transport = "json"
    elif not transport and key == "realtime":
        transport = "websocket"
    if operation["path"] == "/v1/images/tasks" and operation["method"] == "POST":
        # JSON generation and multipart edits share a route, not a capability.
        if transport != operation["transport"]:
            return False
    method = str(entry.get("method") or (operation["method"] if not path else "GET" if transport == "websocket" else "POST")).upper()
    if method != operation["method"]:
        return False
    if transport == "websocket" and operation["transport"] != "websocket":
        return False
    for name, value in (operation.get("query") or {}).items():
        if name in (entry.get("query") or {}) and entry["query"][name] != value:
            return False
    return True


def _endpoint_entries(data):
    if not isinstance(data, dict):
        return []
    entries = []
    raw = data.get("endpoints") or []
    if isinstance(raw, dict):
        raw = [{"key": key, **(value if isinstance(value, dict) else {"path": value})} for key, value in raw.items()]
    for entry in raw:
        if isinstance(entry, dict):
            entries.append(entry)
            entries.extend(item for item in entry.get("variants") or [] if isinstance(item, dict))
    entries.extend(item for item in data.get("variants") or [] if isinstance(item, dict))
    for entry in list(entries):
        if isinstance(entry.get("native"), dict) and entry["native"].get("path"):
            entries.append(entry["native"])
    return entries


def _entry_operations(entries):
    operations = {operation["id"] for entry in entries for operation in PROTOCOLS if _entry_matches(entry, operation)}
    # Gemini's endpoint type identifies its generateContent protocol, whose
    # documented streaming action shares the model capability.
    for entry in entries:
        if (entry.get("type") or entry.get("key")) == "gemini":
            if "gemini-generate" in operations:
                operations.add("gemini-stream")
            if "gemini-compatible-generate" in operations:
                operations.add("gemini-compatible-stream")
    return operations


def _capability_operations(row):
    result, declared = set(), False
    for key in ("operations", "supported_operations"):
        if isinstance(row.get(key), list):
            result.update(value for value in row[key] if isinstance(value, str))
            declared = True
    caps = row.get("capabilities") or {}
    if isinstance(caps, dict):
        for operation in PROTOCOLS:
            for key in (operation["id"], operation["id"].replace("-", "_")):
                if isinstance(caps.get(key), bool):
                    declared = True
                    if caps[key]:
                        result.add(operation["id"])
    return result, declared


def _schema_corrections(model, operation, schema):
    """Small, evidenced gateway metadata repairs, never inferred by vendor family.

    Evidence: anonymous EasyAPI pricing metadata samples, 2026-11-18 and 2026-11-20.
    Fix only the affected exact model/field shapes; retain upstream metadata in
    docs, and expose every applied compatibility repair to the UI.
    """
    schema = normalize_parameter_schema(schema if isinstance(schema, dict) else {})
    props, repairs = schema.get("properties") or {}, []
    if model == "seedance-2.0-mini":
        duration = props.get("duration")
        if isinstance(duration, dict) and duration.get("minimum") == 4 and duration.get("maximum") == 15:
            ordinary = copy.deepcopy(duration)
            props["duration"] = {"type": "integer", "description": duration.get("description", ""),
                                 "anyOf": [{"const": -1}, ordinary]}
            repairs.append("seedance-mini-smart-duration")
        tier = props.get("service_tier")
        if isinstance(tier, dict) and "flex" in tier.get("enum", []):
            tier["enum"] = [value for value in tier["enum"] if value != "flex"]
            repairs.append("seedance-mini-no-flex")
    if model == "seedance-2.5" and operation == "video-generation":
        duration = props.get("duration")
        # This endpoint documents the two allowed forms in the description only.
        if isinstance(duration, dict) and duration.keys() == {"type", "description"} and duration["type"] == "integer":
            props["duration"] = {
                **duration,
                "anyOf": [{"const": -1}, {"type": "integer", "minimum": 4, "maximum": 30}],
            }
            repairs.append("seedance-2.5-smart-duration")
    if model == "qwen-audio-3.0-tts-plus":
        hints = props.get("language_hints")
        if isinstance(hints, dict) and hints.get("type") == "array" and isinstance(hints.get("enum"), list):
            values = hints["enum"]
            if values and all(isinstance(value, str) for value in values):
                hints.setdefault("items", {"type": "string"})["enum"] = hints.pop("enum")
                repairs.append("tts-language-hints-items")
        for name in ("aigc_propagator", "aigc_propagate_id"):
            prop = props.get(name)
            if isinstance(prop, dict) and prop.get("default") in ("阿里云UID", "本次请求Request ID"):
                prop.pop("default")
                repairs.append("tts-placeholder-default:" + name)
    if model == "happyhorse-1.1-r2v" and isinstance(props.get("images"), dict):
        images = props["images"]
        items = images.get("items") or {}
        if items.get("type") == "object" and {"type", "url"}.issubset(items.get("properties", {})):
            images["items"] = {"type": "string", "description": "Reference image URL or data URI"}
            repairs.append("happyhorse-r2v-unified-images-strings")
    return schema, repairs


def _video_roles(schema, model=None, scenario=None):
    props = schema.get("properties") or {}
    refs = props.get("input_references") or {}
    role = ((refs.get("items") or {}).get("properties") or {}).get("role") or {}
    values = [value for value in role.get("enum", []) if value in VIDEO_ROLES]
    if values:
        return values, "input_references"
    if "images" in props:
        as_reference = model == "happyhorse-1.1-r2v" or scenario == "reference-to-video"
        return (["reference_image"] if as_reference else ["first_frame"]), "images"
    if "image" in props or "input_reference" in props:
        return ["first_frame"], "image" if "image" in props else "input_reference"
    return [], None


def _scenario_label(key, locale):
    labels = {
        "zh": ("文生视频", "图生视频", "参考生视频"), "en": ("Text to video", "Image to video", "Reference to video"),
        "ja": ("テキストから動画", "画像から動画", "参照素材から動画"), "fr": ("Texte vers vidéo", "Image vers vidéo", "Référence vers vidéo"),
        "ru": ("Видео из текста", "Видео из изображения", "Видео по референсу"), "vi": ("Văn bản thành video", "Ảnh thành video", "Video từ tư liệu tham chiếu"),
    }
    language = normalize_locale(locale).split("-", 1)[0].lower()
    return labels.get(language, labels["en"])[VIDEO_SCENARIOS.index(key)]


def _video_metadata(model, common_schema, variants, locale=None):
    result = {"roles": [], "scenarios": [], "variants": {}}
    for item in variants:
        key = item.get("key")
        if key not in VIDEO_SCENARIOS:
            continue
        schema, repairs = _schema_corrections(model, "video-generation", item.get("request_schema") or common_schema)
        if isinstance(item.get("required"), list):
            schema["required"] = list(item["required"])
        roles, field = _video_roles(schema, model, key)
        if key == "text-to-video":
            roles = []
        elif model.lower().startswith("wan3"):
            frame_roles = {"first_frame", "last_frame"}
            roles = [role for role in roles if (role in frame_roles) == (key == "image-to-video")]
        result["variants"][key] = {"label": _scenario_label(key, locale), "roles": roles, "media_field": field, "request_schema": schema,
                                   "required": item.get("required", schema.get("required", [])), "corrections": repairs}
        result["scenarios"].append(key)
    common_roles, common_field = _video_roles(common_schema, model)
    if not result["scenarios"]:
        if common_roles:
            if not set(common_schema.get("required") or []) & {"image", "images", "input_reference", "input_references"}:
                result["scenarios"].append("text-to-video")
            if "first_frame" in common_roles or common_field in {"image", "images", "input_reference"}:
                result["scenarios"].append("image-to-video")
            if any(role.startswith("reference_") for role in common_roles):
                result["scenarios"].append("reference-to-video")
        elif common_schema.get("properties"):
            result["scenarios"].append("text-to-video")
        for scenario in result["scenarios"]:
            result["variants"][scenario] = {"label": _scenario_label(scenario, locale), "roles": [] if scenario == "text-to-video" else common_roles,
                "media_field": common_field, "request_schema": copy.deepcopy(common_schema),
                "required": list(common_schema.get("required", [])), "corrections": []}
    # The union is for visible sockets; validation uses the selected scene.
    result["roles"] = [role for role in VIDEO_ROLES if role in common_roles or any(role in entry["roles"] for entry in result["variants"].values())]
    return result


class EasyAPIClient:
    def __init__(self, profile=None, api_key: str | None = None, *, base_url: str | None = None,
                 timeout: float = 1800):
        profile = profile or {}
        if isinstance(profile, str):
            profile = {"base_url": profile}
        self.base_url = normalize_base_url(base_url or profile.get("base_url", "https://token.easyapi.com"))
        self.api_key = api_key if api_key is not None else str(profile.get("api_key") or "")
        self.client_id = str(profile.get("client_id") or "")
        self.profile_id = profile.get("id", "default")
        self.timeout = timeout
        self.max_response_bytes = 64 * 1024 * 1024
        self._sessions = {}
        self._cache = {}
        self._catalog_loads = {}
        self._invalidated = False

    def invalidate(self):
        # Do not abort an already submitted request: it may still incur a charge.
        # No further HTTP/WS request may reuse this snapshot of a changed profile.
        self._invalidated = True
        self._cache.clear()

    def _assert_current(self):
        if self._invalidated:
            raise EasyAPIError("Connection profile changed; resume using the current profile")

    async def session(self) -> aiohttp.ClientSession:
        self._assert_current()
        loop = asyncio.get_running_loop()
        current = self._sessions.get(loop)
        if current is None or current.closed:
            current = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout, connect=30),
                                            cookie_jar=aiohttp.DummyCookieJar(), trust_env=False)
            self._sessions[loop] = current
        return current

    async def close(self):
        loop = asyncio.get_running_loop()
        pending = [task for task in self._catalog_loads.values() if task.get_loop() is loop]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for session in self._sessions.values():
            if not session.closed:
                await session.close()
        self._sessions.clear()

    def url(self, path: str) -> str:
        return self.base_url + validate_path(path)

    def headers(self) -> dict:
        self._assert_current()
        return credential_headers(self.api_key, self.client_id)

    async def request(self, method, path, *, body=None, files=None, query=None, stream=False,
                      locale=None, authenticated=True, on_event=None) -> APIResponse:
        method = str(method).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported HTTP method")
        url = self.url(path)
        session = await self.session()
        headers = (self.headers() if authenticated else {}) | {"Accept": "text/event-stream" if stream else "application/json"}
        if locale is not None:
            headers["Accept-Language"] = normalize_locale(locale)
        kwargs = {"params": query, "headers": headers, "allow_redirects": False}
        with contextlib.ExitStack() as opened:
            if files:
                form = aiohttp.FormData()
                for name, value in (body or {}).items():
                    if value is None:
                        continue
                    form.add_field(str(name), json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value))
                for entry in files:
                    if isinstance(entry, dict):
                        entry = UploadFile(**entry)
                    filename = entry.filename or Path(entry.path).name
                    handle = opened.enter_context(open(entry.path, "rb"))
                    form.add_field(entry.field, handle, filename=filename,
                                   content_type=entry.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
                kwargs["data"] = form
            elif body is not None:
                kwargs["json"] = body
            try:
                async with session.request(method, url, **kwargs) as response:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    result = APIResponse(status=response.status, headers=dict(response.headers), content_type=content_type)
                    if content_type == "text/event-stream":
                        result.data = await self._read_sse(response, on_event=on_event)
                    else:
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            size += len(chunk)
                            if size > self.max_response_bytes:
                                raise EasyAPIError("Response exceeded the memory limit; use streamed content download")
                            chunks.append(chunk)
                        result.content = b"".join(chunks)
                        if "json" in content_type:
                            try:
                                result.data = json.loads(result.content)
                            except (ValueError, UnicodeError) as exc:
                                raise EasyAPIError("Gateway returned invalid JSON", status=response.status) from exc
                            result.content = b""
                        elif content_type.startswith("text/"):
                            result.data = result.content.decode("utf-8", errors="replace")
                    result.data = redact(result.data, self.api_key)
                    business_error = isinstance(result.data, dict) and (result.data.get("error") or result.data.get("success") is False)
                    if response.status >= 300 or business_error:
                        data = result.data if isinstance(result.data, dict) else {}
                        error = data.get("error") or data.get("message") or f"Gateway HTTP {response.status}"
                        if isinstance(error, dict):
                            error = error.get("message") or "Gateway rejected request"
                        raise EasyAPIError(str(error)[:1000], status=response.status, response=result)
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise EasyAPIError(f"Gateway connection failed ({type(exc).__name__}); a submitted task may still exist", retryable=True) from exc

    async def _read_sse(self, response, on_event=None):
        events, data_lines, event_name, event_id = [], [], "message", None
        byte_count, buffer = 0, ""
        decoder = codecs.getincrementaldecoder("utf-8")()

        async def dispatch():
            nonlocal data_lines, event_name
            if not data_lines:
                return
            joined = "\n".join(data_lines)
            data_lines = []
            if joined != "[DONE]":
                try:
                    value = json.loads(joined)
                except ValueError:
                    value = joined
                value = redact(value, self.api_key)
                if event_name == "error" or (isinstance(value, dict) and (value.get("error") or value.get("type") == "error")):
                    raise EasyAPIError("Gateway stream failed: " + str(value)[:1000])
                event = {"event": event_name, "id": event_id, "data": value}
                events.append(event)
                if on_event:
                    pending = on_event(event)
                    if inspect.isawaitable(pending):
                        await pending
            event_name = "message"

        async def read_line(line):
            nonlocal event_name, event_id
            if not line:
                await dispatch()
                return
            if line.startswith(":"):
                return
            key, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if key == "data":
                data_lines.append(value)
            elif key == "event":
                event_name = value
            elif key == "id":
                event_id = value

        async for raw in response.content.iter_chunked(65536):
            byte_count += len(raw)
            if byte_count > 128 * 1024 * 1024:
                raise EasyAPIError("Event stream exceeded the response limit")
            buffer += decoder.decode(raw)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                await read_line(line.rstrip("\r"))
        buffer += decoder.decode(b"", final=True)
        if buffer:
            await read_line(buffer.rstrip("\r"))
        await dispatch()
        return {"events": events}

    async def upload_media(self, path, *, kind=None, role=None, mime=None) -> dict:
        response = await self.request("POST", "/v1/media", files=[UploadFile("file", path, content_type=mime)])
        data = response.data or {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict) or not data.get("url"):
            raise EasyAPIError("Media upload did not return a URL")
        return data

    async def download(self, url: str, destination: str | Path, *, max_bytes=4 * 1024**3) -> Path:
        """Stream result content; credentials follow only the configured gateway origin."""
        current = str(url)
        if current.startswith("/"):
            current = self.base_url + current
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        session = await self.session()
        try:
            for _ in range(6):
                parsed = urlsplit(current)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("Result URL must be an HTTP(S) URL without credentials")
                authenticated = origin(current) == origin(self.base_url)
                if authenticated:
                    try:
                        # A media URL cannot turn an administrator's key into a dashboard request.
                        validate_path(parsed.path)
                    except ValueError:
                        authenticated = False
                headers = self.headers() if authenticated else {}
                async with session.get(current, headers=headers, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise EasyAPIError("Result redirect has no destination")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise EasyAPIError(f"Result download failed (HTTP {response.status})", status=response.status)
                    count = 0
                    with temporary.open("wb") as target:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            count += len(chunk)
                            if count > max_bytes:
                                raise EasyAPIError("Downloaded media exceeded the file limit")
                            target.write(chunk)
                    os.replace(temporary, destination)
                    return destination
            raise EasyAPIError("Too many result redirects")
        finally:
            temporary.unlink(missing_ok=True)

    async def _metadata(self, path, *, query=None, locale=None, refresh=False):
        locale = normalize_locale(locale)
        cache_key = ("metadata", path, tuple(sorted((query or {}).items())), locale)
        pending = self._pending_catalog_load(cache_key, False)
        if pending:
            return copy.deepcopy(await asyncio.shield(pending))
        cached = self._cache.get(cache_key)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return copy.deepcopy(cached[1])
        value = await self._share_catalog_load(cache_key, False, self._fetch_metadata(cache_key, path, query, locale))
        return copy.deepcopy(value)

    async def _fetch_metadata(self, cache_key, path, query, locale):
        try:
            response = await self.request("GET", path, query=query, locale=locale, authenticated=False)
            payload = response.data or {}
            value = payload.get("data", payload) if isinstance(payload, dict) else {}
        except EasyAPIError as exc:
            if exc.status not in {0, 400, 401, 403, 404, 429, 500, 502, 503, 504}:
                raise
            value = None
        self._assert_current()
        self._cache[cache_key] = time.monotonic(), value
        return value

    async def _public_catalog(self, *, locale, refresh=False):
        pricing, schemas = await asyncio.gather(
            self._metadata("/pricing", locale=locale, refresh=refresh),
            self._metadata("/pricing/schema", query={"all": "1"}, locale=locale, refresh=refresh))
        rows = pricing if isinstance(pricing, list) else (pricing or {}).get("items", [])
        result = {str(item.get("model_name") or item.get("id")): copy.deepcopy(item)
                  for item in rows if isinstance(item, dict) and (item.get("model_name") or item.get("id"))}
        for item in (schemas or {}).get("items", []) if isinstance(schemas, dict) else []:
            if isinstance(item, dict) and item.get("model"):
                result.setdefault(item["model"], {}).update(copy.deepcopy(item))
        return result

    async def discover_models(self, family=None, operation=None, *, locale=None, refresh=False) -> list[dict]:
        """The API-key catalog is the allow set; public metadata can only enrich it."""
        self._assert_current()
        locale = normalize_locale(locale)
        if operation:
            requested = get_protocol(operation)
            if family and canonical_family(family) and requested["family"] != canonical_family(family):
                raise ValueError("Operation does not belong to the requested node family")
        want = canonical_family(family)
        if family and want is None:
            raise ValueError("Unknown node family")
        cache_key = ("models", want, operation, locale)
        pending = self._pending_catalog_load(cache_key, refresh)
        if pending:
            return copy.deepcopy(await asyncio.shield(pending))
        cached = self._cache.get(cache_key)
        if not refresh and self._catalog_fresh(cached):
            return copy.deepcopy(cached[1])
        if not self.api_key:
            raise EasyAPIError("Configure an API key before listing permitted models", status=401)
        rows = await self._share_catalog_load(cache_key, refresh, self._discover_rows(want, operation, locale, refresh))
        return copy.deepcopy(rows)

    def _pending_catalog_load(self, key, refresh):
        loop = asyncio.get_running_loop()
        return self._catalog_loads.get((loop, key, True)) or (
            None if refresh else self._catalog_loads.get((loop, key, False)))

    async def _share_catalog_load(self, key, refresh, load):
        task = asyncio.create_task(load)
        key = (task.get_loop(), key, refresh)
        self._catalog_loads[key] = task

        def completed(result):
            self._catalog_loads.pop(key, None)
            # A disconnected browser waiter should neither cancel discovery nor leave an unhandled error.
            if not result.cancelled():
                result.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    @staticmethod
    def _catalog_fresh(cached):
        return cached is not None and time.monotonic() - cached[0] < MODEL_CATALOG_TTL

    def model_catalog_cache_seconds(self, locale):
        cached = self._cache.get(("allowed-models", normalize_locale(locale)))
        return max(0, int(cached[0] + MODEL_CATALOG_TTL - time.monotonic())) if cached else 0

    def _discard_model_catalog(self, locale=None):
        for key in tuple(self._cache):
            if isinstance(key, tuple) and key[0] in {"models", "allowed-models"} and (locale is None or key[-1] == locale):
                self._cache.pop(key, None)

    async def _allowed_models(self, locale, refresh):
        scope_key = ("allowed-models", locale)
        pending = self._pending_catalog_load(scope_key, False)
        if pending:
            return await asyncio.shield(pending)
        scoped = self._cache.get(scope_key)
        if not refresh and self._catalog_fresh(scoped):
            return scoped
        return await self._share_catalog_load(scope_key, False, self._fetch_allowed_models(locale))

    async def model_billing_row(self, model, locale):
        _, rows = await self._allowed_models(normalize_locale(locale), False)
        row = next((row for row in rows if row["id"] == model), None)
        return copy.deepcopy(row)

    async def _fetch_allowed_models(self, locale):
        # Public metadata may enrich the authenticated catalogue. It cannot supply permission.
        try:
            response = await self.request("GET", "/v1/models", locale=locale)
        except EasyAPIError as exc:
            if exc.status in {401, 403}:
                self._discard_model_catalog()
            raise
        self._assert_current()
        payload = response.data or {}
        allowed = payload.get("data", []) if isinstance(payload, dict) else None
        if not isinstance(allowed, list):
            raise EasyAPIError("Model catalog is not a list")
        allowed = [dict(item) if isinstance(item, dict) else {"id": item}
                   for item in allowed if isinstance(item, (dict, str))]
        allowed = [item for item in allowed if isinstance(item.get("id"), str) and item["id"]]
        self._discard_model_catalog(locale)
        scope = time.monotonic(), allowed
        self._cache[("allowed-models", locale)] = scope
        return scope

    async def _discover_rows(self, want, operation, locale, refresh):
        scope_key = ("allowed-models", locale)
        while True:
            scope = await self._allowed_models(locale, refresh)
            rows = await self._classify_models(scope[1], want, operation, locale, refresh) if scope[1] else []
            self._assert_current()
            if self._cache.get(scope_key) is scope:
                self._cache[("models", want, operation, locale)] = scope[0], rows
                return rows
            # A concurrent refresh replaced the permission set while metadata was loading.
            refresh = False

    async def _classify_models(self, allowed, want, operation, locale, refresh):
        public = await self._public_catalog(locale=locale, refresh=refresh)
        semaphore = asyncio.Semaphore(6)

        async def enrich(row):
            identifier = row["id"]
            extra = public.get(identifier) or {}
            category = canonical_family(row.get("category")) or canonical_family(extra.get("category"))
            async with semaphore:
                docs = await self._metadata("/pricing/docs", query={"model_name": identifier}, locale=locale, refresh=refresh)
            docs = docs if isinstance(docs, dict) else {}
            category = canonical_family(docs.get("category")) or category
            if not category:
                params = row.get("params") or extra.get("params") or {}
                categories = [key for key in params if canonical_family(key)] if isinstance(params, dict) else []
                if len(categories) == 1:
                    category = canonical_family(categories[0])
            candidates = ((docs, "docs"), (row, "model"), (extra, "public-schema"))
            operations, authority = set(), None
            for candidate, source in candidates:
                entries = _endpoint_entries(candidate)
                capabilities, explicit = _capability_operations(candidate)
                if entries or explicit:
                    operations, authority = _entry_operations(entries) | capabilities, source
                    break
            if authority is None:
                endpoint_types = row.get("supported_endpoint_types") or extra.get("supported_endpoint_types") or []
                # Channel-level unions are not model capabilities. They may
                # provide hints for advanced selection but never authorize an
                # audio subtype or inject another family into a normal picker.
                hints = _entry_operations([{"type": value} for value in endpoint_types if isinstance(value, str)])
                operations = hints
            own_operations, _ = _capability_operations(row)
            cross_family_declared = bool(operation and operation in operations and
                                         (authority == "docs" or operation in own_operations))
            if want and category and category != want and not cross_family_declared:
                return None
            if category and not cross_family_declared:
                operations = {value for value in operations if any(p["id"] == value and p["family"] == category for p in PROTOCOLS)}
            if operation and authority and operation not in operations:
                return None
            enriched = copy.deepcopy(row)
            for field in ("name", "description", "architecture", "capabilities", "params", "endpoints",
                          "input_modalities", "inputModalities", "tags", "feature_tags"):
                if not enriched.get(field) and extra.get(field) is not None:
                    enriched[field] = copy.deepcopy(extra[field])
            enriched.update(id=identifier, display_name=row.get("name") or extra.get("name") or identifier,
                            category=category or "unknown", operations=sorted(operations),
                            capability_status="declared" if authority in {"docs", "model"} else "inferred" if authority else "unknown",
                            capability_source=authority or "channel-hints", allowed=True)
            if operation and not authority:
                enriched["operation_supported"] = None
            elif operation:
                enriched["operation_supported"] = True
            return enriched

        return [item for item in await asyncio.gather(*(enrich(row) for row in allowed)) if item is not None]

    async def model_schema(self, model, operation=None, *, locale=None, variant=None, refresh=False) -> dict:
        self._assert_current()
        locale = normalize_locale(locale)
        variant = str(variant or "").strip() or None
        if variant not in (*VIDEO_SCENARIOS, None, "auto"):
            # Other protocol variants are platform-declared keys, never paths.
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", variant or ""):
                raise ValueError("Invalid schema variant")
        if variant == "auto":
            variant = None
        cache_key = ("schema", model, operation, variant, locale)
        cached = self._cache.get(cache_key)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return copy.deepcopy(cached[1])
        requested = get_protocol(operation) if operation else None
        result = {"model": model, "operation": operation, "locale": locale,
                  "request_schema": {"type": "object", "properties": {}, "additionalProperties": True},
                  "required": [], "source": "unavailable", "docs": None, "params": None,
                  "matched": False, "schema_operation": None, "variants": [], "selected_variant": None,
                  "corrections": [], "warnings": []}
        docs = await self._metadata("/pricing/docs", query={"model_name": model}, locale=locale, refresh=refresh)
        docs = docs if isinstance(docs, dict) else {}
        result["docs"] = docs or None
        entries = _endpoint_entries(docs)
        variants = [value for value in docs.get("variants") or [] if isinstance(value, dict)]
        if not variants:
            variants = [value for entry in docs.get("endpoints", []) if isinstance(entry, dict)
                        for value in entry.get("variants") or [] if isinstance(value, dict)]
        relevant_variants = [value for value in variants if not requested or _entry_matches(value, requested)]
        result["variants"] = copy.deepcopy(relevant_variants)
        selected = None
        endpoint = next((entry for entry in docs.get("endpoints", [])
                         if isinstance(entry, dict) and (not requested or _entry_matches(entry, requested))), None)
        if variant:
            selected = next((entry for entry in relevant_variants if entry.get("key") == variant), None)
            if selected is None:
                result["warnings"].append("Requested variant is not declared for this model and operation")
                # An incompatible connected slot can infer a different scene.
                # Keep the common model form available while the user fixes it.
                selected = endpoint
        else:
            exact = next((entry for entry in relevant_variants if entry.get("key") == operation), None)
            selected = exact or endpoint or (relevant_variants[0] if len(relevant_variants) == 1 else None)
        if selected is not None:
            result.update(matched=True, schema_operation=selected.get("key", operation),
                          selected_variant=selected.get("key") if selected is not endpoint else None)
            schema = selected.get("request_schema") or docs.get("request_schema") or {}
            if schema.get("properties"):
                result.update(request_schema=copy.deepcopy(schema), required=selected.get("required", schema.get("required", [])), source="docs")
        if entries and requested and endpoint is None and not relevant_variants:
            result["source"] = "docs-no-operation-match"
        elif not result["request_schema"].get("properties"):
            # Prefer the endpoint's exact key (api-video/openai/etc.) to the UI
            # operation id when requesting platform endpoint_params overrides.
            endpoint_key = (endpoint or {}).get("key") or (endpoint or {}).get("type") or operation
            query = {"model_name": model}
            if endpoint_key:
                query["endpoint"] = endpoint_key
            data = await self._metadata("/pricing/schema", query=query, locale=locale, refresh=refresh)
            if isinstance(data, dict):
                documented = _endpoint_entries(data)
                matches = not requested or not documented or any(_entry_matches(value, requested) for value in documented)
                schema = data.get("request") or data.get("request_schema") or {}
                if matches and schema.get("properties") and (not variant or selected is not None):
                    result.update(request_schema=copy.deepcopy(schema), required=schema.get("required", []),
                                  source="schema", matched=True, schema_operation=endpoint_key)
                elif documented and not matches:
                    result["source"] = "schema-no-operation-match"
            if not result["request_schema"].get("properties") and result["source"] not in {"docs-no-operation-match", "schema-no-operation-match"}:
                detail = await self._metadata("/pricing/detail", query={"model_name": model}, locale=locale, refresh=refresh)
                if isinstance(detail, dict):
                    params = detail.get("params") or {}
                    result["params"] = params
                    category = canonical_family(detail.get("category"))
                    block = params.get(category) or next((value for value in params.values() if isinstance(value, dict) and value.get("properties")), {})
                    overrides = block.get("endpoint_params") or {}
                    alias = next((key for key, value in _ENDPOINT_ALIASES.items() if value == operation), None)
                    override = overrides.get(endpoint_key) or overrides.get(operation) or overrides.get(alias) or {}
                    props = dict(block.get("properties") or {})
                    props.update(override.get("properties") or {})
                    expected = {"text": "chat", "image": "image-generation", "video": "video-generation", "audio": "audio-speech"}.get(category)
                    matched = not operation or bool(override) or operation == expected or result["matched"]
                    if props and matched and not variant:
                        normalized = normalize_parameter_schema({"type": "object", "properties": props})
                        result.update(request_schema=normalized, required=normalized.get("required", []), source="detail",
                                      matched=True, schema_operation=operation)
        schema, repairs = _schema_corrections(model, operation, result["request_schema"])
        result.update(request_schema=schema, required=list(schema.get("required", result["required"])), corrections=repairs)
        if result["required"] and "required" not in schema:
            schema["required"] = list(result["required"])
        is_video = requested and requested["family"] == "video" or canonical_family(docs.get("category")) == "video"
        if is_video:
            result["video"] = _video_metadata(model, schema, relevant_variants, locale)
            if variant in result["video"]["variants"]:
                active = result["video"]["variants"][variant]
                result.update(request_schema=copy.deepcopy(active["request_schema"]), required=list(active["required"]),
                              selected_variant=variant, matched=True)
        result["declared"] = bool(result["request_schema"].get("properties"))
        result["fallback"] = "basic-and-advanced-json" if not result["declared"] or not result["matched"] else None
        if result["fallback"]:
            result["form_schema"] = basic_form_schema(operation)
        self._cache[cache_key] = time.monotonic(), copy.deepcopy(result)
        return result
