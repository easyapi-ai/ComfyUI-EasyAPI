"""Exercise cached node creation and background changes through the real UI."""
import ast
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class NodeCatalogFrontendTests(unittest.TestCase):
    def test_new_nodes_reuse_snapshots_and_removed_models_keep_their_values(self):
        root = Path(__file__).resolve().parents[1]
        fixture = ast.parse((root / "tests/test_form_frontend.py").read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(fixture) if isinstance(node, ast.FunctionDef)
                      and node.name == "test_selection_drafts_forms_language_and_video_ports")
        script = next(node.value.value for node in ast.walk(method) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "script" for target in node.targets))
        setup = script.split("(async () => {", 1)[0]
        setup = setup.replace("const context = { document", "let catalogNow = 1000;\n"
                              "class CatalogDate extends Date { static now() { return catalogNow; } }\n"
                              "const context = { Date: CatalogDate, document")
        setup = setup.replace("holdNodeModels && url.searchParams.get('profile_id') === 'p1'", "holdNodeModels")
        checks = r'''
(async () => {
  defaultProfile = 'p2';
  const firstNode = makeNode(), first = new h.NodePanel(firstNode); body.append(first.root); await tick();
  const count = () => requests.filter(item => item.address.startsWith('/easyapi/models?')).length;
  const firstCount = count();
  const secondNode = makeNode(), second = new h.NodePanel(secondNode); body.append(second.root); await tick();
  assert.equal(count(), firstCount, 'Adding a node must reuse the same fresh catalog');
  assert.equal(second.picker.input.disabled, false);
  const originalParams = get(firstNode, 'params_json');
  catalogNow += 121000; holdNodeModels = true;
  const updating = first.refreshModels();
  assert.equal(first.picker.input.disabled, false, 'A stale snapshot renders synchronously');
  assert.equal(first.picker.items[0].id, 'saved-model');
  await tick();
  const otherUpdate = second.refreshModels(); await tick();
  assert.equal(count(), firstCount + 1, 'All stale viewers share one background refresh');
  assert.equal(second.picker.input.disabled, false);
  assert.match(first.catalogStatus.textContent, /Updating models/);
  const schemasBeforeRemoval = requests.filter(item => item.address.startsWith('/easyapi/schema?')).length;
  releaseNodeModels({ models: [{ id: 'other-model' }], cache_ttl: 90 }); await tick();
  await Promise.all([updating, otherUpdate]);
  assert.deepEqual(Array.from(first.models, item => item.id), ['other-model']);
  assert.deepEqual(Array.from(second.models, item => item.id), ['other-model']);
  assert.equal(get(firstNode, 'model'), 'saved-model', 'Do not replace the workflow model');
  assert.equal(get(firstNode, 'params_json'), originalParams, 'Do not rewrite saved parameters');
  assert.equal(first.formSchema, null, 'Unavailable models lose their active schema');
  assert.equal(requests.filter(item => item.address.startsWith('/easyapi/schema?')).length,
    schemasBeforeRemoval, 'Do not reload a removed model schema after clearing it');
  assert.match(first.status.textContent, /not in this permission list/);
  const emptyRefresh = first.refreshModels(true); await tick();
  releaseNodeModels({ models: [], cache_ttl: 90 }); await emptyRefresh; await tick();
  assert.equal(first.picker.items.length, 0); assert.equal(first.picker.input.disabled, true);
  assert.equal(second.picker.items.length, 0); assert.equal(second.picker.input.disabled, true);
  first.dispose(); second.dispose();
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-", str(root / "web")], input=setup + checks,
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
