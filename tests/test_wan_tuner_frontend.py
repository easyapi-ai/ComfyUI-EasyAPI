"""Exercise explicit adoption, source grounding and stale Wan tuning results."""

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class WanTunerFrontendTests(unittest.TestCase):
    def run_node(self, script):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script,
             str(root / "web" / "wan-tuner-panel.js"), str(root / "web" / "easyapi.js"),
             str(root / "web" / "wan-tuner-knowledge.json")],
            capture_output=True, text=True, timeout=40, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preview_apply_cancel_and_context_changes(self):
        self.run_node(r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=''; this.style={}; this.dataset={}; this.attrs={}; }
  append(...children) { for (const child of children) { child.parent=this; this.children.push(child); } }
  replaceChildren(...children) { this.children=[...children]; }
  addEventListener(type, fn) { (this.events[type]??=[]).push(fn); }
  fire(type) { for(const fn of this.events[type]||[]) fn({}); }
  setAttribute(name,value) { this.attrs[name]=value; }
  removeAttribute(name) { delete this.attrs[name]; }
  focus() { document.activeElement=this; }
  select() { this.selectionStart=0; this.selectionEnd=this.value.length; document.activeElement=this; }
  setSelectionRange(start,end,direction) { this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction; }
  remove() { if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null; }
}
globalThis.document={createElement:tag=>new Element(tag),body:new Element('body'),activeElement:null};
const copied=[];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}});
const { WanTunerPanel,contextFingerprint }=await import(pathToFileURL(process.argv[1]));
const knowledge=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const source={facts:['@图片1 shows one actor'],uncertainties:[],invariants:['counter edge stays straight']};
const result={prompt:'Keep the gaze on the page.',analysis:'Simplified the action.',changes:['One action'],risks:['Hands may deform'],suggestedParams:{duration:8,resolution:'1080P'},assumptions:[]};
const make=async transform=>{
 const context={profileId:'p1',modelId:'wan3.0-video',targetModel:'wan3.0-video',prompt:'Read the page.',params:{duration:15,resolution:'480P'},capabilities:{known:true,properties:{duration:{type:'integer',minimum:2,maximum:30},resolution:{enum:['480P']}},inputRoles:['first_frame','last_frame']},profiles:[{id:'p1',name:'Default'}],media:[{id:'first',kind:'image',role:'first_frame',ready:true,url:'data:image/png;base64,AA=='}]};
 const calls=[],applied=[];
 const panel=new WanTunerPanel({t:key=>key,getContext:()=>context,loadModels:async()=>[{id:'chat-small'},{id:'vision',supports_image:true}],knowledgeLoader:async()=>knowledge,
  transform:async(payload,signal,onEvent)=>{calls.push({payload,signal}); return transform?transform(payload,signal,onEvent):{text:JSON.stringify(payload.action==='wan_analyze'?source:result)};},
  resolveMedia:async()=>{throw new Error('unexpected resolver');},onApply:prompt=>applied.push(prompt)});
 await tick();panel.model.value='chat-small';
 return {panel,context,calls,applied};
};
{
 const {panel,context,calls,applied}=await make();
 try {
  const params=JSON.stringify(context.params);
  assert.equal(panel.mode.attrs['aria-label'],'wanMode');
  assert.equal(panel.model.attrs['aria-label'],'promptTransformModel');
  assert.equal(panel.issue.attrs['aria-label'],'wanIssue');
  await panel.execute();
  assert.equal(calls.length,2);
  assert.equal(calls[0].payload.action,'wan_analyze');
  assert.equal(calls[1].payload.action,'wan_tune');
  assert.deepEqual(JSON.parse(calls[1].payload.messages[1].content).capabilities,context.capabilities);
  assert.equal(contextFingerprint(context),panel.resultKey);
  assert.equal(panel.apply.disabled,false);
  assert.equal(panel.resultPrompt.value,result.prompt);
  assert.equal(context.prompt,'Read the page.');
  assert.equal(JSON.stringify(context.params),params);
  await panel.copy(result.prompt);
  assert.equal(copied.at(-1),result.prompt);
  assert.ok(!copied.at(-1).includes('suggestedParams'));
  panel.apply.fire('click');
  assert.deepEqual(applied,[result.prompt]);
  context.params.duration=30;
  panel.checkContext();
  assert.equal(panel.apply.disabled,true);
  assert.equal(panel.completed,null);
  assert.equal(panel.result.hidden,true);
  assert.equal(context.prompt,'Read the page.');
  context.mode='reference-to-video';context.media=[{id:'reference',kind:'video',role:'reference_video',ready:true,url:'https://media.example/source.mp4'}];
  panel.checkContext();assert.equal(panel.mode.value,'reference');
  panel.mode.value='v2v';panel.changed();
  context.media[0].url='https://media.example/new.mp4';panel.checkContext();
  assert.equal(panel.mode.value,'v2v');
 } finally { panel.dispose(); }
}
{
 let resolve;
 const {panel,context,calls}=await make((payload,signal)=>payload.action==='wan_analyze'?{text:JSON.stringify(source)}:new Promise(done=>{resolve=done;}));
 try {
  const pending=panel.execute();await tick();await tick();
  assert.equal(calls.at(-1).payload.action,'wan_tune');
  context.media[0].url='data:image/png;base64,BB==';
  panel.checkContext();
  assert.equal(calls.at(-1).signal.aborted,true);
  resolve({text:JSON.stringify(result)});await pending;
  assert.equal(panel.completed,null);
  assert.equal(panel.apply.disabled,true);
  assert.equal(panel.result.hidden,true);
 } finally { panel.dispose(); }
}
{
 const {panel,context}=await make();
 try {
  await panel.execute();assert.equal(panel.apply.disabled,false);
  context.capabilities.properties.duration.maximum=15;
  panel.checkContext();
  assert.equal(panel.completed,null);
  assert.equal(panel.apply.disabled,true);
 } finally {panel.dispose();}
}
{
 let resolve;
 const {panel,calls}=await make(()=>new Promise(done=>{resolve=done;}));
 try {
  const pending=panel.execute();await tick();await tick();
  assert.equal(calls.length,1);
  panel.cancelRequest();
  assert.equal(calls[0].signal.aborted,true);
  resolve({text:JSON.stringify(source)});await pending;
  assert.equal(calls.length,1);
  assert.equal(panel.apply.disabled,true);
 } finally { panel.dispose(); }
}
{
 const {panel}=await make();
 try {
  panel.output.value='diagnose';panel.issue.value='Step warps';
  await panel.execute();
  assert.equal(panel.completed,null);
  assert.equal(panel.apply.disabled,true);
  assert.ok(panel.status.textContent);
 } finally { panel.dispose(); }
}
{
 let resolve;
 const {panel,context,calls}=await make(()=>new Promise(done=>{resolve=done;}));
 try {
  const pending=panel.execute();await tick();await tick();
  context.params.resolution='720P';
  resolve({text:JSON.stringify(source)});await pending;
  assert.equal(calls.length,1,'stale analysis must not start tuning');
  assert.equal(panel.apply.disabled,true);
 } finally {panel.dispose();}
}
{
 const deferred=[];
 const {panel}=await make(()=>new Promise(resolve=>deferred.push(resolve)));
 try {
  panel.analyze.checked=false;
  const first=panel.execute();await tick();await tick();
  panel.cancelRequest();
  const second=panel.execute();await tick();await tick();
  assert.equal(deferred.length,2);
  assert.equal(panel.cancel.hidden,false);
  deferred[0]({text:JSON.stringify(result)});await first;
  assert.equal(panel.cancel.hidden,false,'old request must not hide new cancel control');
  assert.equal(panel.run.disabled,true);
  deferred[1]({text:JSON.stringify(result)});await second;
  assert.equal(panel.cancel.hidden,true);
  assert.equal(panel.apply.disabled,false);
 } finally {panel.dispose();}
}
{
 let fail=false;
 const {panel,calls}=await make(payload=>{
  if(payload.action==='wan_analyze' && fail) throw new Error('media unavailable');
  return {text:JSON.stringify(payload.action==='wan_analyze'?source:result)};
 });
 try {
  await panel.execute();assert.ok(panel.sourceAnalysis.facts.length);
  fail=true;await panel.execute();
  assert.equal(panel.sourceAnalysis,null);
  assert.equal(panel.completed,null);
  assert.equal(panel.details.children.length,0);
  assert.equal(panel.apply.disabled,true);
  assert.equal(calls.at(-1).payload.action,'wan_analyze');
 } finally {panel.dispose();}
}
{
 const {panel}=await make();
 const navigatorBefore=globalThis.navigator;
 try {
  await panel.execute();
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{}});
  const focused=new Element('textarea');focused.value='existing selection';focused.setSelectionRange(2,7,'backward');
  document.body.append(focused);focused.focus();
  let legacyCopies=0;
  document.execCommand=command=>{
   assert.equal(command,'copy');
   assert.equal(document.activeElement.value,result.prompt);
   assert.equal(document.activeElement.readOnly,true);
   assert.equal(document.activeElement.selectionStart,0);
   assert.equal(document.activeElement.selectionEnd,result.prompt.length);
   ++legacyCopies;return true;
  };
  await panel.copy(result.prompt);
  assert.equal(legacyCopies,1);
  assert.equal(document.activeElement,focused);
  assert.deepEqual([focused.selectionStart,focused.selectionEnd,focused.selectionDirection],[2,7,'backward']);
  assert.equal(document.body.children.length,1,'temporary clipboard textarea is removed');
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async()=>{throw new Error('denied');}}}});
  await panel.copy(result.prompt);
  assert.equal(legacyCopies,2,'permission rejection also uses the fallback');
  assert.equal(document.activeElement,focused);
  document.execCommand=()=>false;
  await panel.copy(result.prompt);
  assert.equal(panel.status.textContent,'wanCopyFailed');
  assert.equal(document.body.children.length,1,'failure also removes the textarea');
  assert.equal(document.activeElement,focused);
  focused.remove();
 } finally {panel.dispose();Object.defineProperty(globalThis,'navigator',{configurable:true,value:navigatorBefore});}
}
''')

    def test_media_resolution_and_actual_context(self):
        self.run_node(r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(process.argv[2],'utf8');
const helpers=source.slice(source.indexOf('function promptFileMedia('),source.indexOf('function listOf('));
const app={nodeOutputs:{}};
const context={URL,URLSearchParams,app,location:{href:'http://localhost:8188/'},
 value:(node,name,fallback='')=>node.widgets?.find(item=>item.name===name)?.value??fallback,
 VIDEO_PORTS:{first_frame:'first_frame',last_frame:'last_frame',reference_image_1:'reference_image',reference_video_1:'reference_video'}};
vm.runInNewContext(helpers+'\nglobalThis.collect=nodePromptMedia;globalThis.output=promptOutputMedia;globalThis.capabilities=promptSchemaCapabilities;',context);
const load={id:1,type:'LoadImage',widgets:[{name:'image',value:'series/f01.png'}]};
const pending={id:2,type:'ImageGenerator',title:'Pending image'};
const graph={links:{7:{origin_id:1},8:{origin_id:2}},getNodeById:id=>id===1?load:pending};
const node={id:10,graph,inputs:[{name:'first_frame',link:7},{name:'last_frame',link:8}]};
const media=JSON.parse(JSON.stringify(context.collect(node,{})));
assert.equal(media.length,2);
assert.equal(media[0].ready,true);
assert.equal(media[0].filename,'f01.png');
assert.equal(media[0].subfolder,'series');
assert.equal(media[0].role,'first_frame');
assert.equal(media[1].ready,false);
assert.equal(media[1].role,'last_frame');
load.widgets[0].value='series/f02.png';
assert.equal(context.collect(node,{})[0].filename,'f02.png');
app.nodeOutputs[2]={images:[{filename:'result.png',subfolder:'done',type:'output'}]};
assert.equal(context.collect(node,{})[1].ready,true);
assert.equal(context.collect(node,{})[1].filename,'result.png');
const task={id:3,easyapiPromptOutput:{easyapi_task:[{latest:{data:{data:{download_url:'https://media.example/a.mp4'}}}}]}};
assert.equal(context.output(task,'video','result_video','result')[0].url,'https://media.example/a.mp4');
const param=JSON.parse(JSON.stringify(context.collect({id:20,inputs:[]},{input_references:[{role:'reference_video',type:'video',url:'https://media.example/b.mp4'}]})));
assert.equal(param[0].role,'reference_video');
assert.equal(param[0].kind,'video');
const nodes=new Map();const links={};let linkId=20;
const connect=(target,name,origin)=>{links[++linkId]={origin_id:origin.id};(target.inputs??=[]).push({name,link:linkId});};
const actorA={id:31,type:'LoadImage',widgets:[{name:'image',value:'actor-a.png'}]};
const actorB={id:32,type:'LoadImage',widgets:[{name:'image',value:'actor-b.png'}]};
const named={id:33,type:'LoadImage',widgets:[{name:'image',value:'named.png'}]};
const groupA={id:41,type:'EasyAPIMedia',widgets:[{name:'kind',value:'image'},{name:'role',value:'reference'}]};
const groupB={id:42,type:'EasyAPIMedia',widgets:[{name:'kind',value:'image'},{name:'role',value:'reference'}]};
const groupC={id:43,type:'EasyAPIMedia',widgets:[{name:'kind',value:'image'},{name:'role',value:'reference'},{name:'url',value:'https://media.example/c.png'}]};
connect(groupA,'image',actorA);connect(groupB,'image',actorB);connect(groupB,'references',groupA);
connect(groupC,'image',actorA);connect(groupC,'references',groupB);
const mixed={id:50,inputs:[]};connect(mixed,'references',groupC);connect(mixed,'reference_image_1',named);
for(const item of [actorA,actorB,named,groupA,groupB,groupC,mixed]){nodes.set(item.id,item);item.graph={links,getNodeById:id=>nodes.get(id)};}
const ordered=context.collect(mixed,{});
assert.deepEqual(Array.from(ordered,item=>item.filename||item.url),['named.png','actor-a.png','actor-b.png','actor-a.png','https://media.example/c.png']);
const audio={id:90,type:'LoadAudio',widgets:[{name:'audio',value:'voice.wav'}]};
const mediaAudio={id:91,type:'EasyAPIMedia',widgets:[{name:'kind',value:'audio'},{name:'role',value:'reference_audio'}]};
connect(mediaAudio,'audio',audio);nodes.set(audio.id,audio);nodes.set(mediaAudio.id,mediaAudio);mediaAudio.graph={links,getNodeById:id=>nodes.get(id)};
const audioTarget={id:92,inputs:[],graph:mediaAudio.graph};connect(audioTarget,'references',mediaAudio);
assert.equal(context.collect(audioTarget,{})[0].filename,'voice.wav');
const localPreview={id:93,easyapiPromptOutput:{easyapi_media:[{kind:'image',url:'/easyapi/files/result.png'}]}};
assert.equal(context.output(localPreview,'image','reference_image','local')[0].type,'easyapi');
const externalView=context.collect({id:94,inputs:[]},{input_references:[{role:'reference_image',type:'image',url:'https://remote.example/view?filename=x.png'}]})[0];
assert.equal(externalView.filename,undefined);
const metadata={
 matched:true,declared:true,
 request_schema:{type:'object',properties:{duration:{type:'integer',maximum:30,description:'unused description'}}},
 video:{variants:{'image-to-video':{
  roles:['first_frame','last_frame'],
  request_schema:{type:'object',required:['prompt'],properties:{
   prompt:{type:'string',maxLength:20000},
   resolution:{type:'string',enum:['480P','720P','1080P'],description:'read-only metadata'},
  }},
 }}},
};
const actual=JSON.parse(JSON.stringify(context.capabilities(metadata,'image-to-video')));
assert.deepEqual(actual.inputRoles,['first_frame','last_frame']);
assert.deepEqual(actual.properties.resolution,{type:'string',enum:['480P','720P','1080P']});
assert.equal(actual.properties.prompt.maxLength,20000);
assert.equal(actual.properties.duration,undefined,'selected variant schema takes precedence');
assert.deepEqual(JSON.parse(JSON.stringify(context.capabilities({matched:false,request_schema:metadata.request_schema},''))),{known:false});
''')

    def test_video_samples_end_range_and_evidence_labels(self):
        self.run_node(r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {videoFrames}=await import(pathToFileURL(process.argv[1]));
const videos=[];
class Video {
 constructor(){this.events=new Map();this.duration=15;this.videoWidth=854;this.videoHeight=480;this.position=0;this.readyState=2;this.seeks=[];}
 addEventListener(type,fn){const set=this.events.get(type)||new Set();set.add(fn);this.events.set(type,set);}
 removeEventListener(type,fn){this.events.get(type)?.delete(fn);}
 fire(type){for(const fn of [...(this.events.get(type)||[])])fn();}
 set src(value){this.url=value;queueMicrotask(()=>this.fire('loadedmetadata'));}
 set currentTime(value){assert.notEqual(value,this.position,'same-position seeking would not produce seeked');this.position=value;this.seeks.push(value);queueMicrotask(()=>this.fire('seeked'));}
 get currentTime(){return this.position;}
 removeAttribute(){}
 load(){}
}
globalThis.document={createElement(tag){if(tag==='video'){const video=new Video();videos.push(video);return video;}return {getContext:()=>({drawImage(){}}),toDataURL:()=> 'data:image/jpeg;base64,AA=='};}};
const signal=new AbortController().signal;
const frames=await videoFrames({id:'source',name:'Clip',label:'@视频1',url:'https://media.example/a.mp4'},{start:14,end:15},signal);
assert.equal(frames.length,6);
assert.equal(new Set(frames.map(f=>f.timestamp)).size,frames.length);
assert.equal(frames.at(-1).timestamp,14.96);
assert.ok(frames.every(f=>f.analysisOnly&&f.sourceMediaId==='source'&&f.sourceLabel==='@视频1'));
const fromStart=await videoFrames({id:'begin',name:'Clip',url:'https://media.example/b.mp4'},{start:0,end:1},signal);
assert.equal(fromStart[0].timestamp,0);
assert.ok(!videos[1].seeks.includes(0));
const limited=await videoFrames({id:'limit',name:'Clip',url:'https://media.example/c.mp4'},{start:4,end:6},signal,2);
assert.equal(limited.length,2);assert.equal(limited[0].timestamp,3.5);assert.equal(limited.at(-1).timestamp,6.5);
''')


if __name__ == "__main__":
    unittest.main()
