"""Private, atomic server-side connection profiles."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

OFFICIAL_GATEWAY = "https://token.easyapi.com"
ENV_PROFILE_ID = "easyapi-env"
_ANY_REVISION = object()
_LEGACY_OFFLINE_PROFILE = {
    "id": "mock",
    "name": "离线测试（不计费）",
    "base_url": "http://comfytv-easyapi-mock:3199",
    "api_key": "mock-only-key",
}


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".easyapi-", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Gateway URL must start with http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Gateway URL cannot contain credentials, query or fragment")
    path = parsed.path.rstrip("/")
    if path == "/v1":
        path = ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ProfileStore:
    def __init__(self, path: str | Path, on_change=None):
        self.path = Path(path)
        if self.path.suffix != ".json":
            self.path = self.path / "profiles.json"
        self._lock = threading.RLock()
        self._on_change = on_change
        self._checks = {}
        self._catalog_secret = secrets.token_bytes(32)
        self._catalog_generations = {}

    def _read(self) -> dict:
        profiles = self._read_state()["profiles"]
        if ENV_PROFILE_ID not in profiles and self._environment_configured():
            profiles[ENV_PROFILE_ID] = self._environment_profile()
        return profiles

    @staticmethod
    def _environment_configured() -> bool:
        return bool(os.environ.get("EASYAPI_API_KEY") or os.environ.get("EASYAPI_API_KEY_FILE"))

    def _environment_profile(self) -> dict:
        key = os.environ.get("EASYAPI_API_KEY", "").strip()
        secret_path = os.environ.get("EASYAPI_API_KEY_FILE")
        if not key and secret_path:
            try:
                with Path(secret_path).open(encoding="utf-8") as secret:
                    key = secret.read(4097).strip()
            except (OSError, UnicodeError):
                key = ""
        if len(key) > 4096 or any(char in key for char in "\r\n"):
            key = ""
        try:
            gateway = normalize_base_url(os.environ.get("EASYAPI_BASE_URL") or OFFICIAL_GATEWAY)
        except ValueError:
            gateway, key = OFFICIAL_GATEWAY, ""
        return {
            "id": ENV_PROFILE_ID, "name": "EasyAPI (Environment)",
            "base_url": gateway, "api_key": key, "auth_method": "environment", "read_only": True,
        }

    def _read_state(self) -> dict:
        if not self.path.exists():
            return {"version": 2, "profiles": {}, "default_profile_id": None}
        with self.path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("Invalid EasyAPI profile store")
        versioned = payload.get("version") == 2 and isinstance(payload.get("profiles"), dict)
        profiles = payload["profiles"] if versioned else payload
        # Match the old installer record exactly so a user-created mock connection survives.
        if profiles.get("mock") == _LEGACY_OFFLINE_PROFILE:
            del profiles["mock"]
            if versioned and payload.get("default_profile_id") == "mock":
                payload["default_profile_id"] = None
            atomic_json(self.path, payload)
        if versioned:
            selected = payload.get("default_profile_id")
            available = selected in profiles if isinstance(selected, str) else False
            return {"version": 2, "profiles": profiles,
                    "default_profile_id": selected if available or (
                        selected == ENV_PROFILE_ID and self._environment_configured()) else None}
        # Existing stores are id -> profile maps. Do not silently choose the
        # first connection when migrating a store containing several profiles.
        return {"version": 2, "profiles": profiles, "default_profile_id": None}

    def default_profile_id(self) -> str | None:
        with self._lock:
            return self._read_state()["default_profile_id"]

    def resolve_id(self, profile_id: str | None = None) -> str:
        with self._lock:
            if profile_id:
                return str(profile_id)
            state = self._read_state()
            selected = state["default_profile_id"]
            if selected:
                return selected
            profiles = self._read()
            if len(profiles) == 1:
                return next(iter(profiles))
            raise ValueError("Select an EasyAPI connection or set an explicit default connection")

    def set_default(self, profile_id: str | None) -> str | None:
        with self._lock:
            state = self._read_state()
            selected = str(profile_id) if profile_id else None
            if selected is not None and selected not in self._read():
                raise ValueError("Default EasyAPI connection has not been configured")
            state["default_profile_id"] = selected
            atomic_json(self.path, state)
            return selected

    def get(self, profile_id: str | None = None) -> dict:
        with self._lock:
            profile_id = self.resolve_id(profile_id)
            profile = self._read().get(profile_id)
            if not profile:
                raise ValueError(f"EasyAPI profile {profile_id!r} has not been configured")
            return copy.deepcopy(profile)

    @staticmethod
    def _revision(profile: dict | None) -> str | None:
        if profile is None:
            return None
        serialized = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def revision(self, profile_id: str) -> str | None:
        with self._lock:
            return self._revision(self._read().get(profile_id))

    @staticmethod
    def public(profile: dict) -> dict:
        key = profile.get("api_key") or ""
        return {key: profile[key] for key in ("id", "name", "base_url") if key in profile} | {
            "has_key": bool(key),
            "key_hint": "••••" + key[-4:] if len(key) >= 10 else None,
            "auth_method": profile.get("auth_method", "manual"),
            "credential_expires_at": profile.get("credential_expires_at"),
            "read_only": bool(profile.get("read_only")),
        }

    def public_status(self, profile: dict) -> dict:
        with self._lock:
            revision, result = self._checks.get(profile["id"], (None, {}))
            profile_revision = self._revision(profile)
            status = result if revision == profile_revision else {"connection_status": "untested"}
            generation = self._catalog_generations.get(profile["id"], "")
            # The browser gets an opaque per-process identity, never a credential or its unkeyed hash.
            catalog_revision = hmac.digest(self._catalog_secret, f"{generation}:{profile_revision}".encode(), "sha256").hex()
            return self.public(profile) | status | {"model_catalog_revision": catalog_revision}

    def list_public(self) -> list[dict]:
        with self._lock:
            return [self.public_status(p) for _, p in sorted(self._read().items())]

    def record_check(self, profile: dict, ok: bool, checked_at: int) -> None:
        with self._lock:
            current = self._read().get(profile["id"])
            if current == profile:
                self._checks[profile["id"]] = (self._revision(profile), {
                    "connection_status": "ok" if ok else "error", "last_checked_at": checked_at,
                })

    def save(self, profile_id: str, base_url: str, api_key: str, name: str | None = None, *, make_default=False,
             auth_method=None, client_id=None, credential_expires_at=None, token_id=None,
             expected_revision=_ANY_REVISION) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(profile_id)):
            raise ValueError("Profile ID must be 1-64 letters, numbers, underscores or dashes")
        with self._lock:
            state = self._read_state()
            profiles = state["profiles"]
            previous = profiles.get(profile_id, {})
            if profile_id == ENV_PROFILE_ID and not previous and self._environment_configured():
                raise ValueError("This connection is managed by the ComfyUI server environment.")
            if expected_revision is not _ANY_REVISION and self._revision(profiles.get(profile_id)) != expected_revision:
                raise ValueError("The connection was changed while authorization was pending.")
            candidate = str(api_key or "").strip()
            key = candidate or previous.get("api_key", "")
            if len(key) > 4096 or any(c in key for c in "\r\n"):
                raise ValueError("API key contains invalid characters")
            gateway = normalize_base_url(base_url)
            method = auth_method or ("manual" if candidate else previous.get("auth_method", "manual"))
            if method == "official" and gateway != OFFICIAL_GATEWAY:
                raise ValueError("An official EasyAPI authorization is only valid for the official gateway.")
            profile = {
                "id": profile_id,
                "name": str(name or previous.get("name") or profile_id)[:120],
                "base_url": gateway,
                "api_key": key,
                "auth_method": method,
            }
            if method == "official":
                profile.update(client_id=client_id or previous.get("client_id"),
                               credential_expires_at=credential_expires_at or previous.get("credential_expires_at"),
                               token_id=token_id or previous.get("token_id"))
            profiles[profile_id] = profile
            if make_default:
                state["default_profile_id"] = profile_id
            atomic_json(self.path, state)
            if any(previous.get(k) != profile.get(k) for k in ("base_url", "api_key", "client_id")):
                self._checks.pop(profile_id, None)
                self._catalog_generations[profile_id] = secrets.token_hex(16)
                if self._on_change:
                    self._on_change(profile_id)
            return self.public_status(profile)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            state = self._read_state()
            profiles = state["profiles"]
            if profile_id == ENV_PROFILE_ID and profile_id not in profiles and self._environment_configured():
                raise ValueError("This connection is managed by the ComfyUI server environment.")
            profiles.pop(profile_id, None)
            self._checks.pop(profile_id, None)
            self._catalog_generations.pop(profile_id, None)
            if state["default_profile_id"] == profile_id:
                state["default_profile_id"] = None
            atomic_json(self.path, state)
            if self._on_change:
                self._on_change(profile_id)
