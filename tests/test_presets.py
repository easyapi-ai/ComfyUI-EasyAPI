"""Scoped creative defaults, persistence, concurrency and credential isolation."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from easyapi_comfy.presets import PresetStore


def value(name="Draft", *, profile_id="first", operation="image-generation", model="vendor/model", params=None, **extra):
    return {"profile_id": profile_id, "operation": operation, "model": model, "name": name,
            "params": params if params is not None else {"seed": 0, "watermark": False, "optional": None}, **extra}


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "presets.json"
        self.store = PresetStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_multiple_named_presets_and_one_default_per_connection_operation(self):
        first = self.store.save(value("Fast", make_default=True))
        second = self.store.save(value("Quality", params={"quality": "high"}))
        alternate_model = self.store.save(value("Other model", model="another-model", make_default=True))
        chat = self.store.save(value("Chat", operation="chat", model="text-model", make_default=True))
        other_profile = self.store.save(value("Other connection", profile_id="second", make_default=True))
        listed = self.store.list("first")
        self.assertCountEqual([p["id"] for p in listed["presets"]], [first["id"], second["id"], alternate_model["id"], chat["id"]])
        self.assertEqual(listed["default_preset_ids"], {"image-generation": alternate_model["id"], "chat": chat["id"]})
        self.assertEqual(self.store.list("first", "chat")["default_preset_ids"], {"chat": chat["id"]})
        self.assertEqual(self.store.list("second")["default_preset_ids"], {"image-generation": other_profile["id"]})

    def test_reopened_store_preserves_zero_false_null_nested_values_and_is_detached(self):
        params = {"seed": 0, "enabled": False, "empty": None, "nested": {"items": [0, False, None, "中文"]}}
        saved = self.store.save(value(params=params, make_default=True))
        params["nested"]["items"].append("changed")
        saved["params"]["seed"] = 99
        reopened = PresetStore(self.path)
        actual = reopened.list("first")["presets"][0]
        self.assertEqual(actual["params"], {"seed": 0, "enabled": False, "empty": None, "nested": {"items": [0, False, None, "中文"]}})
        actual["params"]["nested"]["items"].clear()
        self.assertEqual(len(reopened.list("first")["presets"][0]["params"]["nested"]["items"]), 4)
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_update_preserves_default_unless_explicitly_cleared_and_scope_is_immutable(self):
        saved = self.store.save(value(make_default=True))
        updated = self.store.save({**saved, "name": "Renamed", "params": {"n": 2}}, saved["id"])
        self.assertEqual(updated["id"], saved["id"])
        self.assertEqual(self.store.list("first")["default_preset_ids"]["image-generation"], saved["id"])
        for field, replacement in (("profile_id", "second"), ("operation", "chat"), ("model", "different")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "scope"):
                self.store.save({**updated, field: replacement}, saved["id"])
        self.store.save({**updated, "make_default": False}, saved["id"])
        self.assertEqual(self.store.list("first")["default_preset_ids"], {})
        self.store.save({**updated, "make_default": True}, saved["id"])
        self.store.clear_default("first", "image-generation")
        self.assertEqual(self.store.list("first")["default_preset_ids"], {})

    def test_delete_and_clear_profile_do_not_select_a_replacement_or_cross_scopes(self):
        first = self.store.save(value("Default", make_default=True))
        remaining = self.store.save(value("Remaining"))
        other = self.store.save(value("Other", profile_id="second", make_default=True))
        self.assertFalse(self.store.delete(first["id"], "second"))
        self.assertEqual(len(self.store.list("first")["presets"]), 2)
        self.assertTrue(self.store.delete(first["id"], "first"))
        self.assertEqual(self.store.list("first")["default_preset_ids"], {})
        self.assertEqual(self.store.clear_profile("first"), 1)
        self.assertEqual(self.store.list("first"), {"presets": [], "default_preset_ids": {}})
        self.assertEqual(self.store.list("second")["presets"][0]["id"], other["id"])
        self.assertFalse(self.store.delete(remaining["id"], "first"))

    def test_recursive_sensitive_and_prototype_fields_are_rejected_without_echoing_values(self):
        sensitive = ["api_key", "apiKey", "access_token", "refresh-token", "Authorization", "Proxy-Authorization",
                     "X-Api-Key", "x-goog-api-key", "password", "client_secret", "privateKey", "cookie", "__proto__", "constructor"]
        for key in sensitive:
            with self.subTest(key=key), self.assertRaises(ValueError) as caught:
                self.store.save(value(params={"outer": [{"inner": {key: "never-print-this-value"}}]}))
            self.assertNotIn("never-print-this-value", str(caught.exception))
        self.assertEqual(self.store.list("first")["presets"], [])

    def test_listing_and_defaults_cannot_export_connection_credentials(self):
        private = "private-test-only-key"
        (self.root / "profiles.json").write_text(json.dumps({"first": {"api_key": private}}), encoding="utf-8")
        saved = self.store.save(value(make_default=True))
        exported = self.store.list("first")
        self.assertEqual(set(exported["presets"][0]), {"id", "profile_id", "operation", "model", "name", "params"})
        self.assertNotIn(private, json.dumps(exported))
        self.assertNotIn("api_key", json.dumps(exported))
        self.assertEqual(exported["default_preset_ids"], {"image-generation": saved["id"]})
        with self.assertRaises(ValueError):
            self.store.save({**value(), "api_key": private})
        # Even a hand-edited state file must not turn list/export into a credential leak.
        state = json.loads(self.path.read_text(encoding="utf-8"))
        state["presets"][saved["id"]]["params"] = {"api_key": private}
        self.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.list("first")

    def test_ids_and_scopes_cannot_create_paths_and_model_slashes_remain_data(self):
        saved = self.store.save(value("../display name", model="vendor/model:version"))
        self.assertRegex(saved["id"], r"^[a-f0-9]{32}$")
        self.assertEqual({path.name for path in self.root.iterdir()}, {"presets.json", "presets.json.lock"})
        for invalid in ("../escape", "nested/name", "__proto__"):
            with self.assertRaises(ValueError):
                self.store.save(value(profile_id=invalid))
        with self.assertRaises(ValueError):
            self.store.save(value(operation="../../submit"))
        with self.assertRaises(ValueError):
            self.store.delete("../presets.json", "first")
        with self.assertRaises(ValueError):
            self.store.save(value(model="../outside"))
        self.assertEqual(len(self.store.list("first")["presets"]), 1)

    def test_invalid_json_and_id_updates_leave_existing_state_unchanged(self):
        saved = self.store.save(value(make_default=True))
        before = self.path.read_bytes()
        cycle = {}; cycle["self"] = cycle
        for params in ([1, 2], {"value": float("nan")}, {"value": float("inf")}, {"value": object()}, cycle):
            with self.subTest(kind=type(params).__name__), self.assertRaises(ValueError):
                self.store.save(value(params=params))
        with self.assertRaises(ValueError):
            self.store.save(value(), "0" * 32)
        with self.assertRaises(ValueError):
            self.store.save({**saved, "id": "1" * 32}, saved["id"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_store_instances_do_not_lose_records(self):
        def write(index):
            return PresetStore(self.path).save(value(f"Preset {index}", params={"index": index}, make_default=True))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            created = list(pool.map(write, range(32)))
        listed = self.store.list("first")
        self.assertEqual(len(listed["presets"]), 32)
        self.assertEqual(len({p["id"] for p in listed["presets"]}), 32)
        self.assertIn(listed["default_preset_ids"]["image-generation"], {p["id"] for p in created})

    def test_separate_process_writers_share_the_atomic_lock(self):
        snippet = """from easyapi_comfy.presets import PresetStore
import sys
store=PresetStore(sys.argv[1])
for index in range(8):
    store.save({'profile_id':'first','operation':'image-generation','model':'vendor/model','name':sys.argv[2]+'-'+str(index),'params':{'index':index}})
"""
        cwd = str(Path(__file__).resolve().parents[1])
        processes = [subprocess.Popen([sys.executable, "-c", snippet, str(self.path), str(worker)], cwd=cwd,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for worker in range(3)]
        try:
            for process in processes:
                output, error = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, error.decode(errors="replace"))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait()
        self.assertEqual(len(self.store.list("first")["presets"]), 24)


if __name__ == "__main__":
    unittest.main()
