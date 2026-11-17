"""Same-origin configuration and metadata routes for ComfyUI."""
from __future__ import annotations

import functools
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .client import EasyAPIError
from .protocols import PROTOCOLS
from .runtime import get_runtime


_sessions = {}
_registered = set()
_COOKIE = "easyapi_management_session"


def validate_origin(request):
    supplied = request.headers.get("Origin")
    if not supplied:
        if request.method not in {"GET", "HEAD"} or request.headers.get("Upgrade", "").lower() == "websocket":
            raise web.HTTPForbidden(text="A same-origin browser request is required")
        return
    try:
        actual = urlsplit(supplied)
        expected = urlsplit(os.environ.get("EASYAPI_PUBLIC_ORIGIN") or f"{request.scheme}://{request.host}")
        def normalized(parsed):
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        if actual.username or actual.password or actual.path not in {"", "/"} or actual.query or actual.fragment or normalized(actual) != normalized(expected):
            raise ValueError()
    except ValueError:
        raise web.HTTPForbidden(text="Cross-origin EasyAPI request rejected") from None


def require_management(request):
    validate_origin(request)
    if not request.headers.get("Origin") and (request.method not in {"GET", "HEAD"} or request.headers.get("Upgrade", "").lower() == "websocket"):
        raise web.HTTPForbidden(text="Management requires a same-origin browser")
    session = _sessions.get(request.cookies.get(_COOKIE, ""))
    nonce = request.headers.get("X-EasyAPI-Management", "")
    if request.headers.get("Upgrade", "").lower() == "websocket":
        nonce = nonce or request.query.get("nonce", "")
    if not session or session["expires"] < time.monotonic() or not secrets.compare_digest(str(nonce), session["nonce"]):
        raise web.HTTPForbidden(text="Refresh the EasyAPI settings page before managing profiles or sessions")


def _guard(handler):
    @functools.wraps(handler)
    async def wrapped(request):
        try:
            validate_origin(request)
            return await handler(request)
        except web.HTTPException:
            raise
        except (ValueError, KeyError, EasyAPIError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return wrapped


def build_routes(runtime=None):
    rt = runtime or get_runtime()
    routes = web.RouteTableDef()

    @routes.get("/easyapi/bootstrap")
    @_guard
    async def bootstrap(request):
        now = time.monotonic()
        for sid in [sid for sid, value in _sessions.items() if value["expires"] < now]:
            del _sessions[sid]
        sid = request.cookies.get(_COOKIE, "")
        if sid not in _sessions:
            sid = secrets.token_urlsafe(32)
            _sessions[sid] = {"nonce": secrets.token_urlsafe(32), "expires": now + 3600}
        _sessions[sid]["expires"] = now + 3600
        response = web.json_response({"management_nonce": _sessions[sid]["nonce"]})
        secure = request.secure or os.environ.get("EASYAPI_PUBLIC_ORIGIN", "").startswith("https://")
        # ComfyUI serves extension APIs through both /easyapi and /api/easyapi.
        # The same management session must accompany either HTTP or WS prefix.
        response.set_cookie(_COOKIE, sid, httponly=True, samesite="Strict", secure=secure, path="/", max_age=3600)
        response.headers["Cache-Control"] = "no-store"
        return response

    @routes.get("/easyapi/profiles")
    @_guard
    async def profiles(request):
        return web.json_response({"profiles": rt.profiles.list_public()})

    @routes.post("/easyapi/profiles/{profile_id}")
    @_guard
    async def save_profile(request):
        require_management(request)
        value = await request.json()
        public = rt.profiles.save(request.match_info["profile_id"], value["base_url"], value.get("api_key", ""), value.get("name"))
        return web.json_response({"profile": public})

    @routes.delete("/easyapi/profiles/{profile_id}")
    @_guard
    async def delete_profile(request):
        require_management(request)
        rt.profiles.delete(request.match_info["profile_id"])
        return web.json_response({"ok": True})

    @routes.get("/easyapi/models")
    @_guard
    async def models(request):
        client = rt.client(request.query.get("profile_id", "default"))
        rows = await client.discover_models(request.query.get("family"), refresh=request.query.get("refresh") in {"1", "true"})
        return web.json_response({"models": rows})

    @routes.get("/easyapi/schema")
    @_guard
    async def schema(request):
        model = request.query.get("model", "")
        if not model:
            raise ValueError("model is required")
        result = await rt.client(request.query.get("profile_id", "default")).model_schema(
            model, request.query.get("operation"), refresh=request.query.get("refresh") in {"1", "true"})
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
