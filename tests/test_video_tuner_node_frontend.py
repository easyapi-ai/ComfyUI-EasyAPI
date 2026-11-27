"""Run the dedicated tuner UI and partial prompt construction without paid calls."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class VideoTunerNodeFrontendTests(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script,
             str(Path(__file__).resolve().parents[1] / "web" / "video-tuner-node.js")],
            encoding="utf-8", capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_queue_keeps_upstream_links_and_never_mutates_downstream(self):
        self.run_node(r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {queueVideoTunerNode,ensureTunerScope}=await import(pathToFileURL(process.argv[1]));
const get=(node,key)=>node.widgets.find(item=>item.name===key);
const node={id:7,type:'EasyAPIVideoPromptTuner',widgets:[{name:'scope_id',value:'scope-a'},{name:'revision',value:0},{name:'prompt',value:'NEVER USE THIS LINKED DEFAULT'},{name:'issue',value:'NEVER USE THIS LINKED ISSUE DEFAULT'}],inputs:[{name:'prompt',link:4},{name:'issue',link:5}]};
const downstream={id:8,type:'EasyAPIVideo',widgets:[{name:'seed',value:123,callback(){throw new Error('Downstream seed callback ran');}}]};
node.graph={_nodes:[node,downstream]};
let appQueueCalls=0,beforeQueuedCalls=0,queued=[];
const graph={workflow:{nodes:[{id:7,type:node.type},{id:8,type:downstream.type}],links:[[4,10,0,7,0,'STRING']]},output:{
 '7':{class_type:node.type,inputs:{prompt:['10',0],issue:['13',0],output:'quick',references:['11',0],scope_id:'scope-a',revision:0}},
 '10':{class_type:'TextSource',inputs:{text:'Actual upstream prompt'}},
 '11':{class_type:'EasyAPIMedia',inputs:{image:['12',0]}},
 '12':{class_type:'LoadImage',inputs:{image:'local-fixture.png'}},
 '13':{class_type:'TextSource',inputs:{text:'The logo changes at 3 seconds.'}},
 '8':{class_type:'EasyAPIVideo',inputs:{prompt:['7',0],seed:123}},
 '9':{class_type:'SaveVideo',inputs:{video:['8',0]}},
}};
const pristine=JSON.stringify(graph);
const app={graphToPrompt:async()=>({...graph,output:{...graph.output,'7':{...graph.output['7'],inputs:{...graph.output['7'].inputs,revision:get(node,'revision').value}}}}),queuePrompt(){appQueueCalls++;},beforeQueued(){beforeQueuedCalls++;}};
const api={queuePrompt:async(...args)=>{queued.push(args);return {prompt_id:'mock-prompt-'+queued.length};}};
await queueVideoTunerNode(node,{app,api,createId:()=> 'preview-one'});
assert.equal(appQueueCalls,0);assert.equal(beforeQueuedCalls,0);
assert.equal(JSON.stringify(graph),pristine,'The serializer result and workflow must remain untouched');
assert.equal(get(downstream,'seed').value,123);
const [number,request,options]=queued[0];assert.equal(number,0);
assert.deepEqual(Object.keys(request.output).sort(),['10','11','12','13','7','__easyapi_tune_preview_preview-one'].sort());
assert.deepEqual(request.output['7'].inputs.prompt,['10',0],'Connected raw prompt must remain a Comfy link');
assert.deepEqual(request.output['7'].inputs.issue,['13',0],'The server resolves a connected BadCase description');
assert.equal(request.output['7'].inputs.output,'quick','The description enables diagnosis without rewriting the output preference');
assert.equal(request.output['8'],undefined);assert.equal(request.output['9'],undefined);
assert.deepEqual(options.partialExecutionTargets,['__easyapi_tune_preview_preview-one']);
assert.deepEqual(request.output[options.partialExecutionTargets[0]].inputs,{json_value:['7',2]});
assert.equal(request.output[options.partialExecutionTargets[0]].class_type,'EasyAPIPreview');
assert.ok(!request.workflow.nodes.some(item=>String(item.id).startsWith('__easyapi')),'The helper must not enter persisted workflow metadata');
assert.equal(get(node,'revision').value,0);
await queueVideoTunerNode(node,{app,api,retry:true,createId:()=> 'preview-two'});
assert.equal(get(node,'revision').value,1);assert.equal(queued[1][1].output['7'].inputs.revision,1);
assert.equal(get(downstream,'seed').value,123);assert.equal(JSON.stringify(graph),pristine);
const copy={id:17,type:node.type,widgets:[{name:'scope_id',value:'scope-a'},{name:'revision',value:0}],graph:node.graph};
node.graph._nodes.push(copy);assert.equal(ensureTunerScope(copy,()=> 'scope-copy'),'scope-copy');
assert.equal(ensureTunerScope(node,()=> 'must-not-change'),'scope-a');
assert.equal(get(copy,'scope_id').hidden,true);assert.equal(get(copy,'revision').hidden,true);
const broken={...app,graphToPrompt:async()=>({workflow:{nodes:[]},output:{'7':{class_type:node.type,inputs:{prompt:['missing',0]}}}})};
await assert.rejects(queueVideoTunerNode(node,{app:broken,api}),/missing upstream/);
assert.equal(queued.length,2,'A broken input must fail before queueing');
''')

    def test_specialized_panel_preserves_linked_prompt_and_restores_preview_only(self):
        self.run_node(r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.events={};this.attrs={};this.dataset={};this.style={setProperty(){}};this.value='';this.className='';this.hidden=false;this.disabled=false;this.scrollHeight=740;}
 append(...children){for(const child of children){if(typeof child==='object')child.parentElement=this;this.children.push(child);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(key,value){this.attrs[key]=String(value);}
 addEventListener(type,handler){(this.events[type]??=[]).push(handler);}
 fire(type){for(const handler of this.events[type]||[])handler({target:this});}
 remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(item=>item!==this);}
 querySelectorAll(){return [];} focus(){} select(){} setCustomValidity(){} reportValidity(){}
}
globalThis.Node=Element;
globalThis.document={createElement:tag=>new Element(tag),head:new Element('head'),body:new Element('body'),documentElement:{lang:'en'},activeElement:null};
const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
const {VideoTunerNodePanel,installVideoTunerNode}=await import(pathToFileURL(process.argv[1]));
const {createPromptModelPreferences}=await import(new URL('./prompt-model-preferences.js',pathToFileURL(process.argv[1])));
const fields={profile_id:'p1',text_model:'text-model',analysis_model:'',target_profile_id:'p2',target_model:'wan3.0-video',target_params_json:'{"duration":12,"generate_audio":false}',prompt:'DO NOT FALL BACK TO THIS DEFAULT',domain:'drama',mode:'auto',output:'quick',analyze:true,issue:'',issue_start:-1,issue_end:-1,locale:'en',scope_id:'scope-ui',revision:0};
const makeNode=(id=7,overrides={})=>{const node={id,type:'EasyAPIVideoPromptTuner',widgets:Object.entries({...fields,...overrides}).map(([name,value])=>({name,value,callback(){}})),inputs:[{name:'prompt',link:4},{name:'image',link:5}],size:[430,740],setDirtyCanvas(){}};node.graph={_nodes:[node],setDirtyCanvas(){},change(){}};return node;};
const get=(node,name)=>node.widgets.find(item=>item.name===name);
let queueCalls=[],details=[],results=[];const events=new Map();
const node=makeNode();
const services={t:key=>key,loadProfiles:async()=>({profiles:[{id:'p1',name:'Text'},{id:'p2',name:'Video'}],default_profile_id:'p1'}),
 loadPromptModels:async()=>[{id:'text-model',supports_image:true}],loadVideoModels:async()=>[{id:'wan3.0-video'}],
 app:{canvas:{selected_nodes:{}},graphToPrompt:async()=>({workflow:{nodes:[]},output:{'7':{class_type:node.type,inputs:{prompt:['10',0],profile_id:'p1',scope_id:'scope-ui'}},'10':{class_type:'TextSource',inputs:{text:'linked original'}}}})},
 api:{queuePrompt:async(...args)=>{queueCalls.push(args);return {prompt_id:'queued-'+queueCalls.length};},addEventListener:(type,fn)=>events.set(type,fn),removeEventListener:(type)=>events.delete(type)},
 openDetails:record=>details.push(record),onResult:record=>results.push(record)};
const tick=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
const panel=new VideoTunerNodePanel(node,services);await tick();
assert.equal(panel.prompt.hidden,true);assert.equal(panel.prompt.value,'');assert.equal(panel.promptHint.hidden,false);
assert.equal(panel.getContext().prompt,'');assert.equal(panel.getContext().promptLinked,true);
assert.equal(panel.getContext().profileId,'p2');assert.equal(panel.getContext().tuningProfileId,'p1');
assert.equal(panel.getContext().media[0].ready,false);
assert.equal(get(node,'analysis_model').value,'text-model','A visual text model is the default analysis model');
assert.equal(panel.issueFields.hidden,false,'BadCase description is available without selecting diagnostic output');
assert.equal(panel.issueRange.hidden,true,'Optional time range is hidden until diagnosis is requested');
for (const output of ['quick','full']) {
 panel.output.value=output;panel.output.fire('change');
 panel.issue.value='  The logo changes at 3 seconds.  ';panel.issue.fire('input');
 assert.equal(get(node,'output').value,output,'Automatic diagnosis preserves the selected output preference');
 assert.equal(get(node,'issue').value,panel.issue.value);
 assert.equal(panel.issueFields.hidden,false);assert.equal(panel.issueRange.hidden,false);
 assert.deepEqual(panel.getContext().issue,{description:'The logo changes at 3 seconds.'});
 panel.controls.issue_start.value='2';panel.controls.issue_start.fire('change');
 panel.controls.issue_end.value='4';panel.controls.issue_end.fire('change');
 assert.deepEqual(panel.getContext().issue,{description:'The logo changes at 3 seconds.',start:2,end:4});
 panel.issue.value='  ';panel.issue.fire('input');
 assert.equal(panel.issueRange.hidden,true,'Clearing the description returns to the selected ordinary task');
 assert.equal(panel.getContext().issue,null);
 assert.equal(get(node,'output').value,output);
 panel.setFormState({issue_start:-1,issue_end:-1});
}
panel.output.value='diagnose';panel.output.fire('change');assert.equal(panel.issueRange.hidden,false);
panel.output.value='quick';panel.output.fire('change');
panel.setFormState({issue:'Node BadCase',issue_start:2,issue_end:4});
node.inputs.push({name:'issue',link:21},{name:'issue_start',link:22});panel.sync();
assert.equal(panel.issue.disabled,true);assert.equal(panel.getContext().issue,null);
assert.equal(panel.getContext().issueLinked,true);assert.equal(panel.getContext().issueRangeLinked,true);
assert.equal(panel.issueRange.hidden,false,'Linked diagnostic input can still use optional local time bounds');
node.inputs=node.inputs.filter(input=>!['issue','issue_start'].includes(input.name));
panel.setFormState({issue:'',issue_start:-1,issue_end:-1});
assert.equal(panel.getContext().issueLinked,false);assert.equal(panel.getContext().issueRangeLinked,false);
panel.setFormState({prompt:'must not replace linked input',domain:'ecommerce'});
assert.equal(get(node,'prompt').value,fields.prompt);assert.equal(get(node,'domain').value,'ecommerce');
assert.equal(panel.getFormState().prompt,null);
panel.paramsEditor.value='{"duration":';
await assert.rejects(panel.run(),/JSON/);assert.equal(queueCalls.length,0);assert.equal(panel.queued,undefined);
panel.paramsEditor.value=fields.target_params_json;
await panel.run();assert.equal(queueCalls.length,1);assert.equal(panel.queued,true);
assert.deepEqual(queueCalls[0][1].output['7'].inputs.prompt,['10',0]);
panel.update({easyapi_tuning:[{prompt:'Optimized output',analysis:'Read-only analysis',result:{prompt:'Optimized output',suggestedParams:{duration:8}}}]});
assert.equal(panel.resultPrompt.value,'Optimized output');assert.equal(panel.result.hidden,false);assert.equal(panel.queued,false);
assert.equal(get(node,'prompt').value,fields.prompt,'Executed output must never replace the original prompt widget');
assert.equal(results.length,1);assert.equal(results[0].result.prompt,'Optimized output');assert.equal(storage.size,1);
panel.detailsButton.fire('click');await tick();assert.equal(details.length,1);assert.equal(details[0].context.promptLinked,true);
const restoredNode=makeNode(17,{analysis_model:'text-model',domain:'ecommerce'});const restored=new VideoTunerNodePanel(restoredNode,services);await tick();
assert.equal(restored.resultPrompt.value,'Optimized output');assert.equal(queueCalls.length,1,'Restoring a draft is not a queue action');
assert.equal(get(restoredNode,'prompt').value,fields.prompt);assert.ok(restored.status.textContent.includes('Restored preview'));
const video={id:40,type:'EasyAPIVideo',widgets:[{name:'profile_id',value:'p2'},{name:'model',value:'wan3.0-video'},{name:'params_json',value:'{"duration":15,"seed":0}'}],inputs:[]};
services.app.canvas.selected_nodes={40:video};panel.copySelectedVideo();await tick();
assert.equal(panel.params().duration,15);assert.equal(panel.params().seed,0);
video.inputs=[{name:'params_json',link:99}];
const originalTarget=get(node,'target_params_json').value;
assert.throws(()=>panel.copySelectedVideo(),/come from links/);assert.equal(get(node,'target_params_json').value,originalTarget);
let releaseLate,notifyLate,lastRows;
const late=panel.loadCatalog('late','p1',async(id,signal,notify)=>{
 notifyLate=notify;return new Promise(resolve=>{releaseLate=resolve;});
},rows=>{lastRows=rows;},false);
await tick();notifyLate({value:{models:[{id:'stale'}]},models:[{id:'stale'}]});
notifyLate({invalidated:true,error:new Error('revoked')});
releaseLate([{id:'stale'}]);await late;
assert.deepEqual(lastRows,[],'A late cached result cannot restore permission-revoked candidates');
panel.dispose();restored.dispose();

// Copy/paste lifecycle must allocate a new scope after the clone joins the graph.
class FakeNode {constructor(){Object.assign(this,makeNode(25,{scope_id:''}));this.graph._nodes=[this];}addDOMWidget(){}}
installVideoTunerNode(FakeNode,services);
const installed=new FakeNode();installed.onNodeCreated();await tick();
const originalScope=get(installed,'scope_id').value;
const clone=new FakeNode();clone.onNodeCreated();get(clone,'scope_id').value=originalScope;
clone.graph=installed.graph;installed.graph._nodes=[installed,clone];clone.onConfigure();clone.onAdded();await tick();
assert.notEqual(get(clone,'scope_id').value,originalScope);assert.equal(get(installed,'scope_id').value,originalScope);
installed.onRemoved();clone.onRemoved();

// A saved connection/key refresh cancels old subscriptions and cannot revive stale candidates.
const windowEvents=new Map();globalThis.window={addEventListener:(type,handler)=>(windowEvents.get(type)??(windowEvents.set(type,new Set()),windowEvents.get(type))).add(handler),removeEventListener:(type,handler)=>windowEvents.get(type)?.delete(handler)};
let releaseOld,oldSignal,catalogCalls=0,disposedCalls=0;
const refreshedNode=makeNode(30,{scope_id:'scope-refresh'});
const refreshed=new VideoTunerNodePanel(refreshedNode,{...services,
 loadPromptModels:async(profile,signal)=>{catalogCalls++;if(catalogCalls===1){oldSignal=signal;return new Promise(resolve=>{releaseOld=resolve;});}return [{id:'new-permission-model'}];},
 onDispose:()=>disposedCalls++});
await tick();assert.ok(releaseOld);
for(const handler of windowEvents.get('easyapi:profiles'))handler({});
await tick();assert.equal(oldSignal.aborted,true);
releaseOld([{id:'old-permission-model'}]);await tick();
assert.deepEqual(refreshed.promptModels.map(model=>model.id),['new-permission-model']);
const beforeDispose=catalogCalls;refreshed.dispose();refreshed.dispose();
assert.equal(disposedCalls,1);assert.equal(windowEvents.get('easyapi:profiles').size,0);assert.equal(windowEvents.get('easyapi:locale').size,0);
assert.equal(catalogCalls,beforeDispose);

const preferences=createPromptModelPreferences();
preferences.setFixed('p1','tune','fixed');
preferences.setFixed('p1','analysis','vision');
const preferenceServices={...services,promptModelPreferences:preferences,
 loadPromptModels:async()=>[{id:'fixed'},{id:'alternate'},{id:'vision',supports_image:true}]};
const freshNode=makeNode(35,{scope_id:'fresh',text_model:'',analysis_model:''});
const freshPanel=new VideoTunerNodePanel(freshNode,preferenceServices);await tick();
assert.equal(get(freshNode,'text_model').value,'fixed');
assert.equal(get(freshNode,'analysis_model').value,'vision');
freshPanel.chooseModel('text_model','alternate');freshPanel.chooseModel('target_model','other-video');
assert.equal(preferences.get('p1','tune').recent,'alternate');
assert.equal(preferences.get('p2','tune').recent,undefined,'generation model is not a tuning preference');
await freshPanel.refreshModels(true);
assert.equal(get(freshNode,'text_model').value,'alternate','global defaults seed blank fields only');
const savedNode=makeNode(36,{scope_id:'existing',text_model:'stored-model',analysis_model:''});
const savedPanel=new VideoTunerNodePanel(savedNode,preferenceServices);
savedPanel.configure();await tick();
assert.equal(get(savedNode,'text_model').value,'stored-model');
assert.equal(get(savedNode,'analysis_model').value,'','loading a workflow never adds the global analysis model');
const linkedNode=makeNode(37,{scope_id:'linked',text_model:'',analysis_model:''});
linkedNode.inputs.push({name:'text_model',link:10},{name:'analysis_model',link:11});
const linkedPanel=new VideoTunerNodePanel(linkedNode,preferenceServices);await tick();
assert.equal(get(linkedNode,'text_model').value,'');assert.equal(get(linkedNode,'analysis_model').value,'');
freshPanel.dispose();savedPanel.dispose();linkedPanel.dispose();
''')


if __name__ == "__main__":
    unittest.main()
