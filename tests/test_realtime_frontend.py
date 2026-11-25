"""Run the real frontend's pure audio/session code in Node without network access."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for frontend audio checks")
class FrontendRealtimeTests(unittest.TestCase):
    def test_session_controls_audio_batching_and_resource_cleanup(self):
        source_path = Path(__file__).parents[1] / "web" / "easyapi.js"
        worklet_path = source_path.with_name("audio-worklet.mjs")
        catalog_path = source_path.with_name("model-catalog.js")
        script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const filename = process.argv[1], workletFilename = process.argv[2], catalogFilename = process.argv[3];
let code = fs.readFileSync(filename, 'utf8').replace(/^import .*;\r?\n/gm, '');
code = code.replaceAll('import.meta.url', JSON.stringify(pathToFileURL(filename).href));
const context = { URL, Float32Array, Uint8Array, DataView, Set, Map, console,
  btoa: (v) => Buffer.from(v, 'binary').toString('base64'),
  atob: (v) => Buffer.from(v, 'base64').toString('binary'),
  app: { registerExtension() {} }, api: {},
  document: { createElement: () => ({ dataset: {} }), head: { append() {} } },
  t: (key) => key, textKey: () => null,
  window: { addEventListener() {} }, WebSocket: { OPEN: 1 }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(catalogFilename, 'utf8').replace(/^export /gm, ''), context);
vm.runInContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(filename), 'video-tuning-client.js'), 'utf8').replace(/^export /gm, ''), context);
vm.runInContext(code + '\nglobalThis.testHooks = { RealtimePanel, pcm16Base64 };', context);
const { RealtimePanel, pcm16Base64 } = context.testHooks;
const panel = Object.create(RealtimePanel.prototype);
let config = panel.sessionConfig({ speech_rate: 1.2, max_history_turns: 8, enable_search: true, turn_detection: 'server_vad' });
assert.equal(config.speech_rate, 1.2);
assert.equal(config.max_history_turns, 8);
assert.equal(config.enable_search, true);
assert.equal(config.turn_detection.type, 'server_vad');
assert.equal(panel.sessionConfig({ turn_detection: 'none' }).turn_detection, null);
assert.equal(panel.sessionConfig({ turn_detection: 'server_vad', session: { turn_detection: null } }).turn_detection, null);
const native = panel.sessionConfig({ session: { type: 'realtime', audio: {
  input: { format: { type: 'audio/pcm', rate: 24000 } }, output: { format: { type: 'audio/pcm', rate: 24000 }, voice: 'alloy' }
} } });
assert.equal(native.type, 'realtime'); assert.equal(native.input_audio_format, undefined);
assert.equal(native.audio.output.voice, 'alloy');

const state = { tail: new Float32Array(0), position: 0 };
let outputBytes = 0;
for (let index = 0; index < 100; index++) {
  const encoded = pcm16Base64(new Float32Array(960).fill(.25), 48000, 24000, state);
  const bytes = Buffer.from(encoded, 'base64');
  outputBytes += bytes.length;
  assert.equal(bytes.readInt16LE(0), 8192);
}
assert.equal(outputBytes, 96000); // Two seconds of mono PCM16 at 24 kHz.

let tracksStopped = 0, socketsClosed = 0, playbackStopped = 0, disconnected = 0;
Object.assign(panel, { epoch: 0, started: true, waiting: true, endAfterFreeze: true,
  stream: { getTracks: () => [{ stop() { tracksStopped++; } }, { stop() { tracksStopped++; } }] },
  processor: { port: { onmessage() {} }, disconnect() { disconnected++; } },
  micSource: { disconnect() { disconnected++; } }, silentGain: { disconnect() { disconnected++; } },
  meter: { style: { setProperty() {} } }, sources: new Set([{ stop() { playbackStopped++; } }]),
  socket: { close() { socketsClosed++; } }, dot: { dataset: {} }, muteButton: {}, state: {},
  context: { close: () => Promise.resolve() }
});
panel.disconnect();
assert.equal(tracksStopped, 2); assert.equal(socketsClosed, 1); assert.equal(playbackStopped, 1);
assert.equal(disconnected, 3); assert.equal(panel.started, false); assert.equal(panel.socket, null);

let Processor, packets = [];
const workletContext = { Float32Array, sampleRate: 48000,
  AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (data) => packets.push(data) }; } },
  registerProcessor(name, processor) { assert.equal(name, 'easyapi-capture'); Processor = processor; }
};
vm.createContext(workletContext);
vm.runInContext(fs.readFileSync(workletFilename, 'utf8'), workletContext);
const processor = new Processor();
for (let i = 0; i < 75; i++) processor.process([[new Float32Array(128).fill(.5)]]);
assert.equal(packets.length, 10); // 200 ms becomes ten bounded 20 ms frames.
assert.equal(packets[0].length, 960); assert.equal(packets[0][959], .5);
'''
        result = subprocess.run([shutil.which("node"), "-e", script, str(source_path), str(worklet_path), str(catalog_path)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
