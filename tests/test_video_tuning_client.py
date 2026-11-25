"""Cancellation explicitly stops a job; detaching a view only stops its observer."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed")
class VideoTuningClientTests(unittest.TestCase):
    def test_poll_transport_failure_is_resumable_and_model_timeout_keeps_its_phase(self):
        script = r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createVideoTuningClient}=await import(pathToFileURL(process.argv[1]));
let posts=0,mode='offline';
const transport=createVideoTuningClient({bootstrap:async()=> 'nonce',request:async(path,options)=>{
 if(options.method==='POST'){posts++;return {run_id:'same-run',state:'queued'};}
 if(mode==='offline')throw Object.assign(new Error('temporary read failure'),{status:503});
 if(mode==='missing')throw Object.assign(new Error('not found'),{status:404});
 if(mode==='timeout')return {state:'failed',error:'Analysis timed out after 300 seconds',error_phase:'analysis',error_code:'tuning_timeout',timeout_seconds:300,events:[],cursor:1};
 return {state:'completed',result:{prompt:'complete result'},events:[],cursor:1};
}});
await assert.rejects(transport.run({prompt:'read'}),error=>error.pending===true&&error.runId==='same-run');
mode='complete';assert.equal((await transport.resume('same-run')).prompt,'complete result');
assert.equal(posts,1,'a read failure resumes the existing job without another POST');
mode='timeout';await assert.rejects(transport.resume('same-run'),error=>error.phase==='analysis'&&error.code==='tuning_timeout'&&error.timeoutSeconds===300&&!error.pending);
mode='missing';await assert.rejects(transport.resume('same-run'),error=>!error.pending);
'''
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                 str(Path(__file__).parents[1] / "web/video-tuning-client.js")],
                                capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_upload_keeps_native_multipart_content_type_and_private_management_header(self):
        script = r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createVideoTuningClient}=await import(pathToFileURL(process.argv[1]));
let captured;
const transport=createVideoTuningClient({bootstrap:async()=> 'nonce',request:async(path,options)=>{
 captured={path,options};return {media:{kind:'video',filename:'video.mp4',type:'easyapi_input'}};
}});
const result=await transport.upload(new File(['video'],'diagnostic.mp4',{type:'video/mp4'}));
assert.equal(result.filename,'video.mp4');
assert.equal(captured.path,'/easyapi/video-tuning/media');
assert.equal(captured.options.headers['X-EasyAPI-Management'],'nonce');
assert.equal(captured.options.headers['Content-Type'],undefined,'fetch must add the multipart boundary');
assert.equal(captured.options.body.get('kind'),'video');
assert.equal(captured.options.body.get('file').name,'diagnostic.mp4');
'''
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                 str(Path(__file__).parents[1] / "web/video-tuning-client.js")],
                                capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_cancel_and_detached_view_have_distinct_job_lifetimes(self):
        script = r'''
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createVideoTuningClient}=await import(pathToFileURL(process.argv[1]));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
for(const detach of [false,true]) {
 const calls=[];
 const transport=createVideoTuningClient({bootstrap:async()=> 'nonce',request:async(path,options)=>{
  calls.push({path,options});return {run_id:'run-id',state:options.method==='DELETE'?'cancelled':'queued'};
 }});
 const controller=new AbortController();
 const reason=new Error('stop');if(detach)reason.name='TuningViewDetached';
 const pending=transport.run({prompt:'read'},controller.signal,()=>{},id=>{assert.equal(id,'run-id');controller.abort(reason);});
 await assert.rejects(pending,error=>error===reason);await tick();
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
 assert.equal(calls.filter(call=>call.options.method==='DELETE').length,detach?0:1);
 assert.ok(calls.every(call=>call.options.headers['X-EasyAPI-Management']==='nonce'));
}
'''
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                 str(Path(__file__).parents[1] / "web/video-tuning-client.js")],
                                capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
