"""Exercise tab reloads and node concurrency with the shipped model catalogue cache."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class ModelCatalogFrontendTests(unittest.TestCase):
    def test_cache_reuse_expiry_cancellation_and_scope(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const values = new Map();
const localStore = {
  getItem: key => values.get(key) || null,
  setItem: (key, value) => values.set(key, value),
  removeItem: key => values.delete(key),
};
const context = vm.createContext({ URLSearchParams, AbortController });
const source = fs.readFileSync(process.argv[2], 'utf8').replace(/^export /gm, '');
vm.runInContext(source + '\nglobalThis.factory = createModelCatalog;', context);
const factory = context.factory;
let calls = [];
let now = 1000;
let model = 'video';
const options = { now: () => now, storage: () => localStore };
const args = { profileId: 'profile', family: 'video', operation: 'video-generation', locale: 'zh', revision: 'first' };
const load = async address => {
  calls.push(new URL(address, 'http://test').searchParams);
  return { models: [{ id: model, display_name: 'Video', capability_status: 'declared', ignored: 'private metadata' }], cache_ttl: 90 };
};
(async () => {
  const first = factory(load, options);
  const responses = await Promise.all(Array.from({ length: 5 }, () => first.get(args)));
  assert.equal(calls.length, 1);
  assert.equal(responses[0].models[0].id, 'video');
  assert.ok(values.size === 1 && ![...values.values()][0].includes('private metadata'));
  const reopened = factory(load, options);
  assert.equal((await reopened.get(args)).models[0].display_name, 'Video');
  assert.equal(calls.length, 1, 'A new browser tab can reuse a fresh permission snapshot');
  await reopened.get({ ...args, locale: 'en' });
  await reopened.get({ ...args, operation: 'image-generation', family: 'image' });
  await reopened.get({ ...args, revision: 'rotated' });
  assert.equal(calls.length, 4, 'Locale, family/operation and credentials have independent keys');
  now += 90001;
  await reopened.get(args);
  assert.equal(calls.length, 5, 'The upstream remaining TTL caps browser expiry');
  model = 'new';
  await Promise.all([reopened.get({ ...args, refresh: true }), reopened.get({ ...args, refresh: true })]);
  assert.equal(calls.filter(query => query.get('refresh') === '1').length, 1);
  assert.equal((await factory(load, options).get(args)).models[0].id, 'new');
  reopened.invalidate(args.profileId);
  const invalidated = factory(load, options);
  await invalidated.get(args);
  assert.equal(calls.length, 7);
  const legacy = { ...args, revision: '' };
  await invalidated.get(legacy);
  await factory(load, options).get(legacy);
  assert.equal(calls.length, 9, 'A legacy backend has no identity for cross-tab reuse');

  let finish;
  let pendingCount = 0;
  const slow = factory(() => {
    pendingCount++;
    return new Promise(resolve => { finish = resolve; });
  }, options);
  const controller = new AbortController();
  const distinct = { ...args, family: 'audio', operation: 'realtime' };
  const firstWaiter = slow.get({ ...distinct, signal: controller.signal });
  const secondWaiter = slow.get(distinct);
  controller.abort();
  await assert.rejects(firstWaiter, { name: 'AbortError' });
  finish({ models: [{ id: 'realtime' }] });
  assert.equal((await secondWaiter).models[0].id, 'realtime');
  assert.equal(pendingCount, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        source = Path(__file__).parents[1] / "web/model-catalog.js"
        result = subprocess.run([shutil.which("node"), "-", str(source)], input=script, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
