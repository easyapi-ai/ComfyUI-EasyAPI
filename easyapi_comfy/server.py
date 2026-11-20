"""Same-origin configuration and metadata routes for ComfyUI."""
from __future__ import annotations

import asyncio
import functools
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .billing import connection_balance, model_estimate
from .client import EasyAPIError, normalize_locale
from .prompt_tools import prompt_models, transform_prompt, prepare_wan_request, run_wan_request, resolve_prompt_media
from .protocols import PROTOCOLS
from .runtime import get_runtime


_sessions = {}
_registered = set()
_SECURE_COOKIE = "easyapi_management_session"
_HTTP_COOKIE = "easyapi_management_session_http"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _normalize_origin(value):
    origin = urlsplit(value)
    if (origin.scheme not in {"http", "https"} or not origin.hostname or origin.username is not None
            or origin.path not in {"", "/"} or origin.query or origin.fragment):
        raise ValueError("An HTTP or HTTPS origin is required")
    host = f"[{origin.hostname}]" if ":" in origin.hostname else origin.hostname
    port = origin.port
    default_port = 443 if origin.scheme == "https" else 80
    authority = f"{host}:{port}" if port is not None and port != default_port else host
    return f"{origin.scheme}://{authority}"


def _direct_origin(request):
    return _normalize_origin(f"{request.scheme}://{request.host}")


def _public_origin():
    configured = os.environ.get("EASYAPI_PUBLIC_ORIGIN")
    return _normalize_origin(configured) if configured else None


def validate_origin(request):
    supplied = request.headers.get("Origin")
    if not supplied:
        if request.method not in {"GET", "HEAD"} or request.headers.get("Upgrade", "").lower() == "websocket":
            raise web.HTTPForbidden(text="A same-origin browser request is required")
        return
    try:
        origin = _normalize_origin(supplied)
        public = _public_origin()
        if origin == public:
            return origin
        if origin == _direct_origin(request):
            host = urlsplit(origin).hostname
            # A proxy pin also limits direct management to known hosts.
            if public is None or host == urlsplit(public).hostname or host in _LOOPBACK_HOSTS:
                return origin
    except ValueError:
        pass
    raise web.HTTPForbidden(text="Cross-origin EasyAPI request rejected")


def _management_origin(request):
    origin = validate_origin(request)
    if origin:
        return origin
    # Older browsers/clients can bootstrap with GET, which may omit Origin.
    # The proxy normally preserves the public Host even when it terminates TLS.
    public = _public_origin()
    if public and public == _normalize_origin(f"{urlsplit(public).scheme}://{request.host}"):
        return public
    return _direct_origin(request)


def _session_cookie(origin):
    return _SECURE_COOKIE if origin.startswith("https://") else _HTTP_COOKIE


def require_management(request):
    origin = _management_origin(request)
    sid = request.cookies.get(_session_cookie(origin), "")
    session = _sessions.get(sid)
    nonce = request.headers.get("X-EasyAPI-Management", "")
    if request.headers.get("Upgrade", "").lower() == "websocket":
        nonce = nonce or request.query.get("nonce", "")
    if (not session or session["origin"] != origin or session["expires"] < time.monotonic()
            or not secrets.compare_digest(str(nonce), session["nonce"])):
        raise web.HTTPForbidden(text="Refresh the EasyAPI settings page before managing profiles or sessions")
    return sid


