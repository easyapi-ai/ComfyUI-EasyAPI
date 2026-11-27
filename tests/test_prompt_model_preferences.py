import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class PromptModelPreferenceTests(unittest.TestCase):
    def test_defaults_memory_and_catalog_validation(self):
        script = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(process.argv[2], "utf8");
const { createPromptModelPreferences } = await import("data:text/javascript," + encodeURIComponent(source));
const values = new Map();
const storage = () => ({
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: key => values.delete(key),
});
const models = [{ id: "first" }, { id: "second" }];
const preferences = createPromptModelPreferences(storage);

assert.equal(preferences.remember("main", "optimize", "first"), true);
assert.equal(preferences.setFixed("main", "optimize", "second"), true);
assert.equal(preferences.remember("main", "tune", "second"), true);
assert.equal(preferences.remember("main", "analysis", ""), true);
assert.equal(preferences.remember("main", "optimize", ""), false);

const reopened = createPromptModelPreferences(storage);
assert.equal(reopened.resolve("main", "optimize", models).value, "second");
assert.equal(reopened.resolve("main", "tune", models).value, "second");
assert.equal(reopened.resolve("main", "analysis", models, ["first"]).value, "");
assert.equal(reopened.resolve("other", "tune", models, ["first"]).value, "first");
assert.deepEqual(reopened.resolve("main", "optimize", [models[0]]), { value: "", unavailable: "second" });
assert.equal(reopened.resolve("main", "tune", [models[0]], ["first"]).value, "first");

assert.equal(reopened.setFixed("main", "optimize", undefined), true);
assert.equal(preferences.resolve("main", "optimize", models).value, "first");
assert.equal(reopened.setFixed("main", "analysis", "second"), true);
assert.equal(reopened.resolve("main", "analysis", models).value, "second");
assert.equal(reopened.setFixed("main", "analysis", ""), true);
assert.equal(reopened.get("main", "analysis").fixed, "");

const blocked = createPromptModelPreferences(() => { throw new Error("Storage blocked"); });
assert.equal(blocked.resolve("main", "tune", models, ["first"]).value, "first");
assert.equal(blocked.remember("main", "tune", "first"), false);
assert.equal(blocked.setFixed("main", "tune", "first"), false);
"""
        source = Path(__file__).parents[1] / "web/prompt-model-preferences.js"
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-", str(source)],
            input=script, encoding="utf-8", capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
