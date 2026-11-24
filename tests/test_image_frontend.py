"""Image operation inference and serialized workflow compatibility."""
import shutil
import json
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class ImageFrontendTests(unittest.TestCase):
    def test_image_routes_and_legacy_link_migration(self):
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let source = fs.readFileSync(process.argv[2], 'utf8').replace(/^export /gm, '');
source += '\nglobalThis.hooks = { inferImageOperation, legacyImageLayout, migrateImageWorkflow };';
const context = { console }; vm.createContext(context); vm.runInContext(source, context);
const { inferImageOperation, legacyImageLayout, migrateImageWorkflow } = context.hooks;
assert.equal(inferImageOperation(), 'image-generation');
assert.equal(inferImageOperation([{ name: 'mask', link: 1 }]), 'image-generation');
assert.equal(inferImageOperation([{ name: 'image', link: 0 }]), 'image-edit');
assert.equal(inferImageOperation([{ name: 'references', link: 2 }]), 'image-edit');
for (const params of [{ image: 'https://media.test/ref.png' }, { images: ['data:image/png;base64,abc'] },
  { image: { image_url: { url: 'https://media.test/ref.png' } } }, { images: [{ path: '/tmp/ref.png' }] },
  { input_references: [{ type: 'image', url: 'https://media.test/ref.png' }] },
  { input_references: 'https://media.test/ref.png' }, { input_references: ['https://media.test/ref.png'] }]) {
  assert.equal(inferImageOperation([], params), 'image-edit');
}
for (const params of [{ image: '' }, { images: [] }, { images: ['', null, {}] }, { mask: 'https://media.test/mask.png' },
  { input_references: [{ type: 'audio', url: 'https://media.test/ref.wav' }] }]) {
  assert.equal(inferImageOperation([], params), 'image-generation');
}
function oldNode(id = 1, type = 'EasyAPIImages') {
  return { id, type, properties: {},
    inputs: ['image', 'mask', 'audio', 'video', 'references'].map((name) => ({ name, type: name.toUpperCase(), link: null })),
    outputs: ['STRING', 'EASYAPI_JSON', 'IMAGE', 'MASK', 'AUDIO', 'VIDEO', 'EASYAPI_TASK', 'EASYAPI_MEDIA'].map((type, slot_index) => ({ name: type, type, slot_index, links: [] })),
    widgets_values: ['p1', 'image-model', 'image-task', 'my prompt', '{"seed":0}', false, 1800],
    widgets_values_named: { profile_id: 'p1', model: 'image-model', operation: 'image-task', prompt: 'my prompt', params_json: '{"seed":0}', wait_for_result: false, timeout: 1800 }
  };
}
for (const objectLinks of [false, true]) {
  const image = oldNode(), other = oldNode(3, 'EasyAPIChat');
  const preview = { id: 2, type: 'PreviewImage', inputs: [
    { name: 'images', type: 'IMAGE', link: 10 }, { name: 'json', type: 'EASYAPI_JSON', link: 11 },
    { name: 'text', type: 'STRING', link: 12 }, { name: 'mask', type: 'MASK', link: 13 }
  ] };
  const arrays = [[10,1,2,2,0,'IMAGE'],[11,1,1,2,1,'EASYAPI_JSON'],[12,1,0,2,2,'STRING'],[13,1,3,2,3,'MASK'],
    [14,3,7,1,4,'EASYAPI_MEDIA'],[15,3,4,1,2,'AUDIO'],[16,3,2,4,0,'IMAGE']];
  image.outputs[2].links = [10]; image.outputs[1].links = [11]; image.outputs[0].links = [12]; image.outputs[3].links = [13];
  image.inputs[4].link = 14; image.inputs[2].link = 15;
  other.outputs[7].links = [14]; other.outputs[4].links = [15]; other.outputs[2].links = [16];
  const unrelatedBefore = JSON.parse(JSON.stringify(other));
  const records = objectLinks ? arrays.map(([id,origin_id,origin_slot,target_id,target_slot,type]) => ({id,origin_id,origin_slot,target_id,target_slot,type})) : arrays;
  const graph = { nodes: [image,preview,other], links: records, reroutes: [{ id: 90, linkIds: [10,12] }] };
  const report = migrateImageWorkflow(graph);
  assert.equal(report.nodes, 1); assert.equal(report.disconnected, 3);
  assert.deepEqual(Array.from(image.outputs, (output) => output.type), ['IMAGE','STRING','EASYAPI_JSON']);
  assert.deepEqual(Array.from(image.outputs, (output) => output.slot_index), [0,1,2]);
  assert.deepEqual(Array.from(image.outputs[0].links), [10]); assert.deepEqual(Array.from(image.outputs[2].links), [11]);
  const imageLink = graph.links.find((link) => (objectLinks ? link.id : link[0]) === 10);
  const jsonLink = graph.links.find((link) => (objectLinks ? link.id : link[0]) === 11);
  const referenceLink = graph.links.find((link) => (objectLinks ? link.id : link[0]) === 14);
  assert.equal(objectLinks ? imageLink.origin_slot : imageLink[2], 0, 'Old image output must never feed the new response slot');
  assert.equal(objectLinks ? jsonLink.origin_slot : jsonLink[2], 2);
  assert.equal(objectLinks ? referenceLink.target_slot : referenceLink[4], 2);
  assert.equal(preview.inputs[0].link,10); assert.equal(preview.inputs[1].link,11);
  assert.equal(preview.inputs[2].link,null); assert.equal(preview.inputs[3].link,null);
  assert.equal(other.outputs[4].links.length,0); assert.deepEqual(Array.from(other.outputs[2].links),[16]);
  assert.equal(other.outputs[2].type,unrelatedBefore.outputs[2].type);
  assert.deepEqual(Array.from(image.widgets_values), ['p1','image-model','my prompt','{"seed":0}']);
  assert.equal(image.widgets_values_named.prompt,'my prompt'); assert.ok(!('operation' in image.widgets_values_named));
  assert.equal(image.properties.easyapi_image_layout,2);
  assert.equal(image.properties.easyapi_image_disconnected,3);
  assert.deepEqual(Array.from(graph.reroutes[0].linkIds), [10]);
  const stable = JSON.stringify(graph); const again = migrateImageWorkflow(graph);
  assert.equal(again.nodes,0); assert.equal(JSON.stringify(graph),stable,'Repeated loading must preserve the new output layout');
}
const nested = { definitions: { subgraphs: [{ nodes: [oldNode(91)], links: [] }] } };
assert.equal(migrateImageWorkflow(nested).nodes,1);
assert.equal(nested.definitions.subgraphs[0].nodes[0].properties.easyapi_image_layout,2);
const modern = { type: 'EasyAPIImages', outputs: [{type:'IMAGE'},{type:'STRING'},{type:'EASYAPI_JSON'}] };
assert.equal(legacyImageLayout(modern),false);
assert.equal(migrateImageWorkflow({nodes:[modern],links:[]}).nodes,0);
assert.equal(legacyImageLayout(oldNode(2,'EasyAPIVideo')),false);
// The custom DOM controls occupied a leading null/empty slot in some frontends.
for (const leading of [null, '']) {
  const shifted = oldNode(8); delete shifted.widgets_values_named;
  shifted.widgets_values.unshift(leading);
  migrateImageWorkflow({nodes:[shifted],links:[]});
  assert.deepEqual(Array.from(shifted.widgets_values),['p1','image-model','my prompt','{"seed":0}']);
}
const named = oldNode(9);
named.widgets_values = [null,'incorrect-profile','incorrect-model','image-task','incorrect-prompt','{}'];
named.widgets_values_named.prompt = 'authoritative named prompt';
migrateImageWorkflow({nodes:[named],links:[]});
assert.deepEqual(Array.from(named.widgets_values),['p1','image-model','authoritative named prompt','{"seed":0}']);
assert.equal(named.widgets_values_named.prompt,'authoritative named prompt');
const actualLegacy = oldNode(10); delete actualLegacy.widgets_values_named;
actualLegacy.widgets_values = ['', '', 'qwen-image-3.0-pro', 'image-generation', 'preserve migration prompt', '{"seed":0,"watermark":false}', true, 1800];
migrateImageWorkflow({nodes:[actualLegacy],links:[]});
assert.deepEqual(Array.from(actualLegacy.widgets_values), ['', 'qwen-image-3.0-pro', 'preserve migration prompt', '{"seed":0,"watermark":false}']);
const localized = oldNode(11);
localized.outputs[2].localized_name = '图片'; localized.outputs[2].label = 'Images';
localized.outputs[1].localized_name = 'JSON'; localized.outputs[1].label = 'My custom response';
migrateImageWorkflow({nodes:[localized],links:[]});
assert.equal(localized.outputs[0].name,'image'); assert.equal(localized.outputs[2].name,'响应');
assert.equal(localized.outputs[0].localized_name,undefined); assert.equal(localized.outputs[0].label,undefined);
assert.equal(localized.outputs[2].localized_name,undefined); assert.equal(localized.outputs[2].label,'My custom response');
const staleNew = { type:'EasyAPIImages', outputs:[{type:'IMAGE',localized_name:'文本',label:'Text'},
  {type:'STRING',localized_name:'JSON',label:'JSON'},{type:'EASYAPI_JSON',localized_name:'图片',label:'Images'}] };
migrateImageWorkflow({nodes:[staleNew],links:[]});
assert.deepEqual(Array.from(staleNew.outputs, slot=>slot.name),['image','url','响应']);
assert.ok(staleNew.outputs.every(slot=>slot.localized_name===undefined && slot.label===undefined));
'''
        result = subprocess.run([shutil.which("node"), "-", str(Path(__file__).parents[1] / "web" / "ui-state.js")],
                                input=script, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_translated_image_ports_follow_the_sync_schema(self):
        root = Path(__file__).parents[1]
        for locale, response in [("en", "response"), ("zh", "响应"), ("zh-TW", "回應")]:
            with self.subTest(locale=locale):
                data = json.loads((root / "locales" / locale / "nodeDefs.json").read_text(encoding="utf-8"))["EasyAPIImages"]
                self.assertEqual(data["outputs"], {"0": {"name": "image"}, "1": {"name": "url"}, "2": {"name": response}})
                self.assertEqual(set(data["inputs"]), {"profile_id", "model", "prompt", "params_json", "image", "mask", "references"})
                for obsolete in ("asynchronous", "异步", "非同步"):
                    self.assertNotIn(obsolete, data["description"])


if __name__ == "__main__":
    unittest.main()
