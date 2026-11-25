"""Exercise catalogue updates against the shipped prompt editor's real DOM controls."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class CatalogConsumerFrontendTests(unittest.TestCase):
    def run_node(self, body):
        setup = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attrs={};this.value='';this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(name,value){this.attrs[name]=value;}
  addEventListener(type,fn){(this.events[type]??=[]).push(fn);}
  fire(type){return (this.events[type]||[]).map(fn=>fn({preventDefault(){},stopPropagation(){}}));}
}
const context={document:{createElement:tag=>new Element(tag)},AbortController};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8').replace('export class','class')+'\nglobalThis.Editor=PromptEditor;',context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
const make=async(deferInitial=false)=>{
 const state={profiles:[{id:'p',model_catalog_revision:'first'},{id:'other',model_catalog_revision:'second'}],rows:[{id:'chat'},{id:'other-chat'}]};
 const loads=[],requests=[];
 const editor=Object.create(context.Editor.prototype);
 Object.assign(editor,{t:key=>key,getLocale:()=> 'en',mode:'translate',current:{text:'scene'},revision:1,
  preferredModels:new Map([['p','chat']]),body:new Element('div'),syncButtons(){},
  getContext:()=>({profileId:'p',modelId:'video',profiles:state.profiles}),
  loadModels:async(id,signal,update,options)=>{
   const entry={id,signal,update,options};loads.push(entry);
   if(deferInitial&&loads.length===1)return new Promise(resolve=>{entry.resolve=resolve;});
   return state.rows;
  },
  transform:(payload,signal)=>new Promise((resolve,reject)=>requests.push({payload,signal,resolve,reject})),
 });
 editor.renderTransform('translate');await tick();
 const fields=descendants(editor.body);
 const selects=fields.filter(node=>node.tag==='select');
 const button=label=>fields.find(node=>node.tag==='button'&&node.textContent===label);
 return {editor,state,loads,requests,profile:selects[0],model:selects[1],language:selects[2],run:button('promptTranslate'),retry:button('retryModels')};
};
(async()=>{
'''
        script = setup + body + r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        source = Path(__file__).parents[1] / "web/prompt-editor.js"
        result = subprocess.run([shutil.which("node"), "-", str(source)], input=script, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalidation_during_transform_restores_controls_and_allows_retry(self):
        self.run_node(r'''
const ui=await make();
const pending=ui.run.fire('click')[0];
assert.equal(ui.profile.disabled,true);assert.equal(ui.language.disabled,true);
ui.loads[0].update({invalidated:true,updating:false,error:null});
assert.equal(ui.requests[0].signal.aborted,true);
assert.equal(ui.profile.disabled,false,'connection must be changeable after request cancellation');
assert.equal(ui.language.disabled,false,'language must be changeable after request cancellation');
assert.equal(ui.model.disabled,true);assert.equal(ui.run.disabled,true);
assert.equal(ui.retry.hidden,false,'connection changes without an HTTP error still provide recovery');
await ui.retry.fire('click')[0];
assert.equal(ui.loads.at(-1).options.refresh,true);
assert.equal(ui.profile.disabled,false);assert.equal(ui.language.disabled,false);
assert.equal(ui.model.disabled,false);assert.equal(ui.run.disabled,false);
ui.requests[0].resolve({text:'obsolete result'});await pending;
assert.equal(ui.editor.result.element.hidden,true,'a revoked request cannot publish a late result');
''')

    def test_removed_model_is_cleared_without_auto_selecting_replacement(self):
        self.run_node(r'''
const ui=await make();
const pending=ui.run.fire('click')[0];
ui.loads[0].update({models:[{id:'other-chat'}],value:{models:[{id:'other-chat'}]}});
assert.equal(ui.requests[0].signal.aborted,true);
assert.equal(ui.model.value,'','removing the selected model cannot silently choose another billed model');
assert.deepEqual(ui.model.children.map(node=>node.value),['','other-chat']);
assert.equal(ui.run.disabled,true);assert.equal(ui.model.disabled,false);
assert.equal(ui.profile.disabled,false);assert.equal(ui.language.disabled,false);
ui.model.value='other-chat';ui.model.fire('change');assert.equal(ui.run.disabled,false);
ui.requests[0].resolve({text:'obsolete result'});await pending;
assert.equal(ui.editor.result.element.hidden,true);
ui.loads[0].update({models:[],value:{models:[]}});
assert.equal(ui.model.value,'');assert.equal(ui.model.disabled,true);assert.equal(ui.run.disabled,true);
''')

    def test_stale_background_update_keeps_valid_transform_running(self):
        self.run_node(r'''
const ui=await make();
const pending=ui.run.fire('click')[0];
ui.loads[0].update({updating:true,value:{models:ui.state.rows},stale:true});
ui.loads[0].update({models:[{id:'chat',display_name:'Updated name'},{id:'other-chat'}],value:{models:[]},updating:false});
assert.equal(ui.requests[0].signal.aborted,false,'a label/catalogue refresh must not cancel a valid transformation');
assert.equal(ui.model.value,'chat');assert.equal(ui.run.disabled,true);
assert.equal(ui.model.disabled,true);assert.equal(ui.profile.disabled,true);assert.equal(ui.language.disabled,true);
ui.requests[0].resolve({text:'valid updated prompt'});await pending;
assert.equal(ui.editor.result.element.hidden,false);
assert.equal(ui.profile.disabled,false);assert.equal(ui.language.disabled,false);assert.equal(ui.run.disabled,false);
''')

    def test_revision_change_refreshes_scope_and_rejects_previous_transform(self):
        self.run_node(r'''
const ui=await make();
const pending=ui.run.fire('click')[0];
const firstLoader=ui.loads[0];
ui.state.profiles=[{id:'p',model_catalog_revision:'rotated'},{id:'other',model_catalog_revision:'second'}];
ui.editor.contextChanged();await tick();
assert.equal(firstLoader.signal.aborted,true);
assert.equal(ui.loads.length,2,'an open transform panel reloads candidates after credential rotation');
assert.equal(ui.loads[1].options.profiles[0].model_catalog_revision,'rotated');
assert.equal(ui.requests[0].signal.aborted,true,'changing the key must cancel a request started under its old permissions');
ui.requests[0].resolve({text:'result from previous key'});await pending;
assert.equal(ui.editor.result.element.hidden,true);
assert.equal(ui.profile.disabled,false);assert.equal(ui.language.disabled,false);
''')

    def test_temporary_background_failure_keeps_choices_and_retry_is_explicit(self):
        self.run_node(r'''
const ui=await make();
const failure=Object.assign(new Error('offline'),{status:503});
ui.loads[0].update({updating:false,value:{models:ui.state.rows},stale:true,error:failure});
assert.equal(ui.model.value,'chat');assert.equal(ui.model.disabled,false);assert.equal(ui.run.disabled,false);
assert.equal(ui.retry.hidden,false);
await ui.retry.fire('click')[0];
assert.equal(ui.loads.length,2);assert.equal(ui.loads[1].options.refresh,true);
assert.equal(ui.model.value,'chat');assert.equal(ui.retry.hidden,true);
''')

    def test_invalidation_cannot_be_undone_by_late_cached_loader_result(self):
        self.run_node(r'''
const ui=await make(true);
const initial=ui.loads[0];
initial.update({models:ui.state.rows,value:{models:ui.state.rows}});
assert.equal(ui.model.value,'chat');
initial.update({invalidated:true,error:Object.assign(new Error('revoked'),{status:403})});
initial.resolve(ui.state.rows);await tick();
assert.equal(ui.model.value,'','an already captured cache response cannot re-add models after permission invalidation');
assert.equal(ui.model.disabled,true);assert.equal(ui.run.disabled,true);
''')
