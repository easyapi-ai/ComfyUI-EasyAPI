"""Exercise prompt history and the explicit preview/apply editing path."""

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class PromptEditorTests(unittest.TestCase):
    def test_prompt_edits_and_transform_preview(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.events = {};
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.selectionStart = this.selectionEnd = 0;
    this.offsetWidth = 320;
    this.offsetHeight = 320;
  }
  append(...children) {
    for (const child of children) {
      if (child.parent) child.parent.children = child.parent.children.filter(node => node !== child);
      child.parent = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.append(...children);
  }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(type, listener) { (this.events[type] ??= []).push(listener); }
  fire(type, event = {}) {
    for (const listener of this.events[type] || []) listener({ preventDefault() {}, stopPropagation() {}, ...event });
  }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  focus() { document.activeElement = this; }
  getBoundingClientRect() { return { top: 100, bottom: 180, right: 450 }; }
}

function descendants(element) {
  return element.children.flatMap(child => [child, ...descendants(child)]);
}

const storage = new Map();
const localStorage = {
  getItem: key => storage.get(key),
  setItem: (key, value) => storage.set(key, value),
  removeItem: key => storage.delete(key),
};
const document = { createElement: tag => new Element(tag), body: new Element('body'), activeElement: null,
  addEventListener() {}, removeEventListener() {} };
const window = { innerWidth: 900, innerHeight: 800, addEventListener() {}, removeEventListener() {} };
const context = { document, window, setTimeout, clearTimeout, localStorage, AbortController };
const source = fs.readFileSync(process.argv[1], 'utf8').replace('export class', 'class');
const preferencesSource = fs.readFileSync(process.argv[2], 'utf8').replace('export function', 'function');
vm.runInNewContext(preferencesSource + source + '\nObject.assign(globalThis, { PromptEditor, createPromptModelPreferences });', context);
const { PromptEditor, createPromptModelPreferences } = context;
const tick = () => new Promise(resolve => setImmediate(resolve));
const waitForCollapse = () => new Promise(resolve => setTimeout(resolve, 280));

