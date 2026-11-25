"""Real Chrome checks for staged, non-streaming tuning and retained products."""
import unittest

import test_video_tuner_panel_frontend as panel_checks
from test_prompt_drag_frontend import chrome_executable


@unittest.skipUnless(chrome_executable(), "An installed Chrome browser is needed")
class VideoTuningProgressFrontendTests(unittest.TestCase):
    def run_browser(self, body):
        panel_checks.VideoTunerPanelFrontendTests().run_browser(body)

    def test_phases_wait_clock_analysis_preview_and_atomic_validated_product(self):
        self.run_browser(r'''
const panel=editor.wanPanel;
panel.setResult(success,{progress:false});
let clock=1000,emit,finish;
panel.now=()=>clock;
panel.tune=async(value,signal,onEvent,onStarted)=>{emit=onEvent;onStarted('phase-run');return new Promise(resolve=>{finish=resolve;});};
const descriptor=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value'),writes=[];
Object.defineProperty(panel.resultPrompt,'value',{get(){return descriptor.get.call(this);},set(value){writes.push(value);descriptor.set.call(this,value);}});
const running=panel.execute(true);
check(!panel.progress.hidden&&panel.progressState==='running','a click immediately shows preparation');
check(panel.phaseStatus.textContent==='Preparing','preparation is visible before a model callback');
check(panel.phaseList.children.length===1&&panel.phaseList.children[0].dataset.phase==='preparing','no analysis is invented before a real event');
check(!panel.result.hidden&&!panel.copyPrompt.disabled&&panel.resultPrompt.value===success.prompt,'the existing complete product remains copyable');
clock=3300;panel.paintPhaseClock();check(panel.phaseClock.textContent==='Waiting 2s','clock measures current waiting seconds');
emit({type:'phase',phase:'preparing',state:'completed'});
emit({type:'phase',phase:'analysis',state:'running'});
clock=4400;panel.paintPhaseClock();check(panel.phaseClock.textContent==='Waiting 1s','a real new stage resets its waiting clock');
emit({type:'delta',phase:'analysis',text:'{"facts":["half JSON'});
emit({type:'delta',phase:'tuning',text:'{"prompt":"unvalidated partial prompt'});
check(writes.length===0&&panel.partialPrompt.value===''&&panel.partialSection.hidden,'JSON deltas never enter product or draft textareas');
const source={facts:['One person is seated.'],invariants:['Keep the original clothing.'],uncertainties:['The back of the room is not visible.']};
emit({type:'phase',phase:'analysis',state:'completed',sourceAnalysis:source});
check(!panel.analysisPreview.hidden&&panel.analysisGroups.querySelectorAll('li').length===3,'complete analysis appears as grouped readable items');
check(!panel.analysisGroups.textContent.includes('"facts"')&&!panel.analysisGroups.textContent.includes('{'),'analysis is not raw JSON');
check(panel.completed===success&&panel.resultPrompt.value===success.prompt,'new analysis must not replace the old completed product');
emit({type:'phase',phase:'tuning',state:'running'});
check(panel.analysisGroups.textContent.includes(source.facts[0]),'analysis remains readable while tuning waits');
emit({type:'phase',phase:'tuning',state:'completed'});
emit({type:'phase',phase:'validation',state:'running'});
check(writes.length===0,'the product stays unchanged while validation is running');
emit({type:'phase',phase:'validation',state:'completed'});
check(writes.length===0,'a phase event alone is not a completed product');
const next={...success,prompt:'A complete checked prompt.',sourceAnalysis:source};finish(next);await running;
check(writes.length===1&&writes[0]===next.prompt,'the complete validated product appears once');
check(panel.progressState==='completed'&&panel.phaseTimer===null&&panel.phaseClock.hidden,'terminal success clears its interval');
check([...panel.phaseList.children].map(item=>item.dataset.phase).join(',')==='preparing,analysis,tuning,validation','real phases retain their order');
check(!panel.progress.textContent.includes('%'),'no invented percentage is shown');
''')

    def test_structured_timeout_keeps_old_product_and_marks_only_the_failed_stage(self):
        self.run_browser(r'''
const panel=editor.wanPanel;panel.setResult(success,{progress:false});
panel.tune=async(value,signal,emit,started)=>{started('timeout-run');emit({type:'phase',phase:'preparing',state:'completed'});emit({type:'phase',phase:'analysis',state:'running'});
 throw Object.assign(new Error('The analysis model did not reply in time.'),{phase:'analysis',code:'upstream_timeout',timeoutSeconds:300,pending:false});};
await panel.execute(true);
check(panel.progressState==='failed'&&panel.phaseTimer===null,'terminal errors stop the phase timer');
check(panel.phaseList.querySelector('[data-phase="analysis"]').dataset.state==='failed','the known failing stage is marked');
check(panel.phaseStatus.textContent.includes('300')&&panel.phaseError.textContent.includes('analysis model'),'timeout stage and timeout seconds are visible');
check(!panel.phaseList.querySelector('[data-phase="tuning"]'),'stages never entered are not displayed');
check(panel.completed.prompt===success.prompt&&!panel.result.hidden&&!panel.copyPrompt.disabled,'a failed new attempt keeps the last complete product');
check(panel.apply.disabled,'a previous product is not silently treated as current');
''')

    def test_connection_failure_preserves_run_id_and_resume_never_posts_again(self):
        self.run_browser(r'''
const panel=editor.wanPanel;panel.setResult(success,{progress:false});let posts=0,observes=0;
panel.tune=async(value,signal,emit,started)=>{posts++;started('accepted-run');emit({type:'phase',phase:'tuning',state:'running'});
 throw Object.assign(new Error('Polling connection interrupted.'),{pending:true,runId:'accepted-run'});};
panel.resumeTuning=async(id,signal,emit)=>{observes++;check(id==='accepted-run','the accepted job is resumed');emit({type:'phase',phase:'validation',state:'running'});emit({type:'phase',phase:'validation',state:'completed'});return {...success,prompt:'Resumed validated result.'};};
await panel.execute();
check(panel.runId==='accepted-run'&&readTuningDraft('generic-test-node').runId==='accepted-run','transport failures retain the accepted job');
check(panel.progressState==='pending'&&panel.phaseList.querySelector('[data-phase="tuning"]').dataset.state==='paused','disconnection is waiting rather than a model failure');
check(panel.run.textContent==='Resume viewing'&&panel.rerun.disabled&&!panel.cancel.hidden,'resume is available and duplicate force runs are disabled');
check(!panel.copyPrompt.disabled,'the previous complete result remains copyable while disconnected');
await panel.execute(true);check(posts===1,'force retry is blocked for an unresolved accepted job');
await panel.execute();
check(posts===1&&observes===1&&panel.runId===null,'resume observes the job without another POST');
check(panel.completed.prompt==='Resumed validated result.'&&panel.phaseTimer===null,'resumed completion is adopted and stops its clock');
''')

    def test_cached_skip_and_deferred_resume_do_not_invent_analysis_or_stream_a_draft(self):
        self.run_browser(r'''
const panel=editor.wanPanel;let finish,emit;
panel.partialPrompt.value='An older restored draft.';panel.partialSection.hidden=false;
panel.resumeTuning=async(id,signal,onEvent)=>{emit=onEvent;return new Promise(resolve=>{finish=resolve;});};
panel.runId='saved-cache-run';const resumed=panel.resumeRun(panel.runId);
check(panel.phaseStatus.textContent==='Restoring job status…'&&panel.phaseList.children.length===0,'deferred resume has no guessed stage');
emit({type:'phase',phase:'analysis',state:'skipped'});
emit({type:'delta',phase:'tuning',text:'{"prompt":"new half result'});
check(panel.partialPrompt.value==='An older restored draft.','new callbacks never overwrite a recovered legacy draft');
emit({type:'phase',phase:'cache',state:'completed'});finish({...success,cache_hit:true});await resumed;
check([...panel.phaseList.children].map(item=>item.dataset.phase).join(',')==='cache','a cached result does not fabricate analysis, tuning or validation');
check(panel.phaseTimer===null&&panel.progressState==='completed','cache completion clears its timer');
''')

    def test_cancel_and_detach_cleanup_timers_without_losing_products_or_remote_resume(self):
        self.run_browser(r'''
const panel=editor.wanPanel;panel.setResult(success,{progress:false});
const running=panel.execute();await tick();check(panel.phaseTimer!==null,'an active job owns a waiting timer');
editor.closeButton.click();check(panel.phaseTimer!==null&&!runSignal.aborted,'hiding keeps the job and its real waiting state active');
panel.cancelRequest();check(panel.progressState==='cancelled'&&panel.phaseTimer===null&&runSignal.aborted,'cancel is visible and releases the timer');
check(panel.completed.prompt===success.prompt&&!panel.copyPrompt.disabled,'cancel preserves the old product');
resolveRun(success);await running;
check(panel.progressState==='cancelled','a late cancelled response cannot turn the view into success');
editor.open('wan');await tick();const next=panel.execute();await tick();editor.dispose();
check(panel.phaseTimer===null&&runSignal.reason.name==='TuningViewDetached','teardown clears clocks while detaching accepted work');
check(readTuningDraft('generic-test-node').runId==='test-run','detaching keeps the ID for refresh recovery');
resolveRun(success);await next;
''')

    def test_external_node_execution_only_shows_queued_until_its_real_result_arrives(self):
        self.run_browser(r'''
const panel=editor.wanPanel;let nodeQueues=0;
panel.externalRun=async()=>{nodeQueues++;};panel.readOnlyApply=true;
await panel.execute();
check(nodeQueues===1&&starts===0,'node details use partial node execution rather than a producer HTTP request');
check(panel.progressState==='queued'&&panel.phaseTimer!==null,'queue waiting has a real elapsed clock');
check(!panel.phaseList.querySelector('[data-phase="analysis"]')&&!panel.phaseList.querySelector('[data-phase="tuning"]'),'node queueing never pretends to know model phases');
panel.setResult(success);
check(panel.progressState==='completed'&&panel.phaseTimer===null&&panel.apply.hidden,'actual node result completes the read-only details view');
panel.externalRun=async()=>{panel.failProgress(Object.assign(new Error('Node failed before queue acknowledgement.'),{phase:'tuning'}));};
await panel.execute();
check(panel.progressState==='failed'&&panel.phaseTimer===null,'a websocket error received before queue acknowledgement cannot be reset to queued');
''')


if __name__ == "__main__":
    unittest.main()
