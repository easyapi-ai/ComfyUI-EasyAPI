"""Exercise real preset viewers and manager callbacks against the shared catalog."""
import ast
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend checks")
class PresetCatalogFrontendTests(unittest.TestCase):
    def run_browser_harness(self, body):
        # Reuse only the fake browser/bootstrap from the broader frontend test.
        # The shipped classes, cache and getProfileManager().onChanged run intact.
        root = Path(__file__).parents[1]
        source = ast.parse((root / "tests/test_form_frontend.py").read_text(encoding="utf-8"))
        method = next(item for item in ast.walk(source) if isinstance(item, ast.FunctionDef)
                      and item.name == "test_selection_drafts_forms_language_and_video_ports")
        script = next(item.value.value for item in method.body if isinstance(item, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "script" for target in item.targets))
        setup = script.split("(async () => {", 1)[0]
        run = subprocess.run([shutil.which("node"), "-", str(root / "web")],
                             input=setup + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exitCode=1;});",
                             encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_preset_viewers_share_refresh_and_clear_schema_when_model_is_removed(self):
        self.run_browser_harness(r'''
let now=1000,catalogCalls=0,defer=false; const resolvers=[],schemaCalls=[];
const rows=ids=>({models:ids.map(id=>({id})),cache_ttl:90});
const catalog=context.createModelCatalog(()=>{
 catalogCalls++;return defer?new Promise(resolve=>resolvers.push(resolve)):rows(['video-model']);
},{now:()=>now,storage:()=>null});
const make=()=>new h.PresetPanel({catalog,mutation:async()=>({}),
 request:async address=>{if(address.startsWith('/easyapi/schema?')){schemaCalls.push(address);return {matched:true,declared:true,request_schema:{properties:{}}};}return {};},
 modelPicker:onSelect=>new h.ModelPicker(onSelect),getOperations:()=>[{id:'video-generation',hasModel:true}]});
const first=make(),second=make();body.append(first.root,second.root);
const profiles=[{id:'p1',model_catalog_revision:'old'}];
first.setProfiles(profiles,'p1');second.setProfiles(profiles,'p1');await tick();
assert.equal(catalogCalls,1,'Two preset viewers share the initial model request');
first.model=second.model='video-model';await Promise.all([first.refreshModels(),second.refreshModels()]);
const initialSchemas=schemaCalls.length;
defer=true;now+=90001;
await Promise.all([first.refreshModels(),second.refreshModels()]);await tick();
assert.equal(catalogCalls,2,'Expired snapshots refresh once for concurrent viewers');
assert.equal(first.picker.input.disabled,false);assert.equal(second.picker.input.disabled,false);
assert.equal(first.models[0].id,'video-model','Previous choices stay available while refreshing');
const beforeRemoval=schemaCalls.length;
resolvers.shift()(rows(['replacement']));await tick();
assert.equal(first.models[0].id,'replacement');assert.equal(second.models[0].id,'replacement');
assert.equal(first.model,'video-model','The saved model is preserved rather than silently substituted');
assert.equal(first.formSchema,null);assert.equal(second.formSchema,null);
assert.equal(first.saveButton.disabled,true);assert.equal(schemaCalls.length,beforeRemoval,'Removed selections must not fetch their schema again');
const scope={profileId:'p1',revision:'old',family:'video',operation:'video-generation',locale:'en'};
const empty=catalog.get({...scope,refresh:true});await tick();resolvers.shift()(rows([]));await empty;await tick();
assert.equal(first.models.length,0);assert.equal(second.models.length,0);
assert.equal(first.picker.input.disabled,true);assert.equal(second.picker.input.disabled,true);
assert.equal(schemaCalls.length,beforeRemoval,'An empty authoritative list never reloads the saved model schema');
await first.refreshModels();assert.equal(schemaCalls.length,beforeRemoval,'Reopening an empty cached list also keeps the schema cleared');
assert.ok(initialSchemas>0);
first.destroy();second.destroy();
''')

    def test_invalidated_load_cannot_write_back_and_latest_revision_recovers(self):
        self.run_browser_harness(r'''
let defer=false;const resolvers=[];
const catalog=context.createModelCatalog(()=>defer?new Promise(resolve=>resolvers.push(resolve)):{models:[{id:'old-model'}],cache_ttl:300},{storage:()=>null});
const panel=new h.PresetPanel({catalog,mutation:async()=>({}),request:async()=>({matched:true,declared:true,request_schema:{properties:{}}}),
 modelPicker:onSelect=>new h.ModelPicker(onSelect),getOperations:()=>[{id:'chat',hasModel:true}]});
body.append(panel.root);panel.setProfiles([{id:'p1',model_catalog_revision:'old'}],'p1');await tick();
assert.equal(panel.models[0].id,'old-model');
defer=true;const older=panel.refreshModels(true);await tick();
catalog.invalidate('p1');await older;
assert.equal(panel.models.length,0);assert.equal(panel.picker.input.disabled,true);
panel.setProfiles([{id:'p1',model_catalog_revision:'new'}],'p1');await tick();
assert.equal(resolvers.length,2,'The new credential revision starts a replacement request');
resolvers[0]({models:[{id:'late-old-model'}],cache_ttl:300});await tick();
assert.equal(panel.models.length,0,'The invalidated old request cannot restore choices');
resolvers[1]({models:[{id:'new-model'}],cache_ttl:300});await tick();
assert.equal(panel.models[0].id,'new-model');assert.equal(panel.picker.input.disabled,false);
assert.equal(catalog.peek({profileId:'p1',revision:'old',family:'text',operation:'chat',locale:'en'}),null);
panel.destroy();
''')

    def test_manager_change_restores_presets_without_any_creative_nodes(self):
        self.run_browser_harness(r'''
// No NodePanel is constructed: the singleton preset viewer must restart its own load.
defaultProfile='p1';h.registeredOperations.set('chat',{id:'chat',hasModel:true});
const manager=h.getProfileManager();body.append(manager.root);
await manager.refresh('p1');await tick();
const preset=manager.presetPanel;assert.equal(preset.profile.value,'p1');
assert.equal(preset.models[0].id,'saved-model');
holdNodeModels=true;fixtures.profiles[0].model_catalog_revision='rotated';
await manager.refresh('p1');await tick();
const obsolete=releaseNodeModels;assert.equal(typeof obsolete,'function');
const changed=manager.onChanged('p1');await tick();
const replacement=releaseNodeModels;
assert.notEqual(replacement,obsolete,'The real manager callback restarts the preset load after invalidation');
obsolete({models:[{id:'obsolete-model'}],cache_ttl:300});await tick();
assert.equal(preset.models.length,0);
replacement({models:[{id:'rotated-model'}],cache_ttl:300});await changed;await tick();
assert.equal(preset.models[0].id,'rotated-model');assert.equal(preset.picker.input.disabled,false);
assert.equal(preset.profiles[0].model_catalog_revision,'rotated');
manager.destroy();
''')


if __name__ == "__main__":
    unittest.main()
