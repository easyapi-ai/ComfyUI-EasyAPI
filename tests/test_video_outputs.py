"""Dedicated video outputs preserve native video, source URL and resumable tasks."""
import base64
import json
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from easyapi_comfy.client import APIResponse
from easyapi_comfy.media import decode_response
from easyapi_comfy.tasks import TaskStore
from test_realtime_nodes import load_nodes


class VideoOutputTests(unittest.IsolatedAsyncioTestCase):
    def test_video_schema_has_three_named_outputs_and_other_nodes_keep_their_contract(self):
        nodes = load_nodes()
        self.assertEqual([(entry.id, entry.display_name) for entry in nodes.EasyAPIVideo.define_schema().outputs],
                         [("video", "视频"), ("url", "URL"), ("response", "响应")])
        self.assertEqual(len(nodes.EasyAPIChat.define_schema().outputs), 8)
        self.assertEqual(len(nodes.EasyAPITaskWait.define_schema().outputs), 8)

    async def test_decode_tracks_first_video_url_instead_of_cover_or_later_video(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded = []

            async def download(url, path):
                downloaded.append(url)
                Path(path).write_bytes(b"fixture")

            client = types.SimpleNamespace(base_url="https://gateway.invalid", download=download)
            cases = [
                ({"image_url": "https://media.invalid/cover.png", "video_url": "/first.mp4",
                  "video_urls": ["https://media.invalid/second.mp4"]}, b"", "", "https://gateway.invalid/first.mp4"),
                ({"video_urls": ["data:video/mp4;base64," + base64.b64encode(b"inline").decode(),
                                 "https://media.invalid/later.mp4"]}, b"", "", ""),
                ({"video_url": "https://media.invalid/later.mp4"}, b"binary", "video/mp4", ""),
            ]
            for data, content, content_type, expected in cases:
                with self.subTest(data=data, binary=bool(content)), \
                        patch("easyapi_comfy.media._native_video", side_effect=lambda path: str(path)), \
                        patch("easyapi_comfy.media._decode_image_files", return_value=(None, None)):
                    decoded = await decode_response(APIResponse(data=data, content=content, content_type=content_type), client, directory)
                    self.assertEqual(decoded["video_url"], expected)
                    self.assertTrue(decoded["video"])
                    self.assertEqual(Path(decoded["video"]).read_bytes(), content or (b"inline" if expected == "" else b"fixture"))

    async def test_video_execute_packs_completed_and_submit_only_results(self):
        nodes = load_nodes()
        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(directory)
            client = types.SimpleNamespace(profile_id="mock", model_schema=AsyncMock(return_value={"declared": False}),
                                           request=AsyncMock(return_value=APIResponse(data={"id": "task-1", "status": "queued"})))
            runtime = types.SimpleNamespace(client=lambda _: client, tasks=task_store, output_dir=Path(directory))
            native_video = object()
            completed = {"data": {"status": "completed", "video_url": "https://media.invalid/result.mp4"}, "usage": {"duration": 12}}
            decode = AsyncMock(return_value={"video": native_video, "video_url": "https://media.invalid/result.mp4", "json": completed})
            wait = AsyncMock(return_value=APIResponse(data=completed))
            with patch.object(nodes, "get_runtime", return_value=runtime), patch.object(nodes, "_interrupt"), \
                    patch.object(nodes, "encode_video_sources", new=AsyncMock(return_value=[])), \
                    patch.object(nodes, "prepare_request", new=AsyncMock(return_value=("/v1/videos", {"duration": 12}, [], {}))), \
                    patch.object(nodes, "validate_request", new=AsyncMock()), \
                    patch.object(nodes, "wait_for_task", new=wait), patch.object(nodes.media, "decode_response", new=decode):
                result = await nodes.EasyAPIVideo.execute(profile_id="mock", model="mock-video", params_json='{"duration":12}')
                self.assertEqual(result.args, (native_video, "https://media.invalid/result.mp4", completed))
                self.assertEqual(result.ui["easyapi_task"][0]["task_id"], "task-1")
                self.assertEqual(client.request.await_count, 1)
                self.assertEqual(wait.await_count, 1)
                self.assertEqual(decode.await_count, 1)

                client.request.reset_mock(); wait.reset_mock(); decode.reset_mock()
                submitted = await nodes.EasyAPIVideo.execute(profile_id="mock", model="mock-video", wait_for_result=False)
                self.assertEqual(submitted.args, (None, "", {"id": "task-1", "status": "queued"}))
                self.assertEqual(submitted.ui["easyapi_task"][0]["task_id"], "task-1")
                self.assertEqual(TaskStore(directory).get("task-1", "mock")["task_id"], "task-1")
                self.assertEqual(client.request.await_count, 1)
                wait.assert_not_awaited(); decode.assert_not_awaited()


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class VideoMigrationTests(unittest.TestCase):
    def test_old_video_outputs_migrate_without_changing_inputs_or_other_nodes(self):
        script = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const ctx={}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8').replace(/^export /gm,'')+'\nglobalThis.migrate=migrateVideoWorkflow;',ctx);
const migrate=ctx.migrate;
function oldNode(id=1) { return {id,type:'EasyAPIVideo',inputs:[{name:'first_frame',link:90}],
  widgets_values:['profile','wan3.0-video','video-generation','prompt','{"duration":12}',true,1800],
  outputs:['STRING','EASYAPI_JSON','IMAGE','MASK','AUDIO','VIDEO','EASYAPI_TASK','EASYAPI_MEDIA'].map((type,i)=>({name:type,type,slot_index:i,links:[10+i]}))}; }
for(const objectLinks of [false,true]) {
 const video=oldNode(), target={id:2,inputs:video.outputs.map((out,i)=>({name:out.name,link:10+i})),outputs:[]};
 const unrelated={id:3,type:'EasyAPIChat',outputs:[{type:'IMAGE',links:[90]}]};
 const saved=JSON.stringify([video.inputs,video.widgets_values,unrelated]);
 const links=video.outputs.map((out,i)=>[10+i,1,i,2,i,out.type]); links.push([90,3,0,1,0,'IMAGE']);
 const graph={nodes:[video,target,unrelated],links:objectLinks?links.map(([id,origin_id,origin_slot,target_id,target_slot,type])=>({id,origin_id,origin_slot,target_id,target_slot,type})):links,
 reroutes:[{linkIds:[10,11,15]}]};
 const report=migrate(graph);
 assert.equal(report.nodes,1); assert.equal(report.disconnected,6);
 assert.deepEqual(Array.from(video.outputs,x=>x.type),['VIDEO','STRING','EASYAPI_JSON']);
 assert.deepEqual(Array.from(video.outputs[0].links),[15]); assert.deepEqual(Array.from(video.outputs[1].links),[]); assert.deepEqual(Array.from(video.outputs[2].links),[11]);
 assert.equal(graph.links.length,3); assert.equal(target.inputs[0].link,null); assert.equal(target.inputs[1].link,11); assert.equal(target.inputs[5].link,15);
 for(const link of graph.links) { const id=objectLinks?link.id:link[0]; if(id===15)assert.equal(objectLinks?link.origin_slot:link[2],0); if(id===11)assert.equal(objectLinks?link.origin_slot:link[2],2); }
 assert.deepEqual(Array.from(graph.reroutes[0].linkIds),[11,15]);
 assert.equal(JSON.stringify([video.inputs,video.widgets_values,unrelated]),saved);
 assert.equal(video.properties.easyapi_video_disconnected,6);
 const stable=JSON.stringify(graph); assert.equal(migrate(graph).nodes,0); assert.equal(JSON.stringify(graph),stable);
}
const sub={definitions:{subgraphs:[{nodes:[oldNode()],links:[]}]},subgraphs:[{nodes:[oldNode(2)],links:[]}]};
assert.equal(migrate(sub).nodes,2);
const modern={type:'EasyAPIVideo',outputs:[{type:'VIDEO',localized_name:'文本',label:'Video'},{type:'STRING',localized_name:'JSON',label:'My URL'},{type:'EASYAPI_JSON',localized_name:'图片',label:'JSON'}]};
migrate({nodes:[modern],links:[]});
assert.deepEqual(Array.from(modern.outputs,x=>x.name),['视频','URL','响应']);
assert.ok(modern.outputs.every(x=>x.localized_name===undefined)); assert.equal(modern.outputs[1].label,'My URL'); assert.equal(modern.outputs[0].label,undefined);
'''
        root = Path(__file__).parents[1]
        result = subprocess.run([shutil.which("node"), "-", str(root / "web/ui-state.js")], input=script,
                                encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_video_port_translations_and_examples_match_new_indices(self):
        root = Path(__file__).parents[1]
        for locale, video, response in [("en", "video", "response"), ("zh", "视频", "响应"), ("zh-TW", "影片", "回應")]:
            data = json.loads((root / "locales" / locale / "nodeDefs.json").read_text(encoding="utf-8"))
            self.assertEqual(data["EasyAPIVideo"]["outputs"], {"0": {"name": video}, "1": {"name": "URL"}, "2": {"name": response}})
        for path in (root / "examples/api").glob("*.json"):
            graph = json.loads(path.read_text(encoding="utf-8"))
            for node in graph.values():
                for value in node.get("inputs", {}).values():
                    if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph and graph[str(value[0])]["class_type"] == "EasyAPIVideo":
                        self.assertIn(value[1], (0, 1, 2), path.name)


if __name__ == "__main__":
    unittest.main()
