"""Exercise shared model choices through reloads, stale refresh and credential changes."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class ModelCatalogFrontendTests(unittest.TestCase):
    def run_node(self, body):
        setup = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const values = new Map();
const localStore = {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
const context = vm.createContext({URLSearchParams,AbortController});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8').replace(/^export /gm,'' )+'\nglobalThis.factory=createModelCatalog;',context);
const factory=context.factory;
let now=1000;
const options={now:()=>now,storage:()=>localStore};
const args={profileId:'profile',family:'video',operation:'video-generation',locale:'zh',revision:'first'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const result=id=>({models:[{id,display_name:id,capability_status:'declared',ignored:'private metadata',input_modalities:['text','image'],supports_image:true,supports_video:false}],cache_ttl:90});
(async()=>{
'''
        script = setup + body + r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        source = Path(__file__).parents[1] / "web/model-catalog.js"
        run = subprocess.run([shutil.which("node"), "-", str(source)], input=script, encoding="utf-8",
                             capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_fresh_reload_scope_and_safe_persistence(self):
        self.run_node(r'''
let calls=[];
const load=async path=>{calls.push(path);return result('video');};
const first=factory(load,options);
assert.equal(first.peek(args),null);
const responses=await Promise.all(Array.from({length:5},()=>first.get(args)));
assert.equal(calls.length,1);
assert.equal(responses[0].models[0].id,'video');
assert.equal(first.peek(args).stale,false);
assert.ok(values.size===1&&!Array.from(values.values())[0].includes('private metadata'));
const reopened=factory(load,options);
const stored=await reopened.get(args);
assert.equal(calls.length,1,'refreshing ComfyUI reuses the fresh permission snapshot');
assert.deepEqual(Array.from(stored.models[0].input_modalities),['text','image']);
assert.equal(stored.models[0].supports_image,true);
assert.equal(stored.models[0].supports_video,false);
await reopened.get({...args,locale:'en'});
await reopened.get({...args,family:'image',operation:'image-generation'});
await reopened.get({...args,purpose:'prompt'});
await reopened.get({...args,profileId:'other'});
assert.equal(calls.length,5,'locale, operation, purpose and profile are independent');
assert.ok(calls[3].startsWith('/easyapi/prompt/models?'));
const legacy={...args,profileId:'legacy',revision:''};
await reopened.get(legacy);
await factory(load,options).get(legacy);
assert.equal(calls.length,7,'without a backend credential identity there is no cross-tab reuse');
''')

    def test_stale_returns_immediately_and_shared_refresh_notifies_views(self):
        self.run_node(r'''
let calls=0,finish;
const cache=factory(()=>{calls++;return calls===1?result('old'):new Promise(resolve=>{finish=resolve;});},options);
await cache.get(args);
now+=90001;
const states=[];
const unsubscribe=cache.subscribe(args,state=>states.push(state));
assert.equal(states[0].stale,true);
const choices=await Promise.all([cache.get(args),cache.get(args)]);
assert.equal(choices[0].models[0].id,'old','expired choices return before the slow network');
assert.equal(calls,2,'all consumers share one background refresh');
assert.equal(states.at(-1).updating,true);
assert.equal(states.at(-1).value.models[0].id,'old');
finish(result('new'));
await tick();
assert.equal(states.at(-1).value.models[0].id,'new');
assert.equal(states.at(-1).updating,false);
assert.equal(states.at(-1).stale,false);
unsubscribe();
const count=states.length;
cache.invalidate(args.profileId);
assert.equal(states.length,count,'unmounted views are unsubscribed');
''')

    def test_stale_snapshot_survives_reload_but_expires_after_one_day(self):
        self.run_node(r'''
await factory(async()=>result('old'),options).get(args);
now+=120001;
let finish,calls=0;
const reopened=factory(()=>{calls++;return new Promise(resolve=>{finish=resolve;});},options);
assert.equal(reopened.peek(args).stale,true);
assert.equal((await reopened.get(args)).models[0].id,'old');
await tick();assert.equal(calls,1);
finish(result('updated'));await tick();
now+=86400001;
assert.equal(reopened.peek(args),null);
assert.equal(factory(async()=>result('fresh'),options).peek(args),null);
''')

    def test_network_failure_keeps_choices_and_authorization_revokes_all_purposes(self):
        self.run_node(r'''
let fail=null;
const cache=factory(async()=>{if(fail)throw fail;return result('allowed');},options);
const prompt={...args,purpose:'prompt'};
await cache.get(args);await cache.get(prompt);
const states=[],promptStates=[];
cache.subscribe(args,state=>states.push(state));cache.subscribe(prompt,state=>promptStates.push(state));
now+=90001;fail=Object.assign(new Error('temporarily offline'),{status:503});
assert.equal((await cache.get(args)).models[0].id,'allowed');
await tick();
assert.equal(states.at(-1).error.status,503);
assert.equal(states.at(-1).value.models[0].id,'allowed');
fail=Object.assign(new Error('permission revoked'),{status:403});
await assert.rejects(cache.get({...args,refresh:true}),{status:403});
assert.equal(cache.peek(args),null);assert.equal(cache.peek(prompt),null);
assert.equal(states.at(-1).invalidated,true);
assert.equal(states.at(-1).error.status,403);
assert.equal(promptStates.at(-1).invalidated,true);
assert.equal(promptStates.at(-1).value,undefined);
assert.equal(values.size,0,'revoked snapshots cannot return after a browser refresh');
fail=null;await cache.get(args);
fail=Object.assign(new Error('key revoked'),{status:401});
await assert.rejects(cache.get({...args,refresh:true}),{status:401});
assert.equal(cache.peek(args),null);assert.equal(values.size,0);
''')

    def test_force_refresh_waits_and_replaces_empty_catalog_without_clearing_snapshot(self):
        self.run_node(r'''
let finish,calls=0;
const cache=factory(path=>{calls++;return calls===1?result('removed'):new Promise(resolve=>{finish=resolve;});},options);
await cache.get(args);
const states=[];cache.subscribe(args,state=>states.push(state));
let completed=false;
const first=cache.get({...args,refresh:true}).then(value=>{completed=true;return value;});
const second=cache.get({...args,refresh:true});
await tick();
assert.equal(calls,2);assert.equal(completed,false);
assert.equal(cache.peek(args).value.models[0].id,'removed');
assert.equal(states.at(-1).updating,true);
finish({models:[],cache_ttl:300});
await Promise.all([first,second]);
assert.equal(cache.peek(args).value.models.length,0);
assert.equal(states.at(-1).value.models.length,0);
assert.equal(factory(async()=>result('bad'),options).peek(args).value.models.length,0,'an empty authoritative list replaces old choices');
''')

    def test_revision_changes_and_invalidation_block_old_requests(self):
        self.run_node(r'''
const deferred=[];
const cache=factory(()=>new Promise(resolve=>deferred.push(resolve)),options);
const states=[];cache.subscribe(args,state=>states.push(state));
const old=cache.get(args);await tick();
const rotated={...args,revision:'rotated'};
const current=cache.get(rotated);await tick();
assert.equal(states.at(-1).invalidated,true);
deferred[0](result('old-key'));
await assert.rejects(old,{name:'AbortError'});
assert.equal(cache.peek(args),null,'an old revision cannot roll the connection backwards');
deferred[1](result('new-key'));await current;
assert.equal(cache.peek(rotated).value.models[0].id,'new-key');
const refresh=cache.get({...rotated,refresh:true});await tick();
cache.invalidate(args.profileId);
deferred[2](result('late'));
await assert.rejects(refresh,{name:'AbortError'});
assert.equal(cache.peek(rotated),null);
assert.equal(factory(async()=>result('unexpected'),options).peek(rotated),null);
''')

    def test_one_aborted_viewer_cannot_cancel_shared_discovery(self):
        self.run_node(r'''
let finish,calls=0;
const cache=factory(()=>{calls++;return new Promise(resolve=>{finish=resolve;});},options);
const controller=new AbortController();
const dismissed=cache.get({...args,signal:controller.signal});
const remaining=cache.get(args);
controller.abort();await assert.rejects(dismissed,{name:'AbortError'});
finish(result('shared'));assert.equal((await remaining).models[0].id,'shared');
assert.equal(calls,1);
const aborted=new AbortController();aborted.abort();
await assert.rejects(cache.get({...args,signal:aborted.signal}),{name:'AbortError'});
''')

    def test_refresh_supersedes_older_background_and_storage_can_be_unavailable(self):
        self.run_node(r'''
let count=0;const deferred=[];
const cache=factory(()=>++count===1?result('old'):new Promise(resolve=>deferred.push(resolve)),options);
await cache.get(args);now+=90001;
await cache.get(args);await tick();
const forced=cache.get({...args,refresh:true});await tick();
deferred[1](result('fresh'));await forced;
deferred[0](result('obsolete'));await tick();
assert.equal(cache.peek(args).value.models[0].id,'fresh');
const unavailable=factory(async()=>result('memory'),{now:()=>now,storage:()=>{throw new Error('blocked');}});
await unavailable.get(args);
assert.equal(unavailable.peek(args).value.models[0].id,'memory');
''')