def _guard(handler):
    @functools.wraps(handler)
    async def wrapped(request):
        try:
            validate_origin(request)
            return await handler(request)
        except web.HTTPException:
            raise
        except EasyAPIError as exc:
            status = exc.status if 400 <= exc.status < 600 else 502
            return web.json_response({"error": str(exc), "code": "gateway_authentication" if status == 401 else "gateway_error"}, status=status)
        except (ValueError, KeyError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return wrapped


def build_routes(runtime=None):
    rt = runtime or get_runtime()
    routes = web.RouteTableDef()
    presets = None
    authorization = None

    def preset_store():
        nonlocal presets
        if presets is None:
            from .presets import PresetStore
            presets = PresetStore(rt.state_dir / "presets.json")
        return presets

    def authorization_broker():
        nonlocal authorization
        if authorization is None:
            from .authorization import AuthorizationBroker
            authorization = AuthorizationBroker(rt.profiles, rt.state_dir)
        return authorization

    def authorization_owner(request):
        owner = require_management(request)
        origin = urlsplit(request.headers.get("Origin", ""))
        if origin.scheme != "https" and origin.hostname not in _LOOPBACK_HOSTS:
            raise web.HTTPForbidden(text="Open ComfyUI using HTTPS or localhost to authorize with EasyAPI.")
        return owner

    @routes.get("/easyapi/bootstrap")
    @routes.post("/easyapi/bootstrap")
    @_guard
    async def bootstrap(request):
        origin = _management_origin(request)
        cookie = _session_cookie(origin)
        now = time.monotonic()
        for sid in [sid for sid, value in _sessions.items() if value["expires"] < now]:
            del _sessions[sid]
        sid = request.cookies.get(cookie, "")
        if sid not in _sessions or _sessions[sid]["origin"] != origin:
            sid = secrets.token_urlsafe(32)
            _sessions[sid] = {"nonce": secrets.token_urlsafe(32), "origin": origin}
        _sessions[sid]["expires"] = now + 3600
        response = web.json_response({"management_nonce": _sessions[sid]["nonce"]})
        # ComfyUI serves extension APIs through both /easyapi and /api/easyapi.
        # The same management session must accompany either HTTP or WS prefix.
        # Cookie scope ignores ports, so HTTP cannot share the HTTPS cookie name.
        response.set_cookie(cookie, sid, httponly=True, samesite="Strict", secure=cookie == _SECURE_COOKIE, path="/", max_age=3600)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.get("/easyapi/profiles")
    @_guard
    async def profiles(request):
        return web.json_response({"profiles": rt.profiles.list_public(), "default_profile_id": rt.profiles.default_profile_id()})

    @routes.post("/easyapi/default-profile")
    @_guard
    async def default_profile(request):
        require_management(request)
        value = await request.json()
        if not isinstance(value, dict) or "profile_id" not in value:
            raise ValueError("profile_id is required; use null to clear the default")
        selected = rt.profiles.set_default(value.get("profile_id"))
        return web.json_response({"default_profile_id": selected})

    @routes.post("/easyapi/profiles/{profile_id}")
    @_guard
    async def save_profile(request):
        require_management(request)
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Connection settings must be a JSON object")
        public = rt.profiles.save(request.match_info["profile_id"], value["base_url"], value.get("api_key", ""), value.get("name"),
                                  make_default=value.get("make_default") is True)
        return web.json_response({"profile": public, "default_profile_id": rt.profiles.default_profile_id()})

    @routes.delete("/easyapi/profiles/{profile_id}")
    @_guard
    async def delete_profile(request):
        require_management(request)
        rt.profiles.delete(request.match_info["profile_id"])
        return web.json_response({"ok": True, "default_profile_id": rt.profiles.default_profile_id()})

    @routes.post("/easyapi/profiles/{profile_id}/test")
    @_guard
    async def test_profile(request):
        require_management(request)
        profile_id = request.match_info["profile_id"]
        profile = rt.profiles.get(profile_id)
        checked_at = int(time.time())
        try:
            response = await asyncio.wait_for(rt.client(profile_id).request("GET", "/v1/models"), timeout=15)
            data = response.data
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise EasyAPIError("The gateway did not return a model list.", status=502)
        except (EasyAPIError, TimeoutError) as exc:
            rt.profiles.record_check(profile, False, checked_at)
            status = exc.status if isinstance(exc, EasyAPIError) and 400 <= exc.status < 600 else 502
            return web.json_response({"ok": False, "checked_at": checked_at,
                "code": "gateway_authentication" if status == 401 else "gateway_error",
                "error": "The gateway rejected this credential." if status == 401 else "The gateway could not be reached or did not return a model list.",
                "profile": rt.profiles.public_status(profile)}, status=status)
        rt.profiles.record_check(profile, True, checked_at)
        return web.json_response({"ok": True, "checked_at": checked_at, "model_count": len(data["data"]),
                                  "profile": rt.profiles.public_status(profile)})

    @routes.get("/easyapi/profiles/{profile_id}/balance")
    @_guard
    async def balance(request):
        require_management(request)
        try:
            result = await connection_balance(rt.client(request.match_info["profile_id"]))
        except TimeoutError as error:
            raise EasyAPIError("Balance query timed out", status=504) from error
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    @routes.post("/easyapi/billing/estimate")
    @_guard
    async def estimate(request):
        require_management(request)
        value = await request.json()
        if not isinstance(value, dict) or not isinstance(value.get("profile_id"), str) or not value["profile_id"]:
            raise ValueError("A saved connection is required for a billing estimate")
        try:
            result = await model_estimate(rt.client(value["profile_id"]), value.get("facts"), value.get("locale"),
                                          rates_only=value.get("rates_only") is True)
        except TimeoutError as error:
            raise EasyAPIError("Billing estimate timed out", status=504) from error
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    @routes.get("/easyapi/presets")
    @_guard
    async def list_presets(request):
        profile = rt.profiles.get(request.query.get("profile_id"))
        return web.json_response(preset_store().list(profile["id"], request.query.get("operation")))

    @routes.post("/easyapi/presets")
    @routes.put("/easyapi/presets/{preset_id}")
    @_guard
    async def save_preset(request):
        require_management(request)
        value = await request.json()
        if not isinstance(value, dict) or not value.get("profile_id"):
            raise ValueError("A connection and preset are required")
        rt.profiles.get(value["profile_id"])
        saved = preset_store().save(value, request.match_info.get("preset_id"))
        return web.json_response({"preset": saved})

    @routes.delete("/easyapi/presets/{preset_id}")
    @_guard
    async def delete_preset(request):
        require_management(request)
        profile_id = request.query.get("profile_id")
        if not profile_id:
            raise ValueError("profile_id is required")
        rt.profiles.get(profile_id)
        preset_store().delete(request.match_info["preset_id"], profile_id)
        return web.json_response({"ok": True})

    @routes.post("/easyapi/auth/start")
    @_guard
    async def start_authorization(request):
        owner = authorization_owner(request)
        value = await request.json()
        if not isinstance(value, dict) or not isinstance(value.get("profile_id"), str):
            raise ValueError("Choose a connection ID for the authorization")
        result = await authorization_broker().start(owner, value["profile_id"], value.get("name"),
                                                      make_default=value.get("make_default") is True)
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    @routes.post("/easyapi/auth/{flow_id}/poll")
    @_guard
    async def poll_authorization(request):
        owner = authorization_owner(request)
        result = await authorization_broker().poll(owner, request.match_info["flow_id"])
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    @routes.delete("/easyapi/auth/{flow_id}")
    @_guard
    async def cancel_authorization(request):
        owner = authorization_owner(request)
        result = await authorization_broker().cancel(owner, request.match_info["flow_id"])
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    @routes.get("/easyapi/models")
    @_guard
    async def models(request):
        client = rt.client(request.query.get("profile_id"))
        locale = normalize_locale(request.query.get("locale") or request.query.get("lang"))
        operation = request.query.get("operation")
        rows = await client.discover_models(request.query.get("family"), operation, locale=locale,
                                            refresh=request.query.get("refresh") in {"1", "true"})
        return web.json_response({"models": rows, "empty": not rows, "authority": "api_key", "operation": operation,
                                  "locale": locale, "cache_ttl": client.model_catalog_cache_seconds(locale)},
                                 headers={"Cache-Control": "no-store"})

    @routes.get("/easyapi/prompt/models")
    @_guard
    async def list_prompt_models(request):
        locale = normalize_locale(request.query.get("locale"))
        _, rows = await prompt_models(rt, request.query.get("profile_id"), locale)
        return web.json_response({"models": rows}, headers={"Cache-Control": "no-store"})

    @routes.post("/easyapi/prompt/transform")
    @_guard
    async def edit_prompt(request):
        require_management(request)
        value = await request.json()
        locale = normalize_locale(value.get("locale") if isinstance(value, dict) else None)
        if isinstance(value, dict) and value.get("action") in {"wan_analyze", "wan_tune"}:
            client, body = await prepare_wan_request(rt, value, locale)
            if not body["stream"]:
                result = await run_wan_request(client, body, locale)
                return web.json_response(result, headers={"Cache-Control": "no-store"})
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                                                   "X-Accel-Buffering": "no"})
            await response.prepare(request)

            async def emit(event, data):
                await response.write(("event: " + event + "\ndata: " +
                                      json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8"))
            try:
                await emit("stage", {"stage": value["action"]})
                pending = asyncio.create_task(run_wan_request(client, body, locale, emit=emit))
                try:
                    while not pending.done():
                        if request.transport is None or request.transport.is_closing():
                            pending.cancel()
                            raise asyncio.CancelledError()
                        await asyncio.wait({pending}, timeout=.1)
                    result = pending.result()
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                await emit("result", result)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                raise
            except (EasyAPIError, ValueError) as error:
                await emit("error", {"message": str(error)})
            await response.write_eof()
            return response
        text = await transform_prompt(rt, value, locale)
        return web.json_response({"text": text}, headers={"Cache-Control": "no-store"})

    @routes.post("/easyapi/prompt/media")
    @_guard
    async def prompt_media(request):
        require_management(request)
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Media settings must be an object.")
        rt.client(value.get("profile_id"))
        return web.json_response(resolve_prompt_media(value, rt.output_dir), headers={"Cache-Control": "no-store"})

    @routes.get("/easyapi/schema")
    @_guard
    async def schema(request):
        model = request.query.get("model", "")
        if not model:
            raise ValueError("model is required")
        result = await rt.client(request.query.get("profile_id")).model_schema(
            model, request.query.get("operation"), locale=request.query.get("locale") or request.query.get("lang"),
            variant=request.query.get("variant") or request.query.get("mode"), refresh=request.query.get("refresh") in {"1", "true"})
        result["schema"] = result["request_schema"]
        return web.json_response(result)

    @routes.get("/easyapi/operations")
    @_guard
    async def operations(request):
        return web.json_response({"operations": PROTOCOLS})

    @routes.get("/easyapi/tasks")
    @_guard
    async def tasks(request):
        return web.json_response({"tasks": rt.tasks.list()})

    @routes.get("/easyapi/tasks/{task_id}")
    @_guard
    async def task(request):
        handle = rt.tasks.get(request.match_info["task_id"], request.query.get("profile_id"))
        if handle is None:
            raise web.HTTPNotFound()
        return web.json_response(handle)

    @routes.get("/easyapi/tasks/{task_id}/result")
    @_guard
    async def task_result(request):
        handle = rt.tasks.get(request.match_info["task_id"], request.query.get("profile_id"))
        if handle is None:
            raise web.HTTPNotFound()
        return web.json_response(handle.get("latest", {}))

    @routes.get("/easyapi/files/{filename}")
    @_guard
    async def file(request):
        filename = request.match_info["filename"]
        root = rt.output_dir.resolve()
        target = (root / filename).resolve()
        if target.parent != root or not target.is_file():
            raise web.HTTPNotFound()
        response = web.FileResponse(target)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return routes


def register_routes():
    try:
        from server import PromptServer
    except ImportError:
        return False
    target = PromptServer.instance.routes
    if id(target) not in _registered:
        target._items.extend(build_routes()._items)
        _registered.add(id(target))
    return True
