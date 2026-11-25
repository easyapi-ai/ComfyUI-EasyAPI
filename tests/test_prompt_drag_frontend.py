"""Replay native-popover pointer propagation in an installed Chrome browser."""
import html
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path


def chrome_executable():
    candidates = [shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")]
    candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        directory = os.environ.get(variable)
        if directory:
            candidates.append(str(Path(directory) / "Google/Chrome/Application/chrome.exe"))
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


@unittest.skipUnless(chrome_executable(), "An installed Chrome browser is needed for native pointer propagation")
class PromptDragFrontendTests(unittest.TestCase):
    def run_browser(self, body):
        root = Path(__file__).parents[1]
        source = (root / "web/prompt-editor.js").read_text(encoding="utf-8").replace("export class", "class")
        stylesheet = (root / "web/easyapi.css").read_text(encoding="utf-8")
        setup = r'''
const metrics={};
const check=(condition,message)=>{if(!condition)throw new Error(message);};
const frame=()=>new Promise(resolve=>setTimeout(resolve,30));
const widget=document.getElementById('widget');
let blockedMove=0,blockedUp=0;
widget.addEventListener('pointermove',event=>{blockedMove++;event.stopPropagation();});
widget.addEventListener('pointerup',event=>{blockedUp++;event.stopPropagation();});
const pointer=(target,type,x,y,extra={})=>target.dispatchEvent(new PointerEvent(type,{bubbles:true,composed:true,cancelable:true,pointerId:7,pointerType:'mouse',isPrimary:true,button:0,clientX:x,clientY:y,...extra}));
const editor=new PromptEditor({value:'scene',t:key=>key,onInput(){},getLocale:()=> 'en',
 getContext:()=>({profileId:'p',modelId:'wan3.0-video',targetModel:'wan3.0-video',profiles:[{id:'p'}]}),
 loadModels:async()=>[{id:'chat'}],transform:async()=>({text:'unused'}),
 loadWanPanel:async()=>({WanTunerPanel:class {constructor(){this.root=document.createElement('div');this.root.style.height='200px';}dispose(){}checkContext(){}}})});
widget.append(editor.root);
editor.open('wan');await frame();await frame();
check(editor.nativePopover,'the test must exercise native top-layer popovers');
check(widget.contains(editor.popup),'top-layer popovers must retain the node/DOMWidget ancestry');
metrics.nativePopover=editor.nativePopover;
metrics.parentNode=editor.popup.parentElement.className;
'''
        script = source + "\n(async()=>{\n" + setup + body + r'''
editor.dispose();
const result=document.createElement('pre');result.id='result';result.textContent=JSON.stringify({ok:true,metrics});document.body.append(result);
})().catch(error=>{const result=document.createElement('pre');result.id='result';result.textContent=JSON.stringify({ok:false,error:error.stack});document.body.append(result);});
'''
        page = "<!doctype html><html><head><style>" + stylesheet + "</style></head><body><div data-node-id='1'><div id='widget' class='dom-widget'>" \
            "</div></div><script>" + script.replace("</script", "<\\/script") + "</script></body></html>"
        with tempfile.TemporaryDirectory(prefix="easyapi-prompt-drag-") as temporary:
            directory = Path(temporary)
            target = directory / "index.html"
            target.write_text(page, encoding="utf-8")
            command = [chrome_executable(), "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-extensions",
                       "--disable-background-networking", "--no-first-run", "--no-default-browser-check",
                       "--window-size=1249,1277", "--virtual-time-budget=3000", "--dump-dom",
                       "--user-data-dir=" + str(directory / "browser"), target.as_uri()]
            process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            with subprocess.Popen(command, encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, **process_options) as browser:
                try:
                    stdout, stderr = browser.communicate(timeout=30)
                finally:
                    if browser.poll() is None:
                        # Kill only this test's independent browser and children before deleting its profile.
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(browser.pid), "/T", "/F"], capture_output=True,
                                           timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            try:
                                os.killpg(browser.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        browser.communicate(timeout=5)
        self.assertEqual(browser.returncode, 0, stderr[-1000:])
        match = re.search(r'<pre id="result">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(match, "Chrome did not finish the DOM test: " + stderr[-1000:] + stdout[-1000:])
        result = json.loads(html.unescape(match.group(1)))
        self.assertTrue(result["ok"], json.dumps(result, ensure_ascii=False))
        return result["metrics"]

    def test_native_popover_drag_survives_dom_widget_bubble_stops(self):
        self.run_browser(r'''
const initial=editor.popup.getBoundingClientRect();
pointer(editor.popupHeading,'pointerdown',initial.left+80,initial.top+16);
pointer(editor.popupHeading,'pointermove',initial.left+160,initial.top+76);
const moved=editor.popup.getBoundingClientRect();
metrics.delta={x:moved.left-initial.left,y:moved.top-initial.top};
metrics.stoppedByWidget={move:blockedMove,up:blockedUp};
check(Math.abs(metrics.delta.x-80)<1&&Math.abs(metrics.delta.y-60)<1,
 'Wan drag did not receive DOMWidget-stopped native pointer events: '+JSON.stringify(metrics));
pointer(editor.popupHeading,'pointerup',initial.left+160,initial.top+76);
check(!editor.wanDrag,'pointerup must finish the drag even when the DOMWidget stops bubbling');
''')

    def test_drag_uses_capture_and_removes_capture_listeners_on_pointerup(self):
        self.run_browser(r'''
const phases=[];
const move=editor.onWanDragMove;
editor.onWanDragMove=event=>{phases.push(event.eventPhase);move(event);};
const initial=editor.popup.getBoundingClientRect();
pointer(editor.popupHeading,'pointerdown',initial.left+80,initial.top+16);
pointer(editor.popupHeading,'pointermove',initial.left+120,initial.top+36);
check(phases.length===1&&phases[0]===Event.CAPTURING_PHASE,
 'the drag listener must run before descendant node/DOMWidget propagation guards: '+JSON.stringify(phases));
pointer(editor.popupHeading,'pointerup',initial.left+120,initial.top+36);
check(!editor.wanDrag,'pointerup must reach its capture listener');
pointer(editor.popupHeading,'pointermove',initial.left+200,initial.top+100);
check(phases.length===1,'pointerup must remove the same capture-phase listener that was registered');
metrics.phases=phases;
''')

    def test_dom_widget_stopped_cancel_and_close_both_remove_drag_capture(self):
        self.run_browser(r'''
widget.addEventListener('pointercancel',event=>event.stopPropagation());
let moves=0;
const move=editor.onWanDragMove;
editor.onWanDragMove=event=>{moves++;move(event);};
const initial=editor.popup.getBoundingClientRect();
pointer(editor.popupHeading,'pointerdown',initial.left+80,initial.top+16);
pointer(editor.popupHeading,'pointercancel',initial.left+80,initial.top+16);
check(!editor.wanDrag,'a widget bubble guard must not prevent pointercancel cleanup');
pointer(editor.popupHeading,'pointermove',initial.left+200,initial.top+100);
check(moves===0,'pointercancel must remove the capture listener');
pointer(editor.popupHeading,'pointerdown',initial.left+80,initial.top+16);
editor.close();
pointer(editor.popupHeading,'pointermove',initial.left+200,initial.top+100);
check(moves===0,'closing the popup must remove capture listeners while it remains under the widget ancestor');
check(!editor.wanDrag,'closing while dragging clears the active drag');
metrics.moves=moves;
''')
