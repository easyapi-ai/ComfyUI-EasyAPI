// Kept as .mjs so ComfyUI does not execute this worklet as an extension.
class EasyAPICapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(Math.round(sampleRate * 0.02));
    this.used = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel?.length) {
      for (const sample of channel) {
        this.frame[this.used++] = sample;
        if (this.used === this.frame.length) {
          this.port.postMessage(this.frame.slice());
          this.used = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("easyapi-capture", EasyAPICapture);
