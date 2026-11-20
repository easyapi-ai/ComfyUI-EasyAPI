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
  get lastElementChild() { return this.children.at(-1); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); this.parentElement = null; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  removeEventListener(name, handler) { this.listeners[name] = (this.listeners[name] || []).filter((value) => value !== handler); }
  fire(name, properties = {}) { for (const handler of this.listeners[name] || []) handler({ target: this, key: '', preventDefault() {}, ...properties }); }
  dispatchEvent(event) { this.fire(event.type, event); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  get classList() { const element = this; return { toggle(name, enabled) { const names = new Set(element.className.split(' ').filter(Boolean)); enabled ? names.add(name) : names.delete(name); element.className = [...names].join(' '); }, contains(name) { return element.className.split(' ').includes(name); } }; }
  contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
  closest(selector) { return this.classList.contains(selector.slice(1)) ? this : this.parentElement?.closest(selector) || null; }
  focus() {} select() {} scrollIntoView() {} setCustomValidity(value) { this.validationMessage = value; } reportValidity() {}
  querySelectorAll(selector) { return descendants(this).filter((element) => selector.split(',').includes(element.tagName.toLowerCase())); }
  showModal() {} close() {} pause() {} load() {}
}
function descendants(element) { return element.children.flatMap((child) => [child, ...descendants(child)]); }
const head = new FakeNode('head'), body = new FakeNode('body'); head.root = body.root = true;
const document = { head, body, addEventListener() {}, removeEventListener() {}, createElement: (tag) => new FakeNode(tag), createTextNode: (text) => { const item = new FakeNode(); item.textContent = text; return item; } };
let locale = 'en', defaultProfile = null, holdNodeModels = false, releaseNodeModels;
const requests = [], settingsEvents = new Map();
const fields = { type: 'object', required: ['count'], properties: {
  count: { type: 'integer', default: 1 }, seed: { type: 'integer', default: 42, minimum: 0, description: 'Seed used by the model.' },
  stream: { type: 'boolean', default: true }, settings: { type: 'object', properties: { enabled: { type: 'boolean' } } },
  tags: { type: 'array', items: { type: 'string' }, maxItems: 3 },
  image: { type: 'string' }, input_references: { type: 'array', items: { type: 'string' } }
} };
const videoFields = { type: 'object', properties: {
  resolution: { type: 'string', enum: ['1080P', '720P', '480P'], default: '1080P' },
  duration: { type: 'integer', minimum: 2, maximum: 30, default: 5 },
  seed: { type: 'integer', minimum: 0, maximum: 2147483647 },
  generate_audio: { type: 'boolean', default: true }, watermark: { type: 'boolean', default: false },
} };
const fixtures = {
  profiles: [{ id: 'p1', name: 'First', model_catalog_revision: 'rev-1' }, { id: 'p2', name: 'Second', model_catalog_revision: 'rev-2' }],
  models: [{ id: 'saved-model', display_name: 'Saved model', capability_status: 'declared' }, { id: 'other-model', display_name: 'Other model', capability_status: 'unknown' }],
  presets: [], defaultPresets: {},
};
const app = { configuringGraph: false, extensionManager: { setting: { get: () => locale } }, ui: { settings: { addEventListener: (name, handler) => settingsEvents.set(name, handler) } }, registerExtension(extension) { this.extension = extension; } };
const api = { async fetchApi(address, options = {}) {
  requests.push({ address, options }); const url = new URL(address, 'https://comfy.test');
  let data;
  if (url.pathname === '/easyapi/profiles') data = { profiles: fixtures.profiles, default_profile_id: defaultProfile };
  else if (url.pathname === '/easyapi/models') {
    if (holdNodeModels && url.searchParams.get('profile_id') === 'p1') {
      data = await new Promise((resolve) => { releaseNodeModels = resolve; });
    } else data = { models: fixtures.models, authority: 'api_key' };
  }
  else if (url.pathname === '/easyapi/schema') data = { request_schema: url.searchParams.get('operation') === 'video-generation' ? videoFields : fields, matched: true, declared: true };
  else if (url.pathname === '/easyapi/bootstrap') data = { management_nonce: 'mock-nonce' };
  else if (url.pathname === '/easyapi/prompt/models') data = { models: [{ id: 'prompt-text' }] };
  else if (url.pathname === '/easyapi/prompt/transform') data = { text: 'improved prompt' };
  else if (url.pathname === '/easyapi/tasks') data = { tasks: [] };
  else if (url.pathname === '/easyapi/presets') data = { presets: fixtures.presets.filter((preset) => preset.profile_id === url.searchParams.get('profile_id') && (!url.searchParams.get('operation') || preset.operation === url.searchParams.get('operation'))), default_preset_ids: fixtures.defaultPresets };
  else if (options.method === 'POST' && url.pathname.startsWith('/easyapi/profiles/')) { const payload = JSON.parse(options.body); if (payload.make_default) defaultProfile = url.pathname.split('/').at(-1); data = { profile: payload, default_profile_id: defaultProfile }; }
  else if (url.pathname === '/easyapi/default-profile') { defaultProfile = JSON.parse(options.body).profile_id; data = { default_profile_id: defaultProfile }; }
  else data = {};
  return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)), text: async () => JSON.stringify(data) };
}, apiURL: (url) => '/api' + url };
const window = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {}, isSecureContext: true };
const context = { document, app, api, window, Node: FakeNode, URL, URLSearchParams, Map, Set, JSON, console,
  AbortController, setTimeout, clearTimeout, requestAnimationFrame: (handler) => setTimeout(handler, 0), Event: class { constructor(type) { this.type = type; } },
  navigator: {}, location: { href: 'https://comfy.test/', protocol: 'https:' },
};
vm.createContext(context);
for (const file of ['ui-state.js', 'ui-forms.js', 'ui-i18n.js', 'profile-panel.js', 'preset-panel.js', 'prompt-editor.js', 'model-catalog.js', 'easyapi.js']) {
  const filename = path.join(process.argv[2], file);
  const original = fs.readFileSync(filename, 'utf8');
  const names = [...original.matchAll(/^export (?:const|function|class) (\w+)/gm)].map((match) => match[1]);
  let source = original.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  source = source.replaceAll('import.meta.url', JSON.stringify(pathToFileURL(filename).href));
  const expose = file === 'easyapi.js' ? 'globalThis.hooks = { NodePanel, ModelPicker, ProfileManager, PresetPanel, registeredOperations, credentialHint, officialAuthorizationURL, OFFICIAL_GATEWAY, selectProfile, parseParams, requiredDefaults, writeAt, draftKey, readDraft, videoPortState, t, openProfiles, getProfileManager, promptModels, transformPrompt };' : 'Object.assign(globalThis, {' + names.join(',') + '});';
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
  const chosen = [], picker = new h.ModelPicker((id) => chosen.push(id));
  const pickerPanel = new FakeNode('section'); pickerPanel.className = 'ea-node-panel'; pickerPanel.append(picker.root);
  pickerPanel.clientTop = 0; pickerPanel.clientHeight = 220;
  pickerPanel.getBoundingClientRect = () => ({ top: 40 });
  let anchorTop = 68, availableHeight;
  picker.root.offsetWidth = 200; picker.root.getBoundingClientRect = () => ({ top: anchorTop, bottom: anchorTop + 20, width: 160 });
  picker.list.scrollHeight = 1000; picker.list.style.setProperty = (key, height) => { if (key === '--ea-model-list-height') availableHeight = height; };
  window.innerHeight = 640;
  const longCatalog = Array.from({ length: 101 }, (_, index) => ({ id: `model-${index}` }));
  picker.setItems(longCatalog, 'model-0');
  picker.input.fire('focus');
  assert.equal(availableHeight, '155px', 'Zoomed node panel leaves enough room below the picker');
  assert.equal(picker.list.classList.contains('ea-model-list-above'), false);
  assert.equal(picker.list.children[0].getAttribute('aria-selected'), 'true');
  assert.equal(picker.list.children[0].children[1].textContent, '✓');
  assert.equal(picker.list.children[0].children[1].hidden, false);
  assert.equal(picker.list.children[1].children[1].hidden, true);
  let forwarded = true, cancelled = false;
  const wheel = { deltaX: 0, deltaY: 120, stopPropagation() { forwarded = false; }, preventDefault() { cancelled = true; } };
  picker.list.fire('wheel', wheel);
  assert.equal(forwarded, false, 'Model options must scroll without zooming the canvas');
  assert.equal(cancelled, false, 'Native scroll must remain enabled');
  forwarded = true; picker.list.fire('wheel', { ...wheel, ctrlKey: true });
  assert.equal(forwarded, true, 'Canvas pinch zoom must remain available');
  assert.equal(picker.root.dataset.captureWheel, 'true');
  assert.equal(picker.list.tabIndex, -1, 'The scrollbar must be able to move focus inside the picker');
  picker.root.fire('focusout', { target: picker.input, relatedTarget: picker.list });
  assert.equal(picker.list.hidden, false);
  picker.root.fire('focusout', { target: picker.list, relatedTarget: body });
  assert.equal(picker.list.hidden, true);
  anchorTop = 186;
  picker.input.fire('click');
  assert.equal(availableHeight, '177px');
  assert.equal(picker.list.classList.contains('ea-model-list-above'), true, 'Picker opens upwards when clipped below');
  assert.equal(picker.list.children.length, longCatalog.length);
  assert.equal(picker.list.children[0].tabIndex, -1);
  const key = (name) => picker.root.fire('keydown', { key: name, target: picker.input, stopPropagation() {} });
  key('ArrowUp'); key('Enter');
  assert.deepEqual(chosen, ['model-100']);
  picker.input.fire('click');
  assert.equal(picker.list.children[100].getAttribute('aria-selected'), 'true');
  assert.equal(picker.list.children[100].children[1].hidden, false);
  assert.equal(picker.list.children[0].children[1].hidden, true);
  picker.input.value = 'model-1'; picker.input.fire('input'); key('ArrowDown'); key('ArrowDown'); key('Enter');
  assert.deepEqual(chosen, ['model-100', 'model-10'], 'An explicitly highlighted option wins over a matching input ID');
  picker.input.fire('click'); picker.input.value = 'model'; picker.input.fire('input'); key('Enter');
  assert.equal(chosen.length, 2, 'Enter does not pick an arbitrary result from an ambiguous search');
  picker.root.fire('focusout', { target: picker.input, relatedTarget: body });
  assert.equal(picker.input.value, 'model-10');

  assert.equal(h.selectProfile('', fixtures.profiles, null), '');
  assert.equal(h.selectProfile('', fixtures.profiles, 'p2'), 'p2');
  assert.equal(h.selectProfile('missing-profile', fixtures.profiles, 'p2'), 'missing-profile');
  assert.equal(h.selectProfile('', [fixtures.profiles[0]], null), 'p1');
  assert.throws(() => h.parseParams('{"api_key":"never-save"}'), /credentialsInParams/);
  const node = makeNode(), panel = new h.NodePanel(node); body.append(panel.root); await tick();
  node.easyapiPanel = panel;
  assert.equal(get(node, 'profile_id'), ''); assert.equal(get(node, 'model'), 'saved-model');
  assert.ok(panel.status.textContent.includes('Several connections'));
  assert.equal(descendants(panel.root).some((element) => element.classList.contains('ea-connection-controls')), false);
  defaultProfile = 'p2'; await h.getProfileManager().onChanged(); await tick();
  assert.equal(get(node, 'profile_id'), 'p2'); assert.equal(get(node, 'model'), 'saved-model');
  const nodeMenuItems = app.extension.getNodeMenuItems(node);
  assert.equal(nodeMenuItems[0].content, 'Refresh EasyAPI models');
  assert.equal(nodeMenuItems[1].submenu.options[1].content, '✓ p2');
  const modelRequestCount = () => requests.filter((entry) => entry.address.startsWith('/easyapi/models?')).length;
  const countWithFirstNode = modelRequestCount();
  const extraNode = makeNode(), extra = new h.NodePanel(extraNode); body.append(extra.root); await tick();
  assert.equal(get(extraNode, 'profile_id'), 'p2');
  assert.equal(modelRequestCount(), countWithFirstNode, 'New nodes reuse a fresh model catalog');
  extra.dispose();
  await nodeMenuItems[0].callback();
  assert.equal(modelRequestCount(), countWithFirstNode + 1);
  assert.ok(requests.some((entry) => entry.address.includes('/easyapi/models?') && entry.address.includes('refresh=1')));
  const promptNodeInput = node.inputs.find((input) => input.name === 'prompt');
  promptNodeInput.link = 51; panel.updateBacking();
  assert.equal(panel.promptEditor.root.hidden, true);
  assert.equal(panel.promptHint.hidden, false);
  promptNodeInput.link = null; panel.updateBacking();
  assert.equal(panel.promptEditor.root.hidden, false);
  assert.equal(panel.promptEditor.textarea.value, 'keep this prompt');
  panel.promptEditor.textarea.value = 'edited locally';
  panel.promptEditor.textarea.fire('input', { inputType: 'insertText' });
  panel.promptEditor.textarea.fire('blur');
  assert.equal(get(node, 'prompt'), 'edited locally');
  assert.ok(panel.promptHistoryKey(false));
  assert.ok(!JSON.stringify(node.properties).includes('edited locally'));
  const promptWidget = node.widgets.find((widget) => widget.name === 'prompt');
  promptWidget.value = 'keep this prompt'; promptWidget.callback();
  assert.equal(panel.promptEditor.textarea.value, 'keep this prompt');
  assert.ok(!requests.some((entry) => entry.address.startsWith('/easyapi/prompt/')), 'Typing does not contact a text model');
  const promptModels = await h.promptModels('p2');
  assert.equal(promptModels[0].id, 'prompt-text');
  assert.ok(requests.at(-1).address.includes('profile_id=p2'));
  const promptResult = await h.transformPrompt({ profile_id: 'p2', model: 'prompt-text', action: 'optimize', prompt: 'a cloud' });
  assert.equal(promptResult.text, 'improved prompt');
  assert.equal(requests.at(-2).address, '/easyapi/bootstrap');
  assert.equal(requests.at(-1).options.headers['X-EasyAPI-Management'], 'mock-nonce');
  assert.equal(JSON.parse(requests.at(-1).options.body).model, 'prompt-text');
  panel.chooseProfile('');
  assert.equal(get(node, 'profile_id'), 'p2');
  assert.equal(JSON.parse(get(node, 'params_json')).custom, 7);
  assert.equal(JSON.parse(get(node, 'params_json')).count, 1);
  assert.equal(JSON.parse(get(node, 'params_json')).seed, undefined); // Optional defaults remain hints.
  assert.equal(node.widgets.find((widget) => widget.name === 'model').hidden, true);
  assert.equal(descendants(panel.root).filter((element) => element.role === 'combobox').length, 1);
  const modelRequest = requests.find((entry) => entry.address.startsWith('/easyapi/models?'));
  assert.equal(new URL(modelRequest.address, 'https://x.test').searchParams.get('operation'), 'image-generation');
  assert.equal(findInput(panel, ['image']), undefined); assert.equal(findInput(panel, ['input_references']), undefined);
  const seed = findInput(panel, ['seed']); seed.value = '0'; seed.fire('change');
  assert.ok(seed.getAttribute('aria-describedby'));
  const seedHelp = descendants(panel.schema).find((element) => element.id === seed.getAttribute('aria-describedby'));
  assert.equal(seedHelp.textContent, 'Seed used by the model.');
  seed.value = '-1'; seed.fire('change'); assert.ok(seed.validationMessage);
  const stream = findInput(panel, ['stream']); assert.equal(stream.checked, true); stream.checked = false; stream.fire('change');
  assert.equal(JSON.parse(get(node, 'params_json')).seed, 0); assert.equal(JSON.parse(get(node, 'params_json')).stream, false);
  const nested = findInput(panel, ['settings', 'enabled']); nested.checked = false; nested.fire('change');
  assert.equal(JSON.parse(get(node, 'params_json')).settings.enabled, false);
  const add = descendants(panel.schema).find((element) => element.tagName === 'BUTTON' && element.textContent === 'Add item');
  add.fire('click'); const item = findInput(panel, ['tags', 0]); item.value = 'alpha'; item.fire('change');
  descendants(panel.schema).find((element) => element.tagName === 'BUTTON' && element.textContent === 'Add item').fire('click');
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
  holdNodeModels = true;
  restored.chooseProfile('p1');
  assert.equal(restored.picker.input.disabled, true);
  assert.equal(restored.picker.items.length, 0);
  const modelInput = restoredNode.inputs.find((input) => input.name === 'model');
  modelInput.link = 9; restored.updateBacking(); await restored.refreshModels();
  releaseNodeModels({ models: [{ id: 'out-of-scope' }] }); await tick();
  assert.equal(restored.picker.input.disabled, true);
  assert.equal(restored.picker.items.length, 0);
  assert.equal(restored.schema.children.length, 0);
  holdNodeModels = false;
  modelInput.link = null; restored.updateBacking(); await restored.refreshModels();
  assert.equal(restored.picker.input.disabled, false);
  const videoNode = makeNode('EasyAPIVideo', { values: { profile_id: 'p2', model: 'saved-model', operation: 'video-generation', prompt: '', params_json: '{}', mode: 'auto' } });
  videoNode.inputs.push({ name: 'first_frame', link: null }, { name: 'reference_video_1', link: null });
  const video = new h.NodePanel(videoNode); body.append(video.root);
  assert.equal(video.videoBox.hidden, true);
  await tick();
  const resolution = findInput(video, ['resolution']);
  assert.deepEqual(resolution.children.map((option) => option.textContent), ['1080P', '720P', '480P']);
  assert.equal(resolution.value, '"1080P"');
  const duration = findInput(video, ['duration']);
  assert.deepEqual([duration.type, duration.min, duration.max, duration.value], ['range', '2', '30', '5']);
  const audio = findInput(video, ['generate_audio']);
  assert.equal(audio.getAttribute('role'), 'switch');
  assert.equal(audio.checked, true);
  assert.equal(findInput(video, ['watermark']).checked, false);
  assert.deepEqual(JSON.parse(get(videoNode, 'params_json')), {});
  resolution.value = '"480P"'; resolution.fire('change');
  duration.parentElement.parentElement.lastElementChild.fire('click');
  audio.checked = false; audio.fire('change');
  const videoSeed = findInput(video, ['seed']); videoSeed.value = '0'; videoSeed.fire('change');
  assert.deepEqual(JSON.parse(get(videoNode, 'params_json')), { resolution: '480P', duration: 6, generate_audio: false, seed: 0 });
  videoSeed.parentElement.children.find((element) => element.className === 'ea-shuffle').fire('click');
  const shuffled = JSON.parse(get(videoNode, 'params_json')).seed;
  assert.ok(Number.isInteger(shuffled) && shuffled > 0 && shuffled <= 2147483647);
  for (const input of [resolution, duration, audio, videoSeed]) {
    input.closest('.ea-form-field').children[0].lastElementChild.fire('click');
  }
  assert.deepEqual(JSON.parse(get(videoNode, 'params_json')), {});
  assert.equal(resolution.value, '"1080P"'); assert.equal(duration.value, '5'); assert.equal(audio.checked, true);
  video.commitParams({ duration: -1, resolution: 'saved-value', seed: -1 }); video.renderForm();
  assert.equal(findInput(video, ['duration']).type, 'number');
  assert.equal(findInput(video, ['duration']).value, -1);
  assert.equal(findInput(video, ['resolution']).children.filter((option) => !option.hidden).length, 3);
  assert.deepEqual(JSON.parse(get(videoNode, 'params_json')), { duration: -1, resolution: 'saved-value', seed: -1 });
  findInput(video, ['duration']).closest('.ea-form-field').children[0].lastElementChild.fire('click');
  assert.equal(findInput(video, ['duration']).type, 'range');
  assert.equal(findInput(video, ['duration']).value, '5');
  assert.equal(JSON.parse(get(videoNode, 'params_json')).duration, undefined);
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
  let heldModels, heldSchema, delayModels = false, delaySchema = false;
  const presetRequest = async (address) => {
    if (address.startsWith('/easyapi/presets?')) return { presets: savedPresets, default_preset_ids: defaultPresetMap };
    if (address.startsWith('/easyapi/models?')) {
      if (delayModels) { delayModels = false; return new Promise((resolve) => { heldModels = resolve; }); }
      return { models: fixtures.models };
    }
    if (address.startsWith('/easyapi/schema?')) {
      if (delaySchema) { delaySchema = false; return new Promise((resolve) => { heldSchema = resolve; }); }
      return { request_schema: fields, matched: true, declared: true };
    }
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
  body.append(presetEditor.root); presetEditor.setProfiles(fixtures.profiles, null);
  assert.equal(presetEditor.profile.children[0].disabled, true);
  presetEditor.setProfiles(fixtures.profiles, 'p2', 'p2'); await tick();
  await presetEditor.newFromNode(defaultPreset); presetEditor.name.value = 'Saved zeros'; presetEditor.defaultCheck.checked = true; await presetEditor.save();
  assert.equal(presetMutations[0].method, 'POST'); assert.equal(presetMutations[0].payload.params.seed, 0); assert.equal(presetMutations[0].payload.params.stream, false);
  assert.equal(presetMutations[0].payload.make_default, true); assert.equal(presetMutations[0].payload.prompt, undefined);
  await presetEditor.edit(savedPresets[0]); presetEditor.params.seed = 2; presetEditor.syncJSON(); await presetEditor.save();
  assert.equal(presetMutations[1].method, 'PUT'); assert.equal(presetMutations[1].payload.model, 'saved-model');
  await presetEditor.remove(); assert.equal(presetMutations[2].method, 'DELETE'); assert.ok(presetMutations[2].address.endsWith('?profile_id=p2'));
  delayModels = true;
  presetEditor.profile.value = 'p1'; presetEditor.profile.fire('change');
  assert.equal(presetEditor.picker.input.disabled, true);
  assert.equal(presetEditor.picker.items.length, 0);
  presetEditor.profile.value = 'p2'; presetEditor.profile.fire('change'); await tick();
  heldModels({ models: [{ id: 'p1-only' }] }); await tick();
  assert.equal(presetEditor.picker.items.some((model) => model.id === 'p1-only'), false);
  assert.equal(presetEditor.profile.children.some((option) => !option.value), false);
  delaySchema = true;
  const oldSchema = presetEditor.newFromNode(defaultPreset); await tick();
  await presetEditor.newFromNode({ ...defaultPreset, params: { seed: 1 } });
  heldSchema({ request_schema: { properties: { stale: { type: 'string' } } } }); await oldSchema;
  assert.equal(presetEditor.formSchema.properties.stale, undefined);
  assert.equal(presetEditor.params.seed, 1);
  presetEditor.destroy();

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
  const authorization = manager.form.lastElementChild;
  assert.equal(manager.key.parentElement.parentElement, manager.form);
  assert.ok(manager.form.children.indexOf(manager.key.parentElement) < manager.form.children.indexOf(manager.saveButton.parentElement));
  assert.equal(authorization.tagName, 'DETAILS');
  assert.ok(!authorization.open && authorization.contains(manager.authorizeButton));
  assert.equal(manager.authorizeButton.classList.contains('ea-primary'), false);
  assert.equal(manager.key.placeholder, '');
  await manager.refresh(); manager.select('manual');
  assert.equal(manager.key.value, ''); assert.equal(manager.selected().api_key, undefined);
  assert.equal(manager.key.placeholder, 'Leave blank to keep the saved key');
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
  await assert.rejects(manager.save(), /Enter an API key before saving/);
  manager.key.value = 'mock-only-key'; await manager.save(); assert.equal(manager.key.value, '');
  const savedManually = managerCalls.find((entry) => entry.address === '/easyapi/profiles/easyapi' && entry.options.method === 'POST');
  assert.equal(JSON.parse(savedManually.options.body).api_key, 'mock-only-key');
  window.isSecureContext = true;
  manager.newProfile(); manager.makeDefault.checked = true;
  assert.equal(manager.key.readOnly, false);
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
        result = subprocess.run([shutil.which("node"), "-", str(Path(__file__).parents[1] / "web")],
                                input=script, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
