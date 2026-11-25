"""Exercise Wan positioning through the real prompt editor, with deterministic DOM sizes."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class PromptPositionFrontendTests(unittest.TestCase):
    def run_node(self, body):
        setup = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Events {
 constructor(){this.events=new Map();}
 addEventListener(type,fn){const entries=this.events.get(type)||new Set();entries.add(fn);this.events.set(type,entries);}
 removeEventListener(type,fn){this.events.get(type)?.delete(fn);}
 fire(type,extra={}){const event={target:this,pointerId:7,pointerType:'mouse',button:0,isPrimary:true,clientX:500,clientY:400,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra};for(const fn of [...(this.events.get(type)||[])])fn(event);return event;}
 count(type){return this.events.get(type)?.size||0;}
}
class Element extends Events {
 constructor(tag){super();this.tag=tag;this.tagName=tag.toUpperCase();this.nodeType=1;this.children=[];this.attrs={};this.dataset={};this.style={};this.value='';this.intrinsicHeight=600;this.measures=[];this.captured=new Set();
  const names=new Set();this.classList={add:name=>names.add(name),remove:name=>names.delete(name),contains:name=>names.has(name),toggle:(name,force)=>{if(force??!names.has(name))names.add(name);else names.delete(name);}};}
 append(...children){for(const child of children){if(child.parent)child.parent.children=child.parent.children.filter(item=>item!==child);child.parent=this;this.children.push(child);}}
 replaceChildren(...children){for(const child of this.children)child.parent=null;this.children=[];this.append(...children);}
 contains(node){return this===node||this.children.some(child=>child.contains?.(node));}
 setAttribute(name,value){this.attrs[name]=String(value);}
 removeAttribute(name){delete this.attrs[name];}
 focus(){document.activeElement=this;}
 matches(selector){return selector==='button'?this.tag==='button':selector===':popover-open'?false:false;}
 closest(selector){return selector.split(',').some(part=>part.trim()==='button')&&this.tag==='button'?this:this.parent?.closest(selector)||null;}
 setPointerCapture(id){this.captured.add(id);}
 releasePointerCapture(id){this.captured.delete(id);}
 hasPointerCapture(id){return this.captured.has(id);}
 get offsetWidth(){return this.tag==='section'?Math.min(720,window.innerWidth-16):320;}
 get offsetHeight(){if(this.tag!=='section')return 40;this.measures.push({height:this.style.height,maxHeight:this.style.maxHeight});return Math.min(this.intrinsicHeight,Number.parseFloat(this.style.maxHeight)||Infinity);}
 getBoundingClientRect(){if(this.tag==='section'){const left=Number.parseFloat(this.style.left)||0,top=Number.parseFloat(this.style.top)||0,width=this.offsetWidth,height=this.offsetHeight;return {left,top,width,height,right:left+width,bottom:top+height};}return this.rect||{top:100,bottom:180,right:450,left:100,width:350,height:80};}
}
const document=new Events();Object.assign(document,{createElement:tag=>new Element(tag),body:new Element('body'),activeElement:null});
const window=new Events();Object.assign(window,{innerWidth:1249,innerHeight:1277});
const observers=[];
class ResizeObserver {
 constructor(fn){this.fn=fn;this.targets=new Set();this.disconnected=false;observers.push(this);}
 observe(node){this.targets.add(node);this.disconnected=false;}
 unobserve(node){this.targets.delete(node);}
 disconnect(){this.targets.clear();this.disconnected=true;}
 fire(){this.fn([...this.targets].map(target=>({target})));}
}
const context={document,window,ResizeObserver,setTimeout,clearTimeout,AbortController,localStorage:{getItem:()=>null,setItem(){},removeItem(){}}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8').replace('export class','class')+'\nglobalThis.Editor=PromptEditor;',context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const make=()=>new context.Editor({value:'scene',t:key=>key,onInput(){},getLocale:()=> 'en',getContext:()=>({profileId:'p',modelId:'wan3.0-video',targetModel:'wan3.0-video',profiles:[{id:'p'}]}),loadModels:async()=>[{id:'chat'}],transform:async()=>({text:'optimized'}),loadWanPanel:async()=>({WanTunerPanel:class {constructor(){this.root=new Element('div');}dispose(){this.disposed=true;}checkContext(){}}})});
const left=editor=>Number.parseFloat(editor.popup.style.left);
const top=editor=>Number.parseFloat(editor.popup.style.top);
const dragTo=(editor,x,y)=>{const oldLeft=left(editor),oldTop=top(editor);editor.popupHeading.fire('pointerdown',{target:editor.heading,clientX:100,clientY:100});window.fire('pointermove',{clientX:100+x-oldLeft,clientY:100+y-oldTop});window.fire('pointerup');};
(async()=>{
'''
        script = setup + body + r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        source = Path(__file__).parents[1] / "web/prompt-editor.js"
        result = subprocess.run([shutil.which("node"), "-", str(source)], input=script, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wan_opens_centered_on_both_axes(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();
assert.equal(left(editor),(1249-720)/2);
assert.equal(top(editor),(1277-600)/2,'Wan panel must be vertically centered rather than pinned to the top edge');
editor.dispose();
''')

    def test_height_cap_is_applied_before_measurement_and_content_growth_recenters(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();
editor.popup.style.height='9000px';editor.popup.style.maxHeight='10px';editor.popup.measures=[];
editor.position();
assert.ok(editor.popup.measures.length>0,'position must measure the actual constrained panel height');
assert.ok(editor.popup.measures.every(value=>value.height==='auto'&&value.maxHeight==='760px'),'height and viewport cap must be applied before measuring');
const active=observers.filter(observer=>observer.targets.size);
assert.ok(active.some(observer=>observer.targets.has(editor.popup)||observer.targets.has(editor.body)),'asynchronous panel content needs a resize observer');
editor.popup.intrinsicHeight=700;for(const observer of active)observer.fire();
assert.equal(top(editor),(1277-700)/2);
window.innerHeight=650;window.fire('resize');
assert.equal(editor.popup.style.maxHeight,'634px');assert.equal(top(editor),8);
editor.dispose();
assert.ok(observers.every(observer=>!observer.targets.size),'disposing the editor disconnects size observation');
''')

    def test_title_drag_keeps_manual_position_and_cleans_pointer_listeners(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();
const startLeft=left(editor),startTop=top(editor);
const pressed=editor.popupHeading.fire('pointerdown',{target:editor.heading,clientX:500,clientY:400});
assert.equal(pressed.prevented,true);assert.equal(pressed.stopped,true,'dragging the panel must not start moving the graph');
assert.equal(window.count('pointermove'),1);assert.equal(window.count('pointerup'),1);assert.equal(window.count('pointercancel'),1);
window.fire('pointermove',{clientX:580,clientY:460});
assert.equal(left(editor),startLeft+80);assert.equal(top(editor),startTop+60);
editor.position();window.fire('scroll',{target:document});
assert.equal(left(editor),startLeft+80);assert.equal(top(editor),startTop+60,'ordinary reflows preserve the chosen position');
window.fire('pointerup');
assert.equal(window.count('pointermove'),0);assert.equal(window.count('pointerup'),0);assert.equal(window.count('pointercancel'),0);
window.fire('pointermove',{clientX:900,clientY:900});
assert.equal(left(editor),startLeft+80);assert.equal(top(editor),startTop+60);
editor.popupHeading.fire('pointerdown',{target:editor.heading});window.fire('pointercancel');
assert.equal(window.count('pointermove'),0);assert.equal(window.count('pointerup'),0);assert.equal(window.count('pointercancel'),0);
editor.dispose();
''')

    def test_unrelated_pointer_is_ignored_and_lost_capture_ends_drag(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();
const startLeft=left(editor),startTop=top(editor);
editor.popupHeading.fire('pointerdown',{target:editor.heading,pointerId:7});
assert.equal(editor.popup.dataset.dragging,'true');assert.equal(editor.popupHeading.hasPointerCapture(7),true);
window.fire('pointermove',{pointerId:8,clientX:900,clientY:900});window.fire('pointerup',{pointerId:8});
assert.equal(left(editor),startLeft);assert.equal(top(editor),startTop);assert.equal(window.count('pointermove'),1);
editor.popupHeading.fire('lostpointercapture',{pointerId:7});
assert.equal(window.count('pointermove'),0);assert.equal(window.count('pointerup'),0);assert.equal(window.count('pointercancel'),0);
assert.equal(editor.popup.dataset.dragging,undefined);assert.equal(editor.popupHeading.hasPointerCapture(7),false);
editor.dispose();
''')

    def test_non_primary_or_button_press_cannot_start_drag(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();
const startLeft=left(editor),startTop=top(editor);
for(const event of [{button:2,target:editor.heading},{isPrimary:false,target:editor.heading},{target:editor.closeButton}]){
 editor.popupHeading.fire('pointerdown',event);
 assert.equal(window.count('pointermove'),0);
 window.fire('pointermove',{clientX:900,clientY:900});
 assert.equal(left(editor),startLeft);assert.equal(top(editor),startTop);
}
editor.dispose();
''')

    def test_manual_position_is_clamped_as_content_and_viewport_change(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();dragTo(editor,500,600);
assert.equal(left(editor),500);assert.equal(top(editor),600);
editor.popup.intrinsicHeight=900;
for(const observer of observers.filter(observer=>observer.targets.size))observer.fire();
assert.equal(left(editor),500);assert.equal(top(editor),1277-760-8,'content growth clamps the bottom edge');
window.innerWidth=1000;window.innerHeight=800;window.fire('resize');
assert.equal(left(editor),1000-720-8);assert.equal(top(editor),800-760-8);
window.innerWidth=600;window.innerHeight=500;window.fire('resize');
assert.equal(left(editor),8);assert.equal(top(editor),8);
assert.equal(editor.popup.style.maxHeight,'484px');
editor.dispose();
''')

    def test_close_stops_drag_and_reopening_starts_centered(self):
        self.run_node(r'''
const editor=make();editor.open('wan');await tick();dragTo(editor,400,450);
editor.popupHeading.fire('pointerdown',{target:editor.heading});
assert.equal(window.count('pointermove'),1);
editor.close();
assert.equal(window.count('pointermove'),0);assert.equal(window.count('pointerup'),0);assert.equal(window.count('pointercancel'),0);
assert.equal(window.count('resize'),0);assert.equal(window.count('scroll'),0);
assert.ok(observers.every(observer=>!observer.targets.size));
editor.open('wan');await tick();
assert.equal(left(editor),(1249-720)/2);assert.equal(top(editor),(1277-600)/2,'manual drag position is reset for each opening');
editor.dispose();
''')

    def test_regular_popover_keeps_its_anchor_position_and_does_not_drag(self):
        self.run_node(r'''
const editor=make();editor.open('optimize');await tick();
assert.equal(left(editor),8);assert.equal(top(editor),186);
assert.equal(editor.popup.style.bottom,'auto');assert.equal(editor.popup.style.maxHeight,'400px');
editor.popupHeading.fire('pointerdown',{target:editor.heading});assert.equal(window.count('pointermove'),0);
editor.root.rect={top:300,bottom:380,right:900,left:550,width:350,height:80};
window.innerHeight=400;editor.position();
assert.equal(editor.popup.style.left,'180px');assert.equal(editor.popup.style.top,'auto');
assert.equal(editor.popup.style.bottom,'106px');assert.equal(editor.popup.style.maxHeight,'286px');
editor.dispose();
''')