(async () => {
  const revisions = [], requested = [];
  let finishRequest;
  const make = (historyKey = 'node-1', options = {}) => new PromptEditor({
    value: 'scene', t: key => key, getLocale: () => 'en', historyKey: () => historyKey,
    onInput: text => revisions.push(text),
    getContext: () => ({ profiles: [{ id: 'p1', name: 'Default' }], profileId: 'p1', modelId: 'video-only' }),
    loadModels: async () => [{ id: 'text-small' }, { id: 'text-large' }],
    transform: (payload, signal) => {
      requested.push({ payload, signal });
      return new Promise(resolve => { finishRequest = resolve; });
    },
    ...options,
  });
  const editor = make();
  document.body.append(editor.root);
  const input = editor.textarea;
  assert.equal(editor.promptLabel.htmlFor, input.id);
  assert.equal(editor.promptLabel.textContent, 'prompt');
  const expanded = () => editor.launcher.attrs['aria-expanded'];
  assert.equal(expanded(), 'false');
  assert.equal(editor.toolActions.inert, true);
  assert.equal(editor.buttons.tags.tabIndex, -1);
  input.fire('pointerenter', { pointerType: 'mouse' });
  assert.equal(expanded(), 'false');
  editor.toolbar.fire('pointerenter', { pointerType: 'mouse' });
  assert.equal(expanded(), 'true');
  assert.equal(editor.buttons.tags.tabIndex, 0);
  editor.toolbar.fire('pointerleave');
  editor.toolbar.fire('pointerenter', { pointerType: 'mouse' });
  await waitForCollapse();
  assert.equal(expanded(), 'true');
  editor.toolbar.fire('pointerleave');
  await waitForCollapse();
  assert.equal(expanded(), 'false');
  editor.toolbar.fire('pointerenter', { pointerType: 'touch' });
  assert.equal(expanded(), 'false');
  editor.launcher.fire('click');
  await waitForCollapse();
  assert.equal(expanded(), 'true');
  editor.dismissOutside({ target: document.body });
  await waitForCollapse();
  assert.equal(expanded(), 'false');
  editor.launcher.focus();
  editor.toolbar.fire('focusin');
  assert.equal(expanded(), 'true');
  editor.toolbar.fire('keydown', { key: 'Escape' });
  assert.equal(expanded(), 'false');
  document.activeElement = null;

  const type = (text, inputType = 'insertText', isComposing = false) => {
    input.value = text;
    input.setSelectionRange(text.length, text.length);
    input.fire('input', { inputType, isComposing });
  };
  type('scene a');
  type('scene at dawn');
  input.fire('keydown', { key: 'z', ctrlKey: true });
  assert.equal(input.value, 'scene');
  input.fire('keydown', { key: 'Z', ctrlKey: true, shiftKey: true });
  assert.equal(input.value, 'scene at dawn');

  type('scene at dawn 光', 'insertCompositionText', true);
  type('scene at dawn 光影', 'insertCompositionText', false);
  editor.buttons.undo.fire('click');
  assert.equal(input.value, 'scene at dawn');

  editor.open('tags');
  editor.toolbar.fire('pointerleave');
  await waitForCollapse();
  assert.equal(expanded(), 'true');
  editor.tagSearch.value = '远景';
  editor.tagSearch.fire('input');
  const tag = descendants(editor.body).find(node => node.className === 'ea-prompt-tag');
  assert.equal(tag.textContent, 'wide shot');
  input.setSelectionRange(input.value.length, input.value.length);
  tag.fire('click');
  assert.equal(input.value, 'scene at dawn, wide shot');
  editor.close();

  assert.ok([...storage.values()][0].includes('wide shot'));
  const restored = make();
  restored.open('history');
  const entry = descendants(restored.body).find(node => node.className === 'ea-prompt-history-entry');
  assert.equal(entry.textContent, 'scene at dawn, wide shot');
  entry.fire('click');
  assert.equal(restored.textarea.value, 'scene at dawn, wide shot');
  const isolated = make('node-2');
  isolated.open('history');
  assert.equal(descendants(isolated.body).some(node => node.className === 'ea-prompt-history-entry'), false);
  isolated.dispose();
  restored.dispose();

  editor.open('translate');
  await tick();
  const [model, language] = descendants(editor.body).filter(node => node.tag === 'select');
  assert.equal(model.attrs['aria-label'],'promptTransformModel');
  assert.equal(language.attrs['aria-label'],'promptTranslationLanguage');
  const run = descendants(editor.body).find(node => node.className === 'ea-primary');
  assert.equal(requested.length, 0);
  assert.equal(run.disabled, true);
  assert.equal(model.value, '');
  model.value = 'text-large';
  model.fire('change');
  language.value = 'zh';
  language.fire('change');
  run.fire('click');
  assert.deepEqual(JSON.parse(JSON.stringify(requested[0].payload)), {
    action: 'translate', prompt: 'scene at dawn, wide shot', profile_id: 'p1', model: 'text-large', target_language: 'zh',
  });
  editor.setValue('fresh edit');
  finishRequest({ text: 'ignored result' });
  await tick();
  assert.equal(editor.result.element.hidden, true);
  assert.equal(editor.result.status.textContent, 'promptChanged');
  assert.equal(input.value, 'fresh edit');
  run.fire('click');
  finishRequest({ text: '晨光中的景色' });
  await tick();
  assert.equal(input.value, 'fresh edit');
  assert.equal(editor.result.element.hidden, false);
  const apply = descendants(editor.result.element).find(node => node.tag === 'button');
  apply.fire('click');
  assert.equal(revisions.at(-1), '晨光中的景色');
  editor.buttons.undo.fire('click');
  assert.equal(input.value, 'fresh edit');

  editor.open('optimize');
  await tick();
  descendants(editor.body).find(node => node.className === 'ea-primary').fire('click');
  assert.equal(requested[2].payload.action, 'optimize');
  assert.equal(Object.hasOwn(requested[2].payload, 'target_language'), false);
  editor.setLinked(true);
  assert.equal(input.disabled, true);
  assert.equal(requested[2].signal.aborted, true);
  finishRequest({ text: 'aborted result' });
  await tick();
  assert.equal(input.value, 'fresh edit');
  editor.dispose();

  let updatePrimary, updateOther;
  let primaryModels = [{ id: 'text-small' }, { id: 'text-alternative' }];
  const settings = createPromptModelPreferences();
  assert.equal(settings.setFixed('p1', 'optimize', 'text-small'), true);
  const makeOptimizer = () => make('shared-preference', {
    t: (key, values) => values?.model ? `${key}: ${values.model}` : key,
    getContext: () => ({ profiles: [{ id: 'p1' }, { id: 'p2' }], profileId: 'p1', modelId: 'video-only' }),
    loadModels: async (profileId, _signal, update) => {
      if (profileId === 'p1') { updatePrimary = update; return primaryModels; }
      updateOther = update;
      return [{ id: 'text-large' }, { id: 'text-other' }];
    },
    promptModelPreferences: createPromptModelPreferences(),
  });
  const optimizerControls = instance => {
    const children = descendants(instance.body);
    return {
      profile: children.find(node => node.attrs['aria-label'] === 'connection'),
      model: children.find(node => node.attrs['aria-label'] === 'promptTransformModel'),
      run: children.find(node => node.className === 'ea-primary'),
      children,
    };
  };
  const firstOptimizer = makeOptimizer();
  firstOptimizer.open('optimize'); await tick();
  const firstControls = optimizerControls(firstOptimizer);
  assert.equal(firstControls.model.value, 'text-small');
  firstControls.run.fire('click');
  assert.equal(requested.at(-1).payload.model, 'text-small');
  assert.equal(settings.get('p1', 'optimize').recent, undefined);
  finishRequest({ text: 'configured model result' }); await tick();
  firstControls.model.value = 'text-alternative'; firstControls.model.fire('change');
  updatePrimary({ models: primaryModels, error: new Error('refresh failed') });
  const retry = firstControls.children.find(node => node.textContent === 'retryModels');
  assert.equal(retry.hidden, false);
  retry.fire('click'); await tick();
  assert.equal(firstControls.model.value, 'text-alternative');
  firstControls.profile.value = 'p2'; firstControls.profile.fire('change'); await tick();
  assert.equal(firstControls.model.value, '');
  firstControls.model.value = 'text-large'; firstControls.model.fire('change');
  assert.equal(settings.get('p2', 'optimize').recent, 'text-large');
  firstControls.run.fire('click');
  assert.equal(requested.at(-1).payload.model, 'text-large');
  updateOther({ models: [{ id: 'text-other' }] });
  assert.equal(requested.at(-1).signal.aborted, true);
  assert.equal(firstControls.model.value, '');
  assert.equal(firstControls.run.disabled, true);
  updateOther({ models: [{ id: 'text-large' }, { id: 'text-other' }] });
  assert.equal(firstControls.model.value, '');
  assert.equal(firstControls.run.disabled, true);
  firstControls.model.value = 'text-large'; firstControls.model.fire('change');
  assert.equal(firstControls.run.disabled, false);
  finishRequest({ text: 'discarded result' }); await tick();
  firstOptimizer.dispose();

  assert.equal(settings.setFixed('p1', 'optimize', 'retired-model'), true);
  primaryModels = [{ id: 'text-small' }];
  const nextOptimizer = makeOptimizer();
  nextOptimizer.open('optimize'); await tick();
  const nextControls = optimizerControls(nextOptimizer);
  assert.equal(nextControls.model.value, '');
  assert.equal(nextControls.run.disabled, true);
  assert.equal(nextControls.children.some(node => !node.hidden && node.textContent === 'promptModelUnavailable: retired-model'), true);
  updatePrimary({ models: [{ id: 'text-small' }, { id: 'retired-model' }] });
  assert.equal(nextControls.model.value, 'retired-model');
  assert.equal(nextControls.run.disabled, false);
  nextControls.profile.value = 'p2'; nextControls.profile.fire('change'); await tick();
  assert.equal(nextControls.model.value, 'text-large');
  assert.equal(nextControls.run.disabled, false);
  nextOptimizer.dispose();

  const params = { duration: 15, resolution: '480P' };
  let tuner;
  class Tuner {
    constructor(options) { this.options=options;this.root=new Element('div');tuner=this; }
    dispose() { this.disposed=true; }
    invalidate() { this.invalidated=true; }
  }
  const wan = new PromptEditor({ value:'Original',t:key=>key,onInput(){},getLocale:()=> 'en',
    getContext:()=>({profileId:'p1',modelId:'wan3.0-video',targetModel:'wan3.0-video',params}),
    loadModels:async()=>[],transform:async()=>({text:'unused'}),
    loadWanPanel:async()=>({WanTunerPanel:Tuner}) });
  document.body.append(wan.root);
  wan.open('wan');await tick();
  assert.equal(wan.popup.style.left,'290px');
  assert.equal(wan.popup.style.top,'240px');
  assert.equal(wan.popup.style.height,'auto');
  assert.equal(wan.popup.style.maxHeight,'760px');
  assert.equal(wan.popup.style.bottom,'auto');
  window.innerWidth=390;window.innerHeight=600;wan.position();
  assert.equal(wan.popup.style.height,'auto');
  assert.equal(wan.popup.style.maxHeight,'584px');
  tuner.options.onApply('Tuned prompt');
  assert.equal(wan.textarea.value,'Tuned prompt');
  assert.deepEqual(params,{duration:15,resolution:'480P'});
  assert.equal(tuner.disposed,true);
  wan.open('optimize');await tick();
  assert.equal(wan.popup.style.height,'auto');
  wan.dispose();
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        web = Path(__file__).resolve().parents[1] / "web"
        result = subprocess.run(
            [shutil.which("node"), "-e", script, str(web / "prompt-editor.js"), str(web / "prompt-model-preferences.js")],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
