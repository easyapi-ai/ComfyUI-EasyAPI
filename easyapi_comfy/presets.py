"""Named, credential-free creative presets with scoped defaults.

Preset IDs are opaque. Profile, operation and model values are record data,
never filesystem paths. Applying a preset to a node is a frontend decision.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .profiles import atomic_json


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_RECORD_KEYS = {"id", "profile_id", "operation", "model", "name", "params"}
_UNSAFE_OBJECT_KEYS = {"__proto__", "constructor", "prototype"}
_PRIVATE_KEYS = {
    "authorization", "proxyauthorization", "cookie", "setcookie", "sessiontoken",
    "idtoken", "credentials", "credential", "privatekey", "signingkey",
    "awsaccesskeyid", "awssecretaccesskey", "clientsecret",
}
_PRIVATE_SUFFIXES = ("apikey", "accesstoken", "refreshtoken", "authtoken", "password",
                     "passwd", "secret", "privatekey", "authorization", "credential", "credentials")


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string of at most {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} contains a control character")
    return value.strip()


def _profile_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) or value in _UNSAFE_OBJECT_KEYS:
        raise ValueError("Invalid preset connection ID")
    return value


def _operation(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) or value in _UNSAFE_OBJECT_KEYS:
        raise ValueError("Invalid preset operation")
    return value


def _preset_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid preset ID")
    return value


def _safe_json(value, active=None):
    """Validate without printing user values or allowing credential-shaped keys."""
    active = active if active is not None else set()
    if value is None or type(value) in {str, int, float, bool}:
        return
    if type(value) not in {dict, list}:
        raise ValueError("Preset parameters must contain only JSON values")
    identity = id(value)
    if identity in active:
        raise ValueError("Preset parameters cannot contain circular references")
    active.add(identity)
    try:
        values = value.values() if isinstance(value, dict) else value
        if isinstance(value, dict):
            for key in value:
                if not isinstance(key, str):
                    raise ValueError("Preset parameter keys must be strings")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if key in _UNSAFE_OBJECT_KEYS or normalized in _PRIVATE_KEYS or normalized.endswith(_PRIVATE_SUFFIXES):
                    raise ValueError("Preset parameters cannot contain credentials or unsafe object keys")
        for item in values:
            _safe_json(item, active)
    finally:
        active.remove(identity)


def _record(value, identifier):
    if not isinstance(value, dict):
        raise ValueError("Preset must be a JSON object")
    unknown = set(value) - _RECORD_KEYS - {"make_default", "clear_default"}
    if unknown:
        raise ValueError("Preset contains unsupported fields")
    profile_id = _profile_id(value.get("profile_id"))
    operation = _operation(value.get("operation"))
    model = _text(value.get("model"), "Preset model", 512)
    if "\\" in model or any(segment in {".", ".."} for segment in model.split("/")):
        raise ValueError("Invalid preset model")
    name = _text(value.get("name"), "Preset name", 120)
    params = value.get("params")
    if not isinstance(params, dict):
        raise ValueError("Preset params must be a JSON object")
    _safe_json(params)
    try:
        json.dumps(params, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Preset parameters must be valid finite JSON values") from exc
    for flag in ("make_default", "clear_default"):
        if flag in value and not isinstance(value[flag], bool):
            raise ValueError(f"{flag} must be a boolean")
    if value.get("make_default") and value.get("clear_default"):
        raise ValueError("Cannot set and clear the default in the same operation")
    return {"id": _preset_id(identifier), "profile_id": profile_id, "operation": operation,
            "model": model, "name": name, "params": copy.deepcopy(params)}


class PresetStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.suffix != ".json":
            self.path = self.path / "presets.json"
        self.path = self.path.resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(os.path.normcase(str(self.path)), threading.RLock())

    @contextmanager
    def _locked(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.lock_path.open("a+b") as lock_file:
                if os.name != "nt":
                    os.chmod(self.lock_path, 0o600)
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                else:
                    import msvcrt
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if os.name != "nt":
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    else:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    def _read_state(self):
        if not self.path.exists():
            return {"version": 1, "presets": {}, "default_preset_ids": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid preset store") from exc
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("presets"), dict) or not isinstance(state.get("default_preset_ids"), dict):
            raise ValueError("Invalid preset store")
        for identifier, value in state["presets"].items():
            if not isinstance(value, dict) or set(value) != _RECORD_KEYS or value.get("id") != identifier:
                raise ValueError("Invalid preset record")
            _record(value, identifier)
        for profile_id, operations in state["default_preset_ids"].items():
            _profile_id(profile_id)
            if not isinstance(operations, dict):
                raise ValueError("Invalid preset defaults")
            for operation, identifier in operations.items():
                _operation(operation)
                record = state["presets"].get(identifier)
                if record is None or (record["profile_id"], record["operation"]) != (profile_id, operation):
                    raise ValueError("Invalid preset default scope")
        return state

    def list(self, profile_id, operation=None):
        profile_id = _profile_id(profile_id)
        if operation is not None:
            operation = _operation(operation)
        with self._locked():
            state = self._read_state()
            records = [copy.deepcopy(value) for value in state["presets"].values()
                       if value["profile_id"] == profile_id and (operation is None or value["operation"] == operation)]
            records.sort(key=lambda value: (value["operation"], value["model"], value["name"].casefold(), value["id"]))
            defaults = state["default_preset_ids"].get(profile_id, {})
            return {"presets": records, "default_preset_ids": {key: identifier for key, identifier in defaults.items()
                                                               if operation is None or operation == key}}

    def save(self, value, preset_id=None):
        if not isinstance(value, dict):
            raise ValueError("Preset must be a JSON object")
        supplied = value.get("id")
        if preset_id is not None and supplied is not None and preset_id != supplied:
            raise ValueError("Preset ID does not match the record")
        requested_id = preset_id if preset_id is not None else supplied
        identifier = _preset_id(requested_id) if requested_id is not None else uuid.uuid4().hex
        record = _record(value, identifier)
        with self._locked():
            state = self._read_state()
            previous = state["presets"].get(identifier)
            if requested_id is not None and previous is None:
                raise ValueError("Preset was not found")
            if previous and any(previous[key] != record[key] for key in ("profile_id", "operation", "model")):
                raise ValueError("Preset scope cannot change; save a new preset instead")
            state["presets"][identifier] = record
            defaults = state["default_preset_ids"].setdefault(record["profile_id"], {})
            if value.get("make_default"):
                defaults[record["operation"]] = identifier
            elif value.get("clear_default") or (value.get("make_default") is False and defaults.get(record["operation"]) == identifier):
                defaults.pop(record["operation"], None)
            if not defaults:
                state["default_preset_ids"].pop(record["profile_id"], None)
            atomic_json(self.path, state)
        return copy.deepcopy(record)

    def delete(self, preset_id, profile_id):
        identifier, profile_id = _preset_id(preset_id), _profile_id(profile_id)
        with self._locked():
            state = self._read_state()
            previous = state["presets"].get(identifier)
            if previous is None or previous["profile_id"] != profile_id:
                return False
            del state["presets"][identifier]
            defaults = state["default_preset_ids"].get(profile_id, {})
            if defaults.get(previous["operation"]) == identifier:
                defaults.pop(previous["operation"], None)
            if not defaults:
                state["default_preset_ids"].pop(profile_id, None)
            atomic_json(self.path, state)
            return True

    def clear_default(self, profile_id, operation):
        profile_id, operation = _profile_id(profile_id), _operation(operation)
        with self._locked():
            state = self._read_state()
            defaults = state["default_preset_ids"].get(profile_id, {})
            defaults.pop(operation, None)
            if not defaults:
                state["default_preset_ids"].pop(profile_id, None)
            atomic_json(self.path, state)

    def clear_profile(self, profile_id):
        profile_id = _profile_id(profile_id)
        with self._locked():
            state = self._read_state()
            identifiers = [identifier for identifier, value in state["presets"].items() if value["profile_id"] == profile_id]
            for identifier in identifiers:
                del state["presets"][identifier]
            state["default_preset_ids"].pop(profile_id, None)
            atomic_json(self.path, state)
            return len(identifiers)
