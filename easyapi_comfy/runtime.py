"""Lazy state ownership below ComfyUI's persistent user directory."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .client import EasyAPIClient
from .profiles import ProfileStore
from .tasks import TaskStore


class Runtime:
    def __init__(self, state_dir=None):
        if state_dir is None:
            state_dir = os.environ.get("EASYAPI_STATE_DIR")
        if state_dir is None:
            try:
                import folder_paths
                state_dir = Path(folder_paths.get_user_directory()) / "easyapi"
            except ImportError:
                state_dir = Path.home() / ".easyapi-comfy"
        self.state_dir = Path(state_dir)
        self._clients = {}
        self._retired_clients = []
        self.profiles = ProfileStore(self.state_dir / "profiles.json", on_change=self._invalidate_profile)
        self.tasks = TaskStore(self.state_dir / "tasks.json")
        self.output_dir = self.state_dir / "results"
        try:
            import folder_paths
            self.output_dir = Path(folder_paths.get_output_directory()) / "EasyAPI"
        except ImportError:
            pass
        self.input_dir = self.state_dir / "inputs"

    def client(self, profile_id=None):
        profile_id = self.profiles.resolve_id(profile_id)
        profile = self.profiles.get(profile_id)
        key = (profile_id, profile["base_url"], profile["api_key"], profile.get("client_id"))
        if key not in self._clients:
            self._clients[key] = EasyAPIClient(profile)
        return self._clients[key]

    def _invalidate_profile(self, profile_id):
        for key in [key for key in self._clients if key[0] == profile_id]:
            client = self._clients.pop(key)
            client.invalidate()
            self._retired_clients.append(client)

    async def close(self):
        jobs = getattr(self, "_video_tuning_jobs", None)
        if jobs is not None:
            await jobs.close()
        tuning = getattr(self, "_video_tuning_service", None)
        if tuning is not None:
            await tuning.close()
        for client in [*self._clients.values(), *self._retired_clients]:
            await client.close()
        self._clients.clear()
        self._retired_clients.clear()


_runtime = None
_lock = threading.Lock()


def get_runtime():
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = Runtime()
        return _runtime
