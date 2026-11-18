"""Official website authorization, with all private exchange state on the server.

The browser sees only the official consent URL and a local flow status. The
account access token is used in memory to obtain one instance-bound generation
key, then discarded. This module never starts a flow at import or construction.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

from .client import EasyAPIError
from .profiles import normalize_base_url


OFFICIAL_GATEWAY = "https://token.easyapi.com"
OFFICIAL_CONSOLE = "https://www.easyapi.com"
CLIENT_ID_HEADER = "X-EasyAPI-Client-Id"
KEY_LIFETIME = 30 * 86400


class AuthorizationError(EasyAPIError):
    def __init__(self, code, message, *, status=400, retry_after=2):
        super().__init__(message, status=status)
        self.code = code
        self.retry_after = retry_after


@dataclass(repr=False)
class _Flow:
    id: str
    owner_hash: str
    profile_id: str
    name: str
    make_default: bool
    state: str
    verifier: str
    client_id: str
    deadline: float
    created: float
    authorize_url: str
    profile_revision: str | None
    status: str = "pending"
    next_poll: float = 0
    access_token: str = ""
    user_id: int | None = None
    create_attempted: bool = False
    profile: dict | None = None
    code: str = ""
    message: str = ""
    finished: float | None = None
    expiry_handle: asyncio.TimerHandle | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def forget_private(self):
        self.state = self.verifier = self.access_token = ""
        self.user_id = None


class AuthorizationBroker:
    def __init__(self, profiles, state_dir, *, transport=None, gateway_base=OFFICIAL_GATEWAY,
                 console_base=OFFICIAL_CONSOLE, clock=time.monotonic, wall_time=time.time,
                 ttl=300, poll_interval=2, max_flows=32):
        self.profiles = profiles
        self.state_dir = Path(state_dir)
        self.gateway_base = normalize_base_url(gateway_base)
        self.console_base = normalize_base_url(console_base)
        # Nonofficial addresses are permitted only via an explicitly injected
        # test transport. Neither HTTP routes nor profiles select these values.
        if transport is None and (self.gateway_base != OFFICIAL_GATEWAY or self.console_base != OFFICIAL_CONSOLE):
            raise ValueError("Website authorization is available only for the official EasyAPI service")
        self._transport = transport
        self._clock, self._wall_time = clock, wall_time
        self.ttl, self.poll_interval, self.max_flows = ttl, poll_interval, max_flows
        self._flows: dict[str, _Flow] = {}
        self._start_lock = asyncio.Lock()
        self._client_id = None

    def _instance_client_id(self):
        if self._client_id is not None:
            return self._client_id
        path = self.state_dir / "auth-instance.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            identifier = secrets.token_hex(32)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    json.dump({"version": 1, "client_id": identifier}, target)
                    target.flush()
                    os.fsync(target.fileno())
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            identifier = value.get("client_id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
                raise ValueError()
        except (OSError, ValueError, AttributeError):
            raise AuthorizationError("instance_identity_invalid", "The local authorization identity cannot be read; check server storage") from None
        self._client_id = identifier
        return identifier

    @staticmethod
    def _owner_hash(owner):
        return hashlib.sha256(str(owner).encode("utf-8")).hexdigest()

    def _flow(self, owner, flow_id):
        flow = self._flows.get(str(flow_id))
        if flow is None or not secrets.compare_digest(flow.owner_hash, self._owner_hash(owner)):
            return None
        return flow

    def _finish(self, flow, status, *, code="", message=""):
        flow.status, flow.code, flow.message = status, code, message
        flow.finished = self._clock()
        if flow.expiry_handle is not None:
            flow.expiry_handle.cancel()
            flow.expiry_handle = None
        flow.forget_private()
        flow.authorize_url = ""

    def _live(self, flow):
        if flow.status == "pending" and self._clock() >= flow.deadline:
            self._expire(flow)
        return flow.status == "pending"

    def _expire(self, flow):
        if flow.status == "pending":
            self._finish(flow, "expired", code="authorization_expired", message="Authorization expired; start again")

    def _public(self, flow, *, include_url=False):
        self._live(flow)
        result = {"flow_id": flow.id, "status": flow.status,
                  "expires_in": max(0, math.ceil(flow.deadline - self._clock())) if flow.status == "pending" else 0,
                  "retry_after": max(1, math.ceil(flow.next_poll - self._clock())) if flow.status == "pending" else 0}
        if include_url and flow.status == "pending":
            result["authorize_url"] = flow.authorize_url
        if flow.status == "authorized" and flow.profile is not None:
            result["profile"] = dict(flow.profile)
        if flow.code:
            result.update(code=flow.code, message=flow.message)
        return result

    @staticmethod
    def _not_found():
        return {"status": "failed", "code": "authorization_not_found", "message": "Authorization is unavailable; start again"}

    def _prune(self):
        now = self._clock()
        for flow in list(self._flows.values()):
            self._live(flow)
            if flow.finished is not None and now - flow.finished > 600:
                self._flows.pop(flow.id, None)

    async def start(self, owner, profile_id, name, make_default=False):
        if not owner:
            raise AuthorizationError("management_required", "A current management session is required", status=403)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(profile_id)):
            raise ValueError("Profile ID must be 1-64 letters, numbers, underscores or dashes")
        name = str(name or profile_id).replace("\r", " ").replace("\n", " ").strip()[:120]
        async with self._start_lock:
            self._prune()
            try:
                existing_profile = self.profiles.get(str(profile_id))
            except ValueError:
                existing_profile = None
            if existing_profile and existing_profile.get("read_only"):
                return {"status": "failed", "code": "profile_read_only", "message": "This connection is managed by the server environment and cannot be replaced"}
            revision = self.profiles.revision(str(profile_id))
            for existing in self._flows.values():
                if existing.profile_id == profile_id and self._live(existing):
                    if existing.profile_revision != revision:
                        self._finish(existing, "failed", code="profile_changed", message="The connection changed during authorization; start again")
                        continue
                    if secrets.compare_digest(existing.owner_hash, self._owner_hash(owner)):
                        return self._public(existing, include_url=True)
                    return {"status": "failed", "code": "authorization_in_progress", "message": "This connection already has an authorization in progress"}
            if sum(flow.status == "pending" for flow in self._flows.values()) >= self.max_flows:
                raise AuthorizationError("too_many_authorizations", "Too many authorizations are in progress", status=429)
            state, verifier = secrets.token_hex(16), secrets.token_hex(32)
            now = self._clock()
            flow = _Flow(secrets.token_urlsafe(24), self._owner_hash(owner), str(profile_id), name,
                         bool(make_default), state, verifier, self._instance_client_id(), now + self.ttl, now,
                         self.console_base + "/cli/authorize?" + urlencode({"state": state, "client_name": "ComfyUI"}), revision)
            self._flows[flow.id] = flow
            flow.next_poll = now + self.poll_interval
            try:
                data = await self._call("POST", "/cli/session", body={"state": state,
                    "verifier_hash": hashlib.sha256(verifier.encode()).hexdigest(), "client_name": "ComfyUI"})
                if isinstance(data.get("expires_in"), (int, float)):
                    flow.deadline = min(flow.deadline, now + max(1, data["expires_in"]))
            except AuthorizationError as error:
                self._finish(flow, "failed", code=error.code, message=str(error))
            except asyncio.CancelledError:
                self._finish(flow, "cancelled")
                raise
            if self._live(flow):
                flow.expiry_handle = asyncio.get_running_loop().call_later(
                    flow.deadline - self._clock(), self._expire, flow)
            return self._public(flow, include_url=True)

    async def poll(self, owner, flow_id):
        self._prune()
        flow = self._flow(owner, flow_id)
        if flow is None:
            return self._not_found()
        if not self._live(flow):
            return self._public(flow)
        async with flow.lock:
            if not self._live(flow) or self._clock() < flow.next_poll:
                return self._public(flow)
            flow.next_poll = self._clock() + self.poll_interval
            try:
                if not flow.access_token:
                    data = await self._call("GET", "/cli/ticket", query={"state": flow.state, "verifier": flow.verifier})
                    if not self._live(flow):
                        return self._public(flow)
                    if data.get("status") == "pending":
                        return self._public(flow)
                    ticket = data.get("ticket")
                    if data.get("status") != "ready" or not isinstance(ticket, str) or not ticket:
                        raise AuthorizationError("invalid_authorization_response", "The authorization service returned an invalid status")
                    data = await self._call("POST", "/cli/exchange", body={"ticket": ticket})
                    ticket = ""
                    if not self._live(flow):
                        return self._public(flow)
                    access = data.get("access_token")
                    user_id = data.get("user_id")
                    if not isinstance(access, str) or not access or isinstance(user_id, bool) or not str(user_id).isdigit() or int(user_id) <= 0:
                        raise AuthorizationError("invalid_authorization_response", "The authorization service did not return an account")
                    flow.access_token, flow.user_id = access, int(user_id)
                profile = await self._provision(flow)
                if not self._live(flow):
                    return self._public(flow)
                flow.profile = profile
                self._finish(flow, "authorized")
            except AuthorizationError as error:
                if self._live(flow):
                    if error.code == "rate_limited":
                        flow.next_poll = self._clock() + error.retry_after
                    else:
                        self._finish(flow, "failed", code=error.code, message=str(error))
            except (asyncio.CancelledError, KeyboardInterrupt):
                # A dropped browser HTTP poll must not leave an exchange running
                # outside this flow's lock or a reusable account credential.
                if self._live(flow):
                    self._finish(flow, "failed", code="authorization_interrupted", message="Authorization was interrupted; start again")
                raise
            except Exception:
                if self._live(flow):
                    self._finish(flow, "failed", code="authorization_failed", message="Authorization could not be completed; start again")
            return self._public(flow)

    async def cancel(self, owner, flow_id):
        flow = self._flow(owner, flow_id)
        if flow is None:
            return self._not_found()
        # Do not await an upstream HTTP call's lock. Every subsequent phase
        # checks this state, and cancelled flows never write a local profile.
        if self._live(flow):
            self._finish(flow, "cancelled")
        return self._public(flow)

    async def close(self):
        for flow in self._flows.values():
            if flow.status == "pending":
                self._finish(flow, "cancelled")

    def _check(self, flow):
        if not self._live(flow):
            raise AuthorizationError("authorization_cancelled", "Authorization is no longer active")
        if self.profiles.revision(flow.profile_id) != flow.profile_revision:
            raise AuthorizationError("profile_changed", "The connection changed during authorization; start again")

    def _key_name(self, flow):
        profile = hashlib.sha256(flow.profile_id.encode()).hexdigest()[:10]
        return f"ComfyUI-{flow.client_id[:10]}-{profile}"

    async def _tokens(self, flow, headers):
        self._check(flow)
        data = await self._call("GET", "/tokens", headers=headers,
                                query={"keyword": self._key_name(flow), "p": 1, "page_size": 100})
        self._check(flow)
        if not isinstance(data.get("items"), list):
            raise AuthorizationError("invalid_token_response", "The authorization service did not return the dedicated connection key")
        return [item for item in data["items"] if isinstance(item, dict) and item.get("name") == self._key_name(flow)
                and item.get("client_id") == flow.client_id
                and (item.get("user_id") is None or str(item["user_id"]) == str(flow.user_id))]

    async def _provision(self, flow):
        self._check(flow)
        headers = {"Authorization": "Bearer " + flow.access_token, "EasyAPI-User": str(flow.user_id)}
        rows = await self._tokens(flow, headers)
        now = int(self._wall_time())
        active = [row for row in rows if row.get("status") == 1
                  and isinstance(row.get("expired_time"), (int, float)) and now < row["expired_time"] <= now + KEY_LIFETIME + 300
                  and (row.get("unlimited_quota") is True or isinstance(row.get("remain_quota"), (int, float)) and row["remain_quota"] > 0)]
        if active:
            chosen = sorted(active, key=lambda row: int(row.get("id", 0)))[0]
        else:
            if any(row.get("status") == 2 for row in rows):
                raise AuthorizationError("dedicated_key_disabled", "This instance's dedicated key is disabled; review it in the EasyAPI console")
            if flow.create_attempted:
                raise AuthorizationError("key_creation_unconfirmed", "The dedicated key could not be confirmed; review the console before retrying")
            flow.create_attempted = True
            self._check(flow)
            try:
                await self._call("POST", "/token", headers=headers, body={"name": self._key_name(flow),
                    "unlimited_quota": True, "expired_time": now + KEY_LIFETIME, "model_limits_enabled": False,
                    "client_id": flow.client_id, "client_name": ("ComfyUI / " + flow.name).encode("utf-8")[:128].decode("utf-8", errors="ignore")})
            except AuthorizationError as error:
                if error.code == "rate_limited":
                    # A rate-limit response is a confirmed rejection. Ambiguous
                    # transport failures keep the creation-attempt marker.
                    flow.create_attempted = False
                raise
            rows = await self._tokens(flow, headers)
            active = [row for row in rows if row.get("status") == 1 and isinstance(row.get("expired_time"), (int, float))
                      and now < row["expired_time"] <= now + KEY_LIFETIME + 300]
            if not active:
                raise AuthorizationError("key_creation_unconfirmed", "The dedicated key could not be confirmed; review the console before retrying")
            chosen = sorted(active, key=lambda row: int(row.get("id", 0)), reverse=True)[0]
        token_id = chosen.get("id")
        if isinstance(token_id, bool) or not str(token_id).isdigit() or int(token_id) <= 0:
            raise AuthorizationError("invalid_token_response", "The dedicated key could not be identified")
        self._check(flow)
        data = await self._call("POST", f"/token/{int(token_id)}/key", headers=headers)
        self._check(flow)
        key = data.get("key")
        if not isinstance(key, str) or not key or any(char in key for char in "\r\n"):
            raise AuthorizationError("invalid_token_response", "The dedicated key could not be read")
        if not key.startswith("sk-"):
            key = "sk-" + key
        await self._call("GET", "/usage/token", headers={"Authorization": "Bearer " + key, CLIENT_ID_HEADER: flow.client_id})
        self._check(flow)
        # The account token never reaches ProfileStore or the browser. Test
        # gateway URLs also never become an official profile's saved origin.
        try:
            return self.profiles.save(flow.profile_id, OFFICIAL_GATEWAY, key, flow.name,
                auth_method="official", client_id=flow.client_id, credential_expires_at=chosen.get("expired_time"),
                token_id=int(token_id), make_default=flow.make_default, expected_revision=flow.profile_revision)
        except ValueError:
            if self.profiles.revision(flow.profile_id) != flow.profile_revision:
                raise AuthorizationError("profile_changed", "The connection changed during authorization; start again") from None
            raise AuthorizationError("profile_save_failed", "The authorized connection could not be saved; check server storage") from None

    async def _call(self, method, path, *, body=None, query=None, headers=None):
        allowed = path in {"/cli/session", "/cli/ticket", "/cli/exchange", "/tokens", "/token", "/usage/token"} or re.fullmatch(r"/token/[0-9]+/key", path)
        if not allowed:
            raise AuthorizationError("invalid_authorization_route", "Unsupported authorization operation")
        try:
            if self._transport is not None:
                payload = await self._transport(method, self.gateway_base + path, body=body, query=query, headers=dict(headers or {}))
            else:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20, connect=10),
                        cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as session:
                    async with session.request(method, self.gateway_base + path, json=body, params=query,
                            headers={"Accept": "application/json", **(headers or {})}, allow_redirects=False) as response:
                        if response.status == 429:
                            raise AuthorizationError("rate_limited", "Authorization is temporarily rate limited", status=429, retry_after=8)
                        if response.status >= 300:
                            raise AuthorizationError("authorization_service_unavailable", "The official authorization service is unavailable", status=503)
                        chunks, total = [], 0
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            total += len(chunk)
                            if total > 1024 * 1024:
                                raise AuthorizationError("invalid_authorization_response", "The authorization response exceeded the size limit")
                            chunks.append(chunk)
                        payload = json.loads(b"".join(chunks))
        except AuthorizationError:
            raise
        except Exception:
            raise AuthorizationError("authorization_service_unavailable", "The official authorization service could not be reached", status=503) from None
        succeeded = isinstance(payload, dict) and (payload.get("success") is True or path == "/usage/token" and payload.get("code") is True)
        if not succeeded:
            raise AuthorizationError("authorization_rejected", "The authorization request was rejected; start again")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise AuthorizationError("invalid_authorization_response", "The authorization service returned an invalid response")
        return data
