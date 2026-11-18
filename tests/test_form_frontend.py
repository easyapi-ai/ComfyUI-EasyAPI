"""Behavioral checks of the shipped JS, including real DOM event handlers."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class FormFrontendTests(unittest.TestCase):
    def test_selection_drafts_forms_language_and_video_ports(self):
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path'), { pathToFileURL } = require('node:url');
class FakeNode {
  constructor(tag = '') { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.style = { setProperty() {} }; this.value = ''; this._text = ''; this.className = ''; this.hidden = false; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children.forEach((child) => child.parentElement = null); this.children = []; this._text = ''; this.append(...children); }
  insertBefore(child, before) { const index = this.children.indexOf(before); child.parentElement = this; if (index < 0) this.children.push(child); else this.children.splice(index, 0, child); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) { this.children.forEach((child) => child.parentElement = null); this.children = []; this._text = String(value); }
  get isConnected() { return this.root || Boolean(this.parentElement?.isConnected); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); this.parentElement = null; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  removeEventListener(name, handler) { this.listeners[name] = (this.listeners[name] || []).filter((value) => value !== handler); }
  fire(name, properties = {}) { for (const handler of this.listeners[name] || []) handler({ target: this, key: '', preventDefault() {}, ...properties }); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  get classList() { const element = this; return { toggle(name, enabled) { const names = new Set(element.className.split(' ').filter(Boolean)); enabled ? names.add(name) : names.delete(name); element.className = [...names].join(' '); }, contains(name) { return element.className.split(' ').includes(name); } }; }
  contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
  select() {} scrollIntoView() {} setCustomValidity(value) { this.validationMessage = value; } reportValidity() {}
  querySelectorAll(selector) { return descendants(this).filter((element) => selector.split(',').includes(element.tagName.toLowerCase())); }
  showModal() {} close() {} pause() {} load() {}
}
function descendants(element) { return element.children.flatMap((child) => [child, ...descendants(child)]); }
const head = new FakeNode('head'), body = new FakeNode('body'); head.root = body.root = true;
const document = { head, body, createElement: (tag) => new FakeNode(tag), createTextNode: (text) => { const item = new FakeNode(); item.textContent = text; return item; } };
let locale = 'en', defaultProfile = null;
const requests = [], settingsEvents = new Map();
const fields = { type: 'object', required: ['count'], properties: {
  count: { type: 'integer', default: 1 }, seed: { type: 'integer', default: 42 },
  stream: { type: 'boolean', default: true }, settings: { type: 'object', properties: { enabled: { type: 'boolean' } } },
  tags: { type: 'array', items: { type: 'string' }, maxItems: 3 },
  image: { type: 'string' }, input_references: { type: 'array', items: { type: 'string' } }
} };
const fixtures = {
  profiles: [{ id: 'p1', name: 'First' }, { id: 'p2', name: 'Second' }],
  models: [{ id: 'saved-model', display_name: 'Saved model', capability_status: 'declared' }, { id: 'other-model', display_name: 'Other model', capability_status: 'unknown' }],
  presets: [], defaultPresets: {},
};
const app = { configuringGraph: false, extensionManager: { setting: { get: () => locale } }, ui: { settings: { addEventListener: (name, handler) => settingsEvents.set(name, handler) } }, registerExtension(extension) { this.extension = extension; } };
const api = { async fetchApi(address, options = {}) {
  requests.push({ address, options }); const url = new URL(address, 'https://comfy.test');
  let data;
  if (url.pathname === '/easyapi/profiles') data = { profiles: fixtures.profiles, default_profile_id: defaultProfile };
  else if (url.pathname === '/easyapi/models') data = { models: fixtures.models, authority: 'api_key' };
  else if (url.pathname === '/easyapi/schema') data = { request_schema: fields, matched: true, declared: true };
  else if (url.pathname === '/easyapi/bootstrap') data = { management_nonce: 'mock-nonce' };
  else if (url.pathname === '/easyapi/tasks') data = { tasks: [] };
  else if (url.pathname === '/easyapi/presets') data = { presets: fixtures.presets.filter((preset) => preset.profile_id === url.searchParams.get('profile_id') && (!url.searchParams.get('operation') || preset.operation === url.searchParams.get('operation'))), default_preset_ids: fixtures.defaultPresets };
  else if (options.method === 'POST' && url.pathname.startsWith('/easyapi/profiles/')) { const payload = JSON.parse(options.body); if (payload.make_default) defaultProfile = url.pathname.split('/').at(-1); data = { profile: payload, default_profile_id: defaultProfile }; }
  else if (url.pathname === '/easyapi/default-profile') { defaultProfile = JSON.parse(options.body).profile_id; data = { default_profile_id: defaultProfile }; }
  else data = {};
  return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)), text: async () => JSON.stringify(data) };
}, apiURL: (url) => '/api' + url };
const window = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {}, isSecureContext: true };
const context = { document, app, api, window, Node: FakeNode, URL, URLSearchParams, Map, Set, JSON, console,
  AbortController, setTimeout, clearTimeout, requestAnimationFrame: (handler) => setTimeout(handler, 0), Event: class {},
  navigator: {}, location: { href: 'https://comfy.test/', protocol: 'https:' },
};
vm.createContext(context);
for (const file of ['ui-state.js', 'ui-forms.js', 'ui-i18n.js', 'profile-panel.js', 'preset-panel.js', 'easyapi.js']) {
  const filename = path.join(process.argv[1], file);
  const original = fs.readFileSync(filename, 'utf8');
  const names = [...original.matchAll(/^export (?:const|function|class) (\w+)/gm)].map((match) => match[1]);
  let source = original.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  source = source.replaceAll('import.meta.url', JSON.stringify(pathToFileURL(filename).href));
  const expose = file === 'easyapi.js' ? 'globalThis.hooks = { NodePanel, ModelPicker, ProfileManager, PresetPanel, registeredOperations, credentialHint, officialAuthorizationURL, OFFICIAL_GATEWAY, selectProfile, parseParams, requiredDefaults, writeAt, draftKey, readDraft, videoPortState, t, openProfiles };' : 'Object.assign(globalThis, {' + names.join(',') + '});';
  vm.runInContext('(function(){' + source + '\n' + expose + '})()', context, { filename });
}
const h = context.hooks;
const tick = async () => { for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0)); };
const json = (value) => JSON.stringify(value);
function makeNode(type = 'EasyAPIImages', stored = null) {
  const values = stored?.values || { profile_id: '', model: 'saved-model', operation: 'image-generation', prompt: 'keep this prompt', params_json: '{"custom":7}', mode: 'auto' };
  const widgets = Object.entries(values).map(([name, value]) => ({ name, value, options: {}, callback() {} }));
  const inputs = widgets.map((widget) => ({ name: widget.name, link: null }));
  return { id: 12, type, comfyClass: type, widgets, inputs, properties: stored?.properties || {}, size: [450, 600], graph: { setDirtyCanvas() {}, change() {} }, computeSize: () => [450, 600], setSize() {} };
}
const get = (node, name) => node.widgets.find((widget) => widget.name === name).value;
const findInput = (panel, pathValue) => descendants(panel.schema).find((element) => element.dataset.path === json(pathValue));
(async () => {
  assert.equal(h.selectProfile('', fixtures.profiles, null), '');
  assert.equal(h.selectProfile('', fixtures.profiles, 'p2'), 'p2');
  assert.equal(h.selectProfile('missing-profile', fixtures.profiles, 'p2'), 'missing-profile');
  assert.equal(h.selectProfile('', [fixtures.profiles[0]], null), 'p1');
  assert.throws(() => h.parseParams('{"api_key":"never-save"}'), /credentialsInParams/);
  const node = makeNode(), panel = new h.NodePanel(node); body.append(panel.root); await tick();
  assert.equal(get(node, 'profile_id'), ''); assert.equal(get(node, 'model'), 'saved-model');
  assert.ok(panel.status.textContent.includes('Several connections'));
  defaultProfile = 'p2'; await panel.refreshProfiles(); await tick();
  assert.equal(get(node, 'profile_id'), 'p2'); assert.equal(get(node, 'model'), 'saved-model');
  assert.equal(JSON.parse(get(node, 'params_json')).custom, 7);
  assert.equal(JSON.parse(get(node, 'params_json')).count, 1);
  assert.equal(JSON.parse(get(node, 'params_json')).seed, undefined); // Optional defaults remain hints.
  assert.equal(node.widgets.find((widget) => widget.name === 'model').hidden, true);
  assert.equal(descendants(panel.root).filter((element) => element.role === 'combobox').length, 1);
  const modelRequest = requests.find((entry) => entry.address.startsWith('/easyapi/models?'));
  assert.equal(new URL(modelRequest.address, 'https://x.test').searchParams.get('operation'), 'image-generation');
  assert.equal(findInput(panel, ['image']), undefined); assert.equal(findInput(panel, ['input_references']), undefined);
  const seed = findInput(panel, ['seed']); seed.value = '0'; seed.fire('change');
  const stream = findInput(panel, ['stream']); stream.value = 'false'; stream.fire('change');
  assert.equal(JSON.parse(get(node, 'params_json')).seed, 0); assert.equal(JSON.parse(get(node, 'params_json')).stream, false);
  const nested = findInput(panel, ['settings', 'enabled']); nested.value = 'false'; nested.fire('change');
  assert.equal(JSON.parse(get(node, 'params_json')).settings.enabled, false);
  const add = descendants(panel.schema).find((element) => element.tagName === 'BUTTON' && element.textContent === 'Add item');
  add.fire('click'); const item = findInput(panel, ['tags', 0]); item.value = 'alpha'; item.fire('change');
  assert.equal(JSON.parse(get(node, 'params_json')).tags[0], 'alpha');
  panel.chooseModel('other-model'); await tick(); panel.commitParams({ seed: 9, stream: false });
  panel.chooseModel('saved-model'); await tick();
  assert.equal(JSON.parse(get(node, 'params_json')).seed, 0); assert.equal(JSON.parse(get(node, 'params_json')).custom, 7);
  assert.equal(get(node, 'prompt'), 'keep this prompt');
  const stored = { values: Object.fromEntries(node.widgets.map((widget) => [widget.name, widget.value])), properties: JSON.parse(json(node.properties)) };
  panel.dispose(); const restoredNode = makeNode('EasyAPIImages', stored), restored = new h.NodePanel(restoredNode); body.append(restored.root); restored.configure(); await tick();
  restored.chooseModel('other-model'); await tick(); assert.equal(JSON.parse(get(restoredNode, 'params_json')).seed, 9);
  assert.equal(JSON.parse(get(restoredNode, 'params_json')).stream, false);
  restored.jsonEditor.value = '{"new_field":{"enabled":false},"zero":0}'; restored.jsonEditor.fire('input'); restored.jsonEditor.fire('change');
  assert.equal(JSON.parse(get(restoredNode, 'params_json')).new_field.enabled, false);
  assert.equal(JSON.parse(get(restoredNode, 'params_json')).zero, 0);
  const videoNode = makeNode('EasyAPIVideo'); videoNode.inputs.push({ name: 'first_frame', link: null }, { name: 'reference_video_1', link: null });
  const video = new h.NodePanel(videoNode); body.append(video.root); await tick();
  video.metadata = { video: { roles: ['first_frame'], scenarios: ['text-to-video', 'image-to-video'], variants: { 'text-to-video': { roles: [] }, 'image-to-video': { roles: ['first_frame'] } } } };
  video.updateVideo();
  const firstIndex = videoNode.inputs.findIndex((input) => input.name === 'first_frame');
  assert.ok(!video.videoCSS.includes(`12-in-${firstIndex}`)); // Initial text mode must still allow connecting a first frame.
  const referenceIndex = videoNode.inputs.findIndex((input) => input.name === 'reference_video_1');
  assert.ok(video.videoCSS.includes(`12-in-${referenceIndex}`));
  videoNode.inputs[referenceIndex].link = 8; video.updateVideo();
  assert.ok(video.videoError.textContent.includes('Connections have been preserved'));
  assert.ok(video.videoCSS.includes('outline:')); assert.ok(!video.videoCSS.includes('display:none'));
  assert.equal(videoNode.inputs[referenceIndex].link, 8);
  const unknown = h.videoPortState(videoNode.inputs, { roles: [], scenarios: [], variants: {} }); assert.equal(unknown.known, false);
  const textOnly = h.videoPortState(videoNode.inputs, { roles: [], scenarios: ['text-to-video'], variants: {} }); assert.equal(textOnly.invalid.length, 1);
  locale = 'zh'; assert.equal(h.t('model'), '模型'); await restored.refreshModels();
  assert.equal(new URL(requests.filter((entry) => entry.address.startsWith('/easyapi/models?')).at(-1).address, 'https://x.test').searchParams.get('locale'), 'zh');
  locale = 'zh-TW'; assert.equal(h.t('connection'), '連線設定');
  assert.equal(h.t('audioTruncated'), ' · Audio retention limit reached');
  restored.dispose(); video.dispose();
  // Only a new, still-empty node receives an operation default after model permission lookup.
  locale = 'en';
  const defaultPreset = { id: 'default-image', profile_id: 'p2', operation: 'image-generation', model: 'saved-model', name: 'Zero / false', params: { seed: 0, stream: false } };
  fixtures.presets = [defaultPreset]; fixtures.defaultPresets = { 'image-generation': defaultPreset.id };
  const emptyValues = { profile_id: 'p2', model: '', operation: 'image-generation', prompt: 'preserve user prompt', params_json: '{}', mode: 'auto' };
  const freshNode = makeNode('EasyAPIImages', { values: emptyValues }), fresh = new h.NodePanel(freshNode); body.append(fresh.root); await tick();
  assert.equal(get(freshNode, 'model'), 'saved-model'); assert.equal(JSON.parse(get(freshNode, 'params_json')).seed, 0); assert.equal(JSON.parse(get(freshNode, 'params_json')).stream, false);
  assert.equal(get(freshNode, 'prompt'), 'preserve user prompt');
  const importedValues = { ...emptyValues, model: 'other-model', params_json: '{"zero":0,"flag":false}' };
  const importedNode = makeNode('EasyAPIImages', { values: importedValues }), imported = new h.NodePanel(importedNode); body.append(imported.root); imported.configure(); await tick();
  assert.equal(get(importedNode, 'model'), 'other-model'); assert.equal(get(importedNode, 'params_json'), '{"zero":0,"flag":false}');
  importedNode.inputs.push({ name: 'image', link: 77 }); await imported.applyPresetRecord(defaultPreset, false);
  assert.equal(get(importedNode, 'model'), 'saved-model'); assert.equal(importedNode.inputs.at(-1).link, 77); assert.equal(get(importedNode, 'prompt'), 'preserve user prompt');
  fixtures.presets = [{ ...defaultPreset, model: 'not-permitted-model' }];
  const unavailableNode = makeNode('EasyAPIImages', { values: { ...emptyValues } }), unavailable = new h.NodePanel(unavailableNode); body.append(unavailable.root); await tick();
  assert.equal(get(unavailableNode, 'model'), ''); assert.equal(get(unavailableNode, 'params_json'), '{}');
  assert.ok(unavailable.status.textContent.includes('not available'));
  fresh.dispose(); imported.dispose(); unavailable.dispose(); fixtures.presets = []; fixtures.defaultPresets = {};

  // The central editor creates, updates, defaults and deletes parameter presets without generating content.
  const presetMutations = []; let savedPresets = [], defaultPresetMap = {};
  const presetRequest = async (address) => {
    if (address.startsWith('/easyapi/presets?')) return { presets: savedPresets, default_preset_ids: defaultPresetMap };
    if (address.startsWith('/easyapi/models?')) return { models: fixtures.models };
    if (address.startsWith('/easyapi/schema?')) return { request_schema: fields, matched: true, declared: true };
    return {};
  };
  const presetMutation = async (address, payload, method = 'POST') => {
    presetMutations.push({ address, payload, method });
    if (method === 'DELETE') { savedPresets = []; return { ok: true }; }
    const record = { ...payload, id: method === 'POST' ? 'created-preset' : savedPresets[0].id };
    savedPresets = [record]; if (payload.make_default) defaultPresetMap[payload.operation] = record.id;
    return { preset: record };
  };
  const presetEditor = new h.PresetPanel({ request: presetRequest, mutation: presetMutation, modelPicker: (handler) => new h.ModelPicker(handler), getOperations: () => [{ id: 'image-generation', hasModel: true }] });
  body.append(presetEditor.root); presetEditor.setProfiles(fixtures.profiles, 'p2', 'p2'); await tick();
  await presetEditor.newFromNode(defaultPreset); presetEditor.name.value = 'Saved zeros'; presetEditor.defaultCheck.checked = true; await presetEditor.save();
  assert.equal(presetMutations[0].method, 'POST'); assert.equal(presetMutations[0].payload.params.seed, 0); assert.equal(presetMutations[0].payload.params.stream, false);
  assert.equal(presetMutations[0].payload.make_default, true); assert.equal(presetMutations[0].payload.prompt, undefined);
  await presetEditor.edit(savedPresets[0]); presetEditor.params.seed = 2; presetEditor.syncJSON(); await presetEditor.save();
  assert.equal(presetMutations[1].method, 'PUT'); assert.equal(presetMutations[1].payload.model, 'saved-model');
  await presetEditor.remove(); assert.equal(presetMutations[2].method, 'DELETE'); assert.ok(presetMutations[2].address.endsWith('?profile_id=p2')); presetEditor.destroy();

  // Saved keys never reappear in the central connection manager.
  locale = 'en'; const managerCalls = [], opened = []; let pollCount = 0, authStartStatus = 'pending';
  const publicRows = [
    { id: 'manual', name: 'Manual', base_url: h.OFFICIAL_GATEWAY, has_key: true, key_hint: '••••4321', api_key: 'server-must-not-return-this-key', auth_method: 'manual', connection_status: 'untested' },
    { id: 'easyapi-env', name: 'Environment', base_url: h.OFFICIAL_GATEWAY, has_key: true, key_hint: '••••9999', auth_method: 'environment', read_only: true }
  ];
  const managerRequest = async (address, options = {}) => {
    managerCalls.push({ address, options });
    if (address === '/easyapi/profiles') return { profiles: publicRows, default_profile_id: 'manual' };
    if (address.endsWith('/test')) return { ok: true, model_count: 3, checked_at: 42 };
    if (address === '/easyapi/auth/start') return authStartStatus === 'failed' ? { status: 'failed', message: 'Gateway auth configuration is unavailable' } : { flow_id: 'opaque-flow', status: 'pending', expires_in: 300, retry_after: 1, authorize_url: 'https://www.easyapi.com/cli/authorize?session=opaque' };
    if (address.endsWith('/poll')) {
      if (++pollCount === 1) return { status: 'pending', retry_after: 1 };
      publicRows.push({ id: 'easyapi', name: 'Authorized', base_url: h.OFFICIAL_GATEWAY, has_key: true, key_hint: '••••8888', auth_method: 'official' });
      return { status: 'authorized', profile: publicRows.at(-1) };
    }
    return {};
  };
  let changed = 0;
  const manager = new h.ProfileManager({ request: managerRequest, bootstrap: async () => 'mock-management-nonce', onChanged: () => changed++, openExternal: (url) => opened.push(url) });
  body.append(manager.root); assert.equal(manager.base.value, 'https://token.easyapi.com');
  await manager.refresh(); manager.select('manual');
  assert.equal(manager.key.value, ''); assert.equal(manager.selected().api_key, undefined);
  assert.ok(manager.root.textContent.includes('••••4321')); assert.ok(!manager.root.textContent.includes('server-must-not-return-this-key'));
  await manager.test();
  const probe = managerCalls.find((entry) => entry.address.endsWith('/test'));
  assert.equal(probe.options.method, 'POST'); assert.equal(probe.options.headers['X-EasyAPI-Management'], 'mock-management-nonce');
  assert.ok(manager.notice.textContent.includes('3 permitted models'));
  manager.select('easyapi-env'); assert.equal(manager.saveButton.disabled, true); assert.equal(manager.deleteButton.disabled, true); assert.equal(manager.key.readOnly, true);
  window.isSecureContext = false;
  manager.newProfile();
  assert.equal(manager.authorizeButton.disabled, true);
  assert.ok(manager.authNotice.textContent.includes('HTTPS or localhost'));
  assert.equal(manager.saveButton.disabled, false);
  assert.equal(manager.manual.open, true);
  manager.key.value = 'mock-only-key'; await manager.save(); assert.equal(manager.key.value, '');
  const savedManually = managerCalls.find((entry) => entry.address === '/easyapi/profiles/easyapi' && entry.options.method === 'POST');
  assert.equal(JSON.parse(savedManually.options.body).api_key, 'mock-only-key');
  window.isSecureContext = true;
  manager.newProfile(); manager.makeDefault.checked = true;
  await manager.startAuthorization(); clearTimeout(manager.pollTimer);
  assert.equal(opened.length, 1); assert.ok(opened[0].startsWith('https://www.easyapi.com/cli/authorize?'));
  const authStart = managerCalls.find((entry) => entry.address === '/easyapi/auth/start');
  const authPayload = JSON.parse(authStart.options.body);
  assert.equal(authPayload.make_default, true); assert.equal(authPayload.api_key, undefined);
  await manager.pollAuthorization(manager.flowVersion); clearTimeout(manager.pollTimer); assert.equal(manager.flow.status, 'pending');
  await manager.pollAuthorization(manager.flowVersion);
  assert.equal(manager.flow.status, 'authorized'); assert.equal(manager.key.value, ''); assert.ok(changed >= 2);
  manager.newProfile(); await manager.startAuthorization(); clearTimeout(manager.pollTimer); await manager.cancelAuth(); assert.equal(manager.flow.status, 'cancelled');
  manager.newProfile(); authStartStatus = 'failed'; await manager.startAuthorization(); assert.equal(manager.flow.status, 'failed'); assert.ok(manager.authStatus.textContent.includes('Gateway auth configuration'));
  assert.throws(() => h.officialAuthorizationURL('https://evil.invalid/authorize'), /trusted/);
  assert.throws(() => h.officialAuthorizationURL('http://token.easyapi.com/authorize'), /trusted/);
  assert.throws(() => h.officialAuthorizationURL('https://token.easyapi.com/cli/authorize'), /trusted/);
  manager.showError(Object.assign(new Error('Access denied'), { code: 'invalid_token' })); assert.ok(manager.notice.textContent.includes('invalid_token'));
  manager.destroy();
  // Sidebar, topbar command and node shortcut all open one singleton entry.
  let active = false, toggles = 0; const sidebarContainer = new FakeNode('aside'); body.append(sidebarContainer);
  app.extensionManager.command = { commands: [], execute: async (id) => {
    assert.equal(id, 'Workspace.ToggleSidebarTab.easyapi-connections'); active = !active; toggles++; app.sidebar.render(sidebarContainer);
  } };
  app.extensionManager.registerSidebarTab = (tab) => { app.sidebar = tab; app.extensionManager.command.commands.push({ id: 'Workspace.ToggleSidebarTab.' + tab.id, active: () => active }); };
  h.registeredOperations.set('image-generation', { id: 'image-generation', hasModel: true });
  app.extension.setup(); assert.equal(app.sidebar.id, 'easyapi-connections');
  await h.openProfiles('p1'); await tick(); assert.equal(toggles, 1);
  const firstRoot = sidebarContainer.children[0];
  await app.extension.commands.find((command) => command.id === 'EasyAPI.OpenManager').function(); await tick();
  assert.equal(toggles, 1); assert.equal(sidebarContainer.children[0], firstRoot);
  assert.equal(app.extension.menuCommands[0].path[0], 'EasyAPI'); app.sidebar.destroy();
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-e", script, str(Path(__file__).parents[1] / "web")],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
