"""Image size metadata, constraints and the actual form event handlers."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class ImageSizeFrontendTests(unittest.TestCase):
    def test_image_size_validation_and_controls(self):
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag = '') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {}; this.style = { setProperty() {} }; this.className = ''; this.value = ''; this.hidden = false; this._text = ''; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.children = []; this._text = String(value); }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  fire(name) { for (const handler of this.listeners[name] || []) handler({target: this, preventDefault() {}}); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  setCustomValidity(value) { this.validationMessage = value; }
  reportValidity() {} focus() {}
  querySelector(selector) { return all(this).find(child => selector[0] === '.' && child.className.split(' ').includes(selector.slice(1))); }
}
const all = element => element.children.flatMap(child => [child, ...all(child)]);
const context = {document: {createElement: tag => new Element(tag), createTextNode: text => {const node = new Element(); node.textContent = text; return node;}}, Node: Element};
vm.createContext(context);
for (const name of ['ui-state.js', 'ui-forms.js']) {
  const source = fs.readFileSync('web/' + name, 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
  vm.runInContext(source + (name === 'ui-forms.js' ? '\nglobalThis.hooks = {validateImageSize, renderSchemaForm};' : ''), context);
}
const { validateImageSize: validate, renderSchemaForm: render } = context.hooks;
const definition = { type: 'string', default: 'auto', enum: ['auto', '1024*1024', 'Square'], 'x-size-spec': { allow_custom: true, min_width: 256, max_width: 2048, min_height: 256, max_height: 2048, multiple_of: 16, min_pixels: 262144, max_pixels: 2097152, min_ratio: .5, max_ratio: 2 } };
const plain = value => JSON.parse(JSON.stringify(value));
const valid = (value, expected, spec = definition) => assert.deepEqual(plain(validate(spec, value)), {ok: true, value: expected, reason: ''});
const invalid = (value, reason, spec = definition) => {const result = validate(spec, value); assert.equal(result.ok, false, value); assert.equal(result.reason, reason, value);};
valid(' 1024 X 1024 ', '1024x1024'); valid('1024×1024', '1024x1024'); valid('1024*1024', '1024x1024'); valid('AUTO', 'auto'); valid('square', 'Square');
valid('1024x512', '1024x512'); valid('512x1024', '512x1024'); valid('2048x1024', '2048x1024');
invalid('0x512', 'invalid_size'); invalid('1.5x512', 'invalid_size'); invalid(1024, 'invalid_size'); invalid('1e3x512', 'invalid_size'); invalid('9007199254740992x512', 'invalid_size');
invalid('9007199254740991x9007199254740991', 'invalid_size', { 'x-size-spec': {allow_custom: true} });
invalid('240x1024', 'min_width'); invalid('2064x512', 'max_width'); invalid('1024x240', 'min_height'); invalid('512x2064', 'max_height'); invalid('513x1024', 'multiple_of');
invalid('256x256', 'min_pixels'); invalid('2048x2048', 'max_pixels'); invalid('256x1536', 'min_ratio'); invalid('1536x256', 'max_ratio');
valid('64x64', '64x64', {enum: ['64*64'], 'x-size-spec': {allow_custom: true, min_width: 512}});
invalid('auto', 'auto_not_allowed', {'x-size-spec': {allow_custom: true}});
valid('auto', 'auto', {default: 'auto'}); invalid('512x512', 'custom_not_allowed', {default: 'auto'});
const messages = {sizeCustom: 'Custom', sizeWidth: 'Width', sizeHeight: 'Height', optionalValue: 'Default', useDefault: 'Reset'};
const t = (key, options) => messages[key] || (typeof options === 'string' ? options : key);
let params = {}, commits = 0;
const container = new Element('div');
render(container, {properties: {size: definition, output_format: {type: 'string', enum: ['png','webp']}, output_compression: {type: 'integer', minimum: 0, maximum: 100}, vendor_flag: {type: 'boolean'}}}, () => params, next => {params = next; commits++;}, t);
const find = className => all(container).find(element => element.className.split(' ').includes(className));
const preset = find('ea-size-preset'), width = find('ea-size-width'), height = find('ea-size-height');
assert.ok(preset && width && height, 'Size metadata provides presets and width/height inputs');
assert.equal(commits, 0, 'Displaying optional defaults does not send them');
assert.equal(preset.value, '');
assert.ok(find('ea-fields').children.some(field => field.textContent.includes('output_format')), 'Image output format is a common field');
assert.ok(find('ea-more').textContent.includes('vendor_flag'), 'Other model parameters stay under More');
preset.value = '__custom'; preset.fire('change'); assert.equal(commits, 0);
width.value = '1024'; width.fire('change'); assert.equal(commits, 0, 'Incomplete dimensions stay local');
height.value = '512'; height.fire('change'); assert.equal(params.size, '1024x512');
width.value = '513'; width.fire('change'); assert.equal(params.size, '1024x512'); assert.ok(width.validationMessage); assert.equal(find('ea-size-error').hidden, false);
width.value = '512'; width.fire('change'); assert.equal(params.size, '512x512'); assert.equal(find('ea-size-error').hidden, true);
preset.value = '1024x1024'; preset.fire('change'); assert.equal(params.size, '1024x1024');
const reset = container.children[0].children[0].children[0].children.at(-1); reset.fire('click');
assert.deepEqual(plain(params), {}); assert.equal(preset.value, ''); assert.equal(find('ea-size-error').hidden, true);
params = {size: 'legacy-invalid'}; const before = commits;
render(container, {properties: {size: definition}}, () => params, next => {params = next; commits++;}, t);
const raw = find('ea-size-raw'); assert.ok(raw && !raw.hidden); assert.equal(raw.value, 'legacy-invalid'); assert.equal(commits, before);
raw.value = '512 X 1024'; raw.fire('change'); assert.equal(params.size, '512x1024'); assert.equal(raw.hidden, true);
const ordinary = new Element('div');
render(ordinary, {properties: {size: {type: 'string', enum: ['512x512']}}}, () => ({}), () => {throw new Error('display must not commit');}, t);
assert.equal(all(ordinary).find(element => element.className === 'ea-size-preset'), undefined, 'Custom sizes require explicit allow_custom');
const otherFields = new Element('div');
render(otherFields, {properties: {output_size: definition, settings: {type: 'object', properties: {size: definition}}}}, () => ({}), () => {throw new Error('display must not commit');}, t);
assert.equal(all(otherFields).filter(element => element.className === 'ea-size-preset').length, 0, 'Extended size controls only apply to top-level size');
assert.equal(all(otherFields).filter(element => element.tagName === 'SELECT').length, 2, 'Other size-shaped fields keep ordinary schema controls');
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=Path(__file__).parents[1], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
