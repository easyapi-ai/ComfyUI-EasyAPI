"""Replay persistent generic tuning sessions in a real native-popover browser."""
import html
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_prompt_drag_frontend import chrome_executable


@unittest.skipUnless(chrome_executable(), "An installed Chrome browser is needed")
class VideoTunerPanelFrontendTests(unittest.TestCase):
    def run_browser(self, body):
        root = Path(__file__).parents[1]
        scripts = []
        for name in ["wan-tuner-core.js", "wan-tuner-panel.js", "video-tuning-state.js", "video-tuner-panel.js", "prompt-editor.js"]:
            source = (root / "web" / name).read_text(encoding="utf-8")
            names = re.findall(r"^export (?:async )?(?:function|class|const) (\w+)", source, re.M)
            source = re.sub(r"^import [\s\S]*?from [\"'][^\"']+[\"'];\r?\n", "", source, flags=re.M)
            source = re.sub(r"^export \{[^\n]+\};?\s*$", "", source, flags=re.M)
            source = re.sub(r"^export ", "", source, flags=re.M).replace("import.meta.url", json.dumps((root / "web" / name).as_uri()))
            scripts.append("(function(){" + source + "\nObject.assign(globalThis,{" + ",".join(names) + "});})();")
        setup = r'''
const check=(ok,message)=>{if(!ok)throw new Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
localStorage.clear();
const context={profileId:'p',targetModel:'seedance-example',modelId:'seedance-example',operation:'video-generation',
 prompt:'A person reads a book.',params:{duration:5},profiles:[{id:'p',name:'Mock',model_catalog_revision:'r1'}],media:[],resultMedia:[],capabilities:{known:true}};
const success={prompt:'The person slowly turns one page.',analysis:'One visible action.',changes:['Simplified action'],risks:[],assumptions:[],suggestedParams:{duration:5}};
let resolveRun,runSignal,starts=0,resumes=0;
const transport={run:async(value,signal,event,started)=>{starts++;runSignal=signal;started('test-run');return new Promise(resolve=>{resolveRun=resolve;});},
 resume:async()=>{resumes++;return success;}};
const make=()=>new PromptEditor({value:context.prompt,t:key=>key,onInput:prompt=>{context.prompt=prompt;},
 getContext:()=>context,loadModels:async()=>[{id:'chat',supports_image:true}],transform:async()=>{throw new Error('legacy transport should not run');},
 getLocale:()=> 'en',tuningScopeId:()=> 'generic-test-node',videoTuning:transport,
 loadWanPanel:async()=>({WanTunerPanel:VideoTunerPanel})});
let editor=make();document.getElementById('widget').append(editor.root);editor.open('wan');await tick();
'''
        script = "\n".join(scripts) + "\n(async()=>{\n" + setup + body + r'''
editor.dispose();
const out=document.createElement('pre');out.id='result';out.textContent=JSON.stringify({ok:true,starts,resumes});document.body.append(out);
})().catch(error=>{const out=document.createElement('pre');out.id='result';out.textContent=JSON.stringify({ok:false,error:error.stack});document.body.append(out);});
'''
        page = "<!doctype html><meta charset='utf-8'><style>" + (root / "web/easyapi.css").read_text(encoding="utf-8") + \
               "</style><div id='widget'></div><button id='canvas'>Canvas</button><script>" + script.replace("</script", "<\\/script") + "</script>"
        with tempfile.TemporaryDirectory(prefix="easyapi-video-panel-") as directory:
            target = Path(directory) / "index.html"
            target.write_text(page, encoding="utf-8")
            run = subprocess.run([chrome_executable(), "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-extensions",
                                  "--disable-background-networking", "--no-first-run", "--no-default-browser-check",
                                  "--user-data-dir=" + str(Path(directory) / "browser"), "--virtual-time-budget=3000",
                                  "--window-size=1249,1277", "--dump-dom", target.as_uri()], capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=30)
        match = re.search(r'<pre id="result">(.*?)</pre>', run.stdout, re.S)
        self.assertIsNotNone(match, run.stderr[-800:] + run.stdout[-800:])
        result = json.loads(html.unescape(match.group(1)))
        self.assertTrue(result["ok"], json.dumps(result, ensure_ascii=False))

    def test_canvas_click_and_hiding_do_not_cancel_or_destroy_tuning(self):
        self.run_browser(r'''
check(editor.popup.getAttribute('popover')==='manual','video panel must not use light dismiss');
const panel=editor.wanPanel;
document.getElementById('canvas').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));
check(editor.mode==='wan'&&editor.popup.matches(':popover-open'),'clicking canvas must preserve the panel');
const running=panel.execute();await tick();
editor.closeButton.click();
check(editor.mode===null&&editor.wanPanel===panel,'close only hides the persistent session');
check(!runSignal.aborted,'hiding the panel must not cancel a model call');
resolveRun(success);await running;
editor.open('wan');await tick();
check(editor.wanPanel===panel&&panel.resultPrompt.value===success.prompt,'reopening must recover the same result');
check(starts===1,'reopening must not submit another model request');
''')

    def test_stale_result_remains_copyable_and_draft_restores_without_a_model_call(self):
        self.run_browser(r'''
const panel=editor.wanPanel;const running=panel.execute();await tick();resolveRun(success);await running;
check(!panel.apply.disabled,'unchanged result can be applied');
context.params.duration=10;panel.checkContext();
check(panel.completed.prompt===success.prompt&&!panel.result.hidden,'context changes must not delete the product');
check(!panel.copyPrompt.disabled&&panel.apply.disabled,'stale results can be copied but cannot be applied');
editor.dispose();editor=make();document.getElementById('widget').append(editor.root);editor.open('wan');await tick();
check(editor.wanPanel.resultPrompt.value===success.prompt,'new browser view restores the saved result');
check(!editor.wanPanel.copyPrompt.disabled,'restored result remains copyable');
check(starts===1,'draft recovery must not silently call the model again');
''')

    def test_local_storage_sanitizes_credentials_and_original_media(self):
        self.run_browser(r'''
check(writeTuningDraft('sanitized-test',{completed:success,params:{api_key:'secret',authorization:'secret',duration:5},
 media:[{url:'data:image/png;base64,AAAA'}],resultMedia:[{path:'/private/file.mp4'}],partial:'draft'}),'draft should save');
const raw=localStorage.getItem('easyapi.videoTuningDraft.v1.sanitized-test');
check(!raw.includes('secret')&&!raw.includes('base64')&&!raw.includes('/private/'),'draft must exclude credentials and source assets');
check(readTuningDraft('sanitized-test').completed.prompt===success.prompt,'text product must remain recoverable');
''')

    def test_rebuilt_view_resumes_a_saved_run_instead_of_cancelling_or_resubmitting(self):
        self.run_browser(r'''
const panel=editor.wanPanel;const running=panel.execute();await tick();
editor.dispose();
check(runSignal.aborted&&runSignal.reason.name==='TuningViewDetached','tearing down a workflow view only detaches polling');
check(readTuningDraft('generic-test-node').runId==='test-run','the accepted run ID must remain recoverable');
resolveRun(success);await running;
editor=make();document.getElementById('widget').append(editor.root);editor.open('wan');await tick();
check(resumes===1&&starts===1,'a rebuilt view resumes instead of submitting another paid request');
check(editor.wanPanel.completed.prompt===success.prompt,'resumed result is visible');
''')

    def test_uploaded_diagnostic_video_uses_a_server_asset_instead_of_a_blob_url(self):
        self.run_browser(r'''
const panel=editor.wanPanel;
panel.uploadMedia=async(file,signal)=>({kind:'video',filename:'uploaded-video.mp4',type:'easyapi_input',name:file.name,ready:true});
await panel.addFiles([{name:'diagnostic.mp4',type:'video/mp4'}]);
check(panel.output.value==='diagnose','a supplied result video selects diagnostic tuning');
panel.issue.value='Background bends during the action.';
const value=panel.requestValue();
check(value.resultMedia.length===1&&value.resultMedia[0].filename==='uploaded-video.mp4','server receives an accessible video descriptor');
check(!JSON.stringify(value).includes('blob:'),'browser-local object URLs must not be sent to the server');
check(value.media.length===0,'diagnostic video must not become a generation reference');
''')
