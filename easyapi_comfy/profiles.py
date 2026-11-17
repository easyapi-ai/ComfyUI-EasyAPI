"""Private, atomic server-side connection profiles."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("Invalid EasyAPI profile store")
        return payload

    def get(self, profile_id: str = "default") -> dict:
        with self._lock:
            profile = self._read().get(profile_id)
            if not profile:
                raise ValueError(f"EasyAPI profile {profile_id!r} has not been configured")
            return copy.deepcopy(profile)

    @staticmethod
    def public(profile: dict) -> dict:
        return {key: profile[key] for key in ("id", "name", "base_url") if key in profile} | {
            "has_key": bool(profile.get("api_key")),
        }

    def list_public(self) -> list[dict]:
        with self._lock:
            return [self.public(p) for _, p in sorted(self._read().items())]

    def save(self, profile_id: str, base_url: str, api_key: str, name: str | None = None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(profile_id)):
            raise ValueError("Profile ID must be 1-64 letters, numbers, underscores or dashes")
        with self._lock:
            profiles = self._read()
            previous = profiles.get(profile_id, {})
            key = str(api_key or "").strip() or previous.get("api_key", "")
            if any(c in key for c in "\r\n"):
                raise ValueError("API key contains invalid characters")
            profile = {
                "id": profile_id,
                "name": str(name or previous.get("name") or profile_id)[:120],
                "base_url": normalize_base_url(base_url),
                "api_key": key,
            }
            profiles[profile_id] = profile
            atomic_json(self.path, profiles)
            if self._on_change and any(previous.get(k) != profile[k] for k in ("base_url", "api_key")):
                self._on_change(profile_id)
            return self.public(profile)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            profiles = self._read()
            profiles.pop(profile_id, None)
            atomic_json(self.path, profiles)
            if self._on_change:
                self._on_change(profile_id)
