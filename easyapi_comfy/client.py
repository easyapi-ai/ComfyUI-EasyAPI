"""Asynchronous transport, discovery and streamed media IO for EasyAPI."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from .profiles import normalize_base_url
from .protocols import get_protocol, validate_path


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


class EasyAPIClient:
    def __init__(self, profile=None, api_key: str | None = None, *, base_url: str | None = None,
                 timeout: float = 1800):
        profile = profile or {}
        if isinstance(profile, str):
            profile = {"base_url": profile}
        self.base_url = normalize_base_url(base_url or profile.get("base_url", "https://token.easyapi.com"))
        self.api_key = api_key if api_key is not None else str(profile.get("api_key") or "")
        self.profile_id = profile.get("id", "default")
        self.timeout = timeout
        self.max_response_bytes = 64 * 1024 * 1024
        self._sessions = {}
        self._cache = {}
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
        for session in self._sessions.values():
            if not session.closed:
                await session.close()
        self._sessions.clear()

    def url(self, path: str) -> str:
        return self.base_url + validate_path(path)

    def headers(self) -> dict:
        self._assert_current()
        return {"Authorization": "Bearer " + self.api_key} if self.api_key else {}

    async def request(self, method, path, *, body=None, files=None, query=None, stream=False) -> APIResponse:
        method = str(method).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported HTTP method")
        url = self.url(path)
        session = await self.session()
        headers = self.headers() | {"Accept": "text/event-stream" if stream else "application/json"}
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
                        result.data = await self._read_sse(response)
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

    async def _read_sse(self, response):
        events, data_lines, event_name, event_id = [], [], "message", None
        byte_count = 0
        async for raw in response.content:
            byte_count += len(raw)
            if byte_count > 128 * 1024 * 1024:
                raise EasyAPIError("Event stream exceeded the response limit")
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    joined = "\n".join(data_lines)
                    if joined != "[DONE]":
                        try:
                            value = json.loads(joined)
                        except ValueError:
                            value = joined
                        if event_name == "error" or (isinstance(value, dict) and (value.get("error") or value.get("type") == "error")):
                            raise EasyAPIError("Gateway stream failed: " + str(redact(value, self.api_key))[:1000])
                        events.append({"event": event_name, "id": event_id, "data": value})
                    data_lines, event_name = [], "message"
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if key == "data":
                data_lines.append(value)
            elif key == "event":
                event_name = value
            elif key == "id":
                event_id = value
        if data_lines and "\n".join(data_lines) != "[DONE]":
            joined = "\n".join(data_lines)
            try:
                joined = json.loads(joined)
            except ValueError:
                pass
            if event_name == "error" or (isinstance(joined, dict) and (joined.get("error") or joined.get("type") == "error")):
                raise EasyAPIError("Gateway stream failed: " + str(redact(joined, self.api_key))[:1000])
            events.append({"event": event_name, "id": event_id, "data": joined})
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

    async def discover_models(self, family=None, *, refresh=False) -> list[dict]:
        self._assert_current()
        cached = self._cache.get("models")
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            rows = cached[1]
        else:
            response = await self.request("GET", "/v1/models")
            rows = (response.data or {}).get("data", [])
            if not isinstance(rows, list):
                raise EasyAPIError("Model catalog is not a list")
            self._cache["models"] = time.monotonic(), copy.deepcopy(rows)
        if family:
            want = str(family).lower()
            rows = [row for row in rows if str(row.get("category") or "").lower() == want]
        return copy.deepcopy(rows)

    async def model_schema(self, model, operation=None, *, refresh=False) -> dict:
        self._assert_current()
        cache_key = (model, operation)
        cached = self._cache.get(cache_key)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return copy.deepcopy(cached[1])
        try:
            requested_protocol = get_protocol(operation) if operation else None
        except ValueError:
            requested_protocol = None
        requested_path = requested_protocol.get("path") if requested_protocol else None
        result = {"model": model, "operation": operation, "request_schema": {"type": "object", "properties": {}, "additionalProperties": True},
                  "required": [], "source": "unavailable", "docs": None, "params": None,
                  "matched": False, "schema_operation": None}
        for path in ("/pricing/docs", "/pricing/schema", "/pricing/detail"):
            try:
                response = await self.request("GET", path, query={"model_name": model})
            except EasyAPIError as exc:
                if exc.status not in {0, 400, 401, 403, 404, 500, 502, 503}:
                    raise
                continue
            payload = response.data or {}
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            if path.endswith("/docs"):
                result["docs"] = data
                variants = data.get("variants") or []
                if not variants:
                    variants = [v for endpoint in data.get("endpoints", []) for v in endpoint.get("variants", [])]
                selected = next((v for v in variants if operation and v.get("key") == operation), None)
                endpoint = next((e for e in data.get("endpoints", [])
                                 if operation and (e.get("key") == operation or (requested_path and e.get("path") == requested_path))), None)
                # A generic video endpoint has several scene variants. Its common
                # schema is safer than imposing the first scene's required fields.
                selected = selected or endpoint
                if not operation:
                    selected = selected or (data.get("endpoints") or variants or [{}])[0]
                if selected is None:
                    result["source"] = "docs-no-operation-match"
                    break
                result.update(matched=True, schema_operation=selected.get("key", operation))
                schema = selected.get("request_schema") or data.get("request_schema") or {}
                if schema.get("properties"):
                    result.update(request_schema=schema, required=selected.get("required", schema.get("required", [])), source="docs", variant=selected)
                    break
            elif path.endswith("/schema"):
                endpoints = data.get("endpoints") or []
                if operation and endpoints and not any(e.get("type") == operation or (requested_path and e.get("path") == requested_path) for e in endpoints):
                    result["source"] = "schema-no-operation-match"
                    break
                schema = data.get("request") or data.get("request_schema") or {}
                if schema.get("properties"):
                    result.update(request_schema=schema, required=schema.get("required", []), source="schema",
                                  matched=bool(not operation or endpoints or result["matched"]), schema_operation=operation)
                    break
            else:
                params = data.get("params") or {}
                result["params"] = params
                category = str(data.get("category") or "").lower()
                block = params.get(category) or next((v for v in params.values() if isinstance(v, dict) and v.get("properties")), {})
                props = dict(block.get("properties") or {})
                override = (block.get("endpoint_params") or {}).get(operation, {})
                props.update(override.get("properties") or {})
                if props:
                    required = [key for key, prop in props.items() if prop.get("required")]
                    default_operation = {"text": "chat", "image": "image-generation", "video": "video-generation", "audio": "audio-speech"}.get(category)
                    matched = bool(not operation or override or operation == default_operation or result["matched"])
                    result.update(request_schema=normalize_parameter_schema({"type": "object", "properties": props, "required": required}), required=required, source="detail",
                                  matched=matched, schema_operation=operation if matched else None)
        result["declared"] = bool(result["request_schema"].get("properties"))
        self._cache[cache_key] = time.monotonic(), copy.deepcopy(result)
        return result
