import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const sheet = document.createElement("link");
sheet.rel = "stylesheet";
sheet.href = new URL("./easyapi.css", import.meta.url).href;
document.head.append(sheet);
const panels = new Set();
let nonce = "";
const el = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value !== undefined && value !== null) node[key] = value;
  }
  for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
};
const button = (text, action, primary = false) => el("button", { type: "button", class: primary ? "ea-primary" : "", onclick: action }, text);
const field = (text, input) => el("label", {}, el("span", {}, text), input);
const widget = (node, name) => node.widgets?.find((w) => w.name === name);
const value = (node, name, fallback = "") => widget(node, name)?.value ?? fallback;
function setValue(node, name, next) {
  const target = widget(node, name);
  if (!target) return;
  target.value = next;
  target.callback?.(next);
  node.graph?.setDirtyCanvas(true, true);
}
function objectValue(text) {
  const parsed = JSON.parse(text || "{}");
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("高级参数必须是 JSON 对象");
  return parsed;
}
async function jsonRequest(path, options = {}) {
  const response = await api.fetchApi(path, options);
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try { const parsed = JSON.parse(body); message = parsed.error?.message || parsed.error || parsed.message || body; } catch {}
    throw new Error(String(message || `HTTP ${response.status}`).slice(0, 350));
  }
  return response.json();
}
async function bootstrap() {
  // The backend may have restarted while the workflow and this module stayed open.
  nonce = (await jsonRequest("/easyapi/bootstrap")).management_nonce;
  if (!nonce) throw new Error("未获取连接管理凭据，请刷新页面");
  return nonce;
}
function listOf(data, key) {
  return Array.isArray(data) ? data : (Array.isArray(data?.[key]) ? data[key] : Array.isArray(data?.data) ? data.data : []);
}
function familyFor(node) {
  const type = node.comfyClass || node.type || "";
  if (/NativeRequest/.test(type)) return "";
  if (/Realtime|Speech|Transcribe/.test(type)) return "audio";
  if (/Image|Tools/.test(type)) return "image";
  if (/Video/.test(type)) return "video";
  if (/Embedding/.test(type)) return "embedding";
  if (/Rerank/.test(type)) return "rerank";
  if (/Suno/.test(type)) return "suno";
  if (/Midjourney/.test(type)) return "midjourney";
  return "text";
}

async function openProfiles() {
  const dialog = el("dialog", { class: "easyapi-dialog" });
  const notice = el("p", { class: "ea-error", role: "status" });
  const id = el("input", { placeholder: "例如 main", required: true, pattern: "[A-Za-z0-9_-]+" });
  const name = el("input", { placeholder: "我的 EasyAPI" });
  const base = el("input", { type: "url", placeholder: "https://你的网关地址", required: true });
  const key = el("input", { type: "password", placeholder: "留空保留已保存的密钥", autocomplete: "new-password" });
  const profileList = el("div", { class: "ea-profile-list" });
  let profiles = [];
  const reset = () => { id.value = ""; name.value = ""; base.value = ""; key.value = ""; id.readOnly = false; };
  const fill = (profile) => {
    id.value = profile.id || profile.profile_id; name.value = profile.name || "";
    base.value = profile.base_url || ""; key.value = ""; id.readOnly = true;
  };
  async function load() {
    profiles = listOf(await jsonRequest("/easyapi/profiles"), "profiles");
    profileList.replaceChildren(...profiles.map((p) => button(p.name || p.id || p.profile_id, () => fill(p))), button("＋ 新建", reset));
  }
  const close = () => { key.value = ""; dialog.close(); dialog.remove(); };
  const save = button("保存连接", async () => {
    notice.textContent = "";
    try {
      if (!id.value.trim() || !base.value.trim()) throw new Error("请填写连接别名和服务地址");
      await jsonRequest(`/easyapi/profiles/${encodeURIComponent(id.value.trim())}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-EasyAPI-Management": await bootstrap() },
        body: JSON.stringify({ name: name.value.trim(), base_url: base.value.trim(), api_key: key.value }),
      });
      key.value = "";
      await load();
      notice.textContent = "连接已保存。密钥仅保存在 ComfyUI 后端。";
      for (const panel of panels) {
        if (String(value(panel.node, "profile_id")) === id.value.trim()) panel.realtime?.disconnect();
        panel.refreshProfiles();
      }
    } catch (error) { notice.textContent = error.message; }
  }, true);
  const remove = button("删除此连接", async () => {
    try {
      if (!id.value) return;
      await jsonRequest(`/easyapi/profiles/${encodeURIComponent(id.value)}`, {
        method: "DELETE", headers: { "X-EasyAPI-Management": await bootstrap() },
      });
      reset(); await load();
      for (const panel of panels) { panel.realtime?.disconnect(); panel.refreshProfiles(); }
    } catch (error) { notice.textContent = error.message; }
  });
  dialog.append(el("form", { onsubmit: (event) => event.preventDefault() },
    el("div", { class: "ea-title" }, el("h2", {}, "EasyAPI 连接设置"), button("关闭", close)),
    el("p", { class: "ea-caption" }, "工作流只保存连接别名。密钥不会写入工作流、图片元数据或浏览器存储。"), profileList,
    field("连接别名", id), field("显示名称", name), field("服务地址", base), field("API 密钥", key), notice,
    el("div", { class: "ea-dialog-actions" }, remove, save)));
  document.body.append(dialog);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
  dialog.showModal();
  try { await load(); } catch (error) { notice.textContent = error.message; }
}

function unwrapSchema(raw) {
  let schema = raw?.schema || raw?.data || raw;
  if (Array.isArray(schema)) schema = schema[0];
  return schema?.schema || schema;
}

class NodePanel {
  constructor(node) {
    this.node = node; this.disposed = false; this.sequence = 0;
    this.root = el("section", { class: "easyapi-panel" });
    this.profile = el("select", { title: "连接配置", onchange: () => {
      this.realtime?.disconnect(); setValue(node, "profile_id", this.profile.value); this.refreshModels(true);
    } });
    this.models = el("select", { title: "可用模型", onchange: () => { if (this.models.value) {
      this.realtime?.disconnect(); setValue(node, "model", this.models.value); this.refreshSchema();
    } } });
    if (!widget(node, "model")) this.models.hidden = true;
    this.status = el("div", { class: "ea-status", role: "status" });
    this.error = el("div", { class: "ea-error", role: "alert" });
    this.schema = el("div", { class: "ea-schema" });
    this.preview = el("pre", { class: "ea-preview" });
    this.root.append(el("div", { class: "ea-title" }, "连接与模型", button("连接设置", () => openProfiles())),
      el("div", { class: "ea-row" }, this.profile, button("刷新模型", () => this.refreshModels(true))),
      this.models, this.status, this.error, this.schema);
    if (widget(node, "task_id")) this.addTaskSelector();
    if (node.comfyClass === "EasyAPIRealtime" || node.type === "EasyAPIRealtime") {
      this.realtime = new RealtimePanel(this);
      this.root.append(this.realtime.root);
    } else {
      this.root.append(el("div", { class: "ea-caption" }, `模型可在上方原生字段手填。专用参数同步到 ${widget(node, "request_body") ? "request_body" : "params_json"}；连接素材优先。`), this.preview);
    }
    panels.add(this);
    for (const name of ["model", "operation", "profile_id"]) {
      const target = widget(node, name);
      if (!target) continue;
      const original = target.callback;
      target.callback = (...args) => {
        original?.apply(target, args);
        if (name === "profile_id") {
          this.profile.value = String(target.value); this.refreshModels();
        } else this.refreshSchema();
      };
    }
    this.refreshProfiles();
  }
  async refreshProfiles() {
    try {
      const profiles = listOf(await jsonRequest("/easyapi/profiles"), "profiles");
      if (this.disposed) return;
      const current = String(value(this.node, "profile_id"));
      this.profile.replaceChildren(el("option", { value: "" }, "选择连接配置"), ...profiles.map((p) =>
        el("option", { value: p.id || p.profile_id }, p.name || p.id || p.profile_id)));
      this.profile.value = current;
      if (!current && profiles.length === 1) {
        setValue(this.node, "profile_id", profiles[0].id || profiles[0].profile_id);
        this.profile.value = String(value(this.node, "profile_id"));
      }
      if (!profiles.length) this.status.textContent = "先添加一个 EasyAPI 连接。";
      await this.refreshModels();
    } catch (error) { if (!this.disposed) this.error.textContent = error.message; }
  }
  async refreshModels(force = false) {
    const profileId = String(value(this.node, "profile_id"));
    if (!profileId || !widget(this.node, "model")) return;
    const sequence = ++this.sequence;
    this.modelAbort?.abort(); this.modelAbort = new AbortController();
    this.status.textContent = "正在读取可用模型…"; this.error.textContent = "";
    try {
      const data = await jsonRequest(`/easyapi/models?${new URLSearchParams({ profile_id: profileId, family: familyFor(this.node), refresh: String(force) })}`,
        { signal: this.modelAbort.signal });
      if (this.disposed || sequence !== this.sequence) return;
      const models = listOf(data, "models").map((m) => typeof m === "string" ? { id: m } : m);
      const current = String(value(this.node, "model"));
      this.models.replaceChildren(el("option", { value: "" }, "选择模型（也可手填）"), ...models.map((m) =>
        el("option", { value: m.id || m.name }, m.display_name || m.name || m.id)));
      this.models.value = current;
      this.status.textContent = `${models.length} 个可用模型${current && !models.some((m) => (m.id || m.name) === current) ? " · 已保存模型不在列表中，请确认权限" : ""}`;
      await this.refreshSchema();
    } catch (error) { if (error.name !== "AbortError" && sequence === this.sequence && !this.disposed) {
      this.status.textContent = "可手填模型继续使用"; this.error.textContent = error.message;
    } }
  }
  async refreshSchema() {
    const profileId = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
    if (!profileId || !model || !widget(this.node, "params_json")) return;
    this.schemaAbort?.abort(); this.schemaAbort = new AbortController();
    const selected = `${profileId}:${model}:${value(this.node, "operation", "realtime")}`;
    this.selectedSchema = selected;
    try {
      const raw = await jsonRequest(`/easyapi/schema?${new URLSearchParams({ profile_id: profileId, model,
        operation: String(value(this.node, "operation", "realtime")) })}`, { signal: this.schemaAbort.signal });
      if (this.disposed || selected !== this.selectedSchema) return;
      let spec = unwrapSchema(raw);
      const endpoint = spec?.endpoint_params?.[value(this.node, "operation", "realtime")];
      if (endpoint) spec = endpoint;
      const properties = spec?.properties || {};
      const targetField = widget(this.node, "request_body") ? "request_body" : "params_json";
      let params;
      try { params = objectValue(value(this.node, targetField, "{}")); } catch { return; }
      const controls = [];
      for (const [key, definition] of Object.entries(properties)) {
        if (/^(model|prompt|messages|contents|input|stream|api_key|authorization|token)$/i.test(key) || !definition || controls.length >= 18) continue;
        const initial = params[key] ?? definition.default;
        let input;
        const options = definition.enum || definition.options;
        if (Array.isArray(options)) {
          input = el("select", {}, el("option", { value: "" }, "使用默认值"), ...options.map((v) => el("option", { value: JSON.stringify(v) }, String(v))));
          input.value = initial === undefined ? "" : JSON.stringify(initial);
        } else if (definition.type === "boolean") input = el("input", { type: "checkbox", checked: Boolean(initial) });
        else if (["integer", "number"].includes(definition.type)) input = el("input", { type: "number", value: initial ?? "",
          min: definition.minimum, max: definition.maximum, step: definition.type === "integer" ? 1 : "any" });
        else if (["object", "array"].includes(definition.type)) input = el("textarea", { value: initial === undefined ? "" : JSON.stringify(initial), placeholder: definition.type === "array" ? "[]" : "{}" });
        else input = el("input", { value: initial ?? "", placeholder: definition.description || "使用默认值" });
        input.addEventListener("change", () => {
          try {
            const next = objectValue(value(this.node, targetField, "{}"));
            let changed = input.type === "checkbox" ? input.checked : input.value;
            if (changed === "") delete next[key];
            else {
              if (Array.isArray(options) || ["object", "array"].includes(definition.type)) changed = JSON.parse(changed);
              else if (["integer", "number"].includes(definition.type)) changed = Number(changed);
              next[key] = changed;
            }
            setValue(this.node, targetField, JSON.stringify(next, null, 2)); this.error.textContent = "";
          } catch (error) { this.error.textContent = error.message; }
        });
        const wrapper = field(definition.title || key, input); wrapper.title = definition.description || key;
        if (input.tagName === "TEXTAREA") wrapper.className = "ea-wide";
        controls.push(wrapper);
      }
      this.schema.replaceChildren(...controls);
    } catch (error) { if (error.name !== "AbortError" && !this.disposed) this.schema.replaceChildren(); }
  }
  addTaskSelector() {
    const select = el("select", { title: "已保存任务", onchange: () => {
      const handle = this.savedTasks?.find((t) => t.task_id === select.value);
      if (!handle) return;
      setValue(this.node, "task_id", handle.task_id);
      if (handle.profile_id) setValue(this.node, "profile_id", handle.profile_id);
      if (handle.operation) setValue(this.node, "operation", handle.operation);
    } });
    const refresh = async () => {
      try { this.savedTasks = listOf(await jsonRequest("/easyapi/tasks"), "tasks");
        if (!this.disposed) select.replaceChildren(el("option", { value: "" }, "选择已保存任务"), ...this.savedTasks.map((t) =>
          el("option", { value: t.task_id }, `${t.task_id} · ${t.state || "submitted"}`)));
      } catch (error) { this.error.textContent = error.message; }
    };
    this.root.append(el("div", { class: "ea-row" }, select, button("刷新任务", refresh))); refresh();
  }
  dispose() {
    this.disposed = true; this.modelAbort?.abort(); this.schemaAbort?.abort();
    this.realtime?.dispose(); panels.delete(this); this.root.remove();
  }
}

class PreviewPanel {
  constructor() {
    this.root = el("section", { class: "easyapi-panel" });
    this.preview = el("pre", { class: "ea-preview" });
    this.media = el("div", { class: "ea-row" });
    this.root.append(this.preview, this.media);
  }
  update(message) {
    this.preview.textContent = (message?.easyapi_text || message?.text || []).join("\n").slice(0, 24000);
    const descriptors = message?.easyapi_media || [];
    this.media.replaceChildren(...descriptors.slice(0, 12).map((item) => {
      const relative = typeof api.apiURL === "function" && item.url.startsWith("/") ? api.apiURL(item.url) : item.url;
      if (item.kind === "video") return el("video", { src: relative, controls: true, preload: "metadata", style: "max-width:100%;max-height:240px" });
      if (item.kind === "audio") return el("audio", { src: relative, controls: true, preload: "metadata" });
      if (item.kind === "image") return el("img", { src: relative, alt: "生成结果", style: "max-width:100%;max-height:240px" });
      return el("a", { href: relative, target: "_blank", rel: "noopener" }, "打开结果文件");
    }));
  }
  dispose() { this.media.querySelectorAll("audio,video").forEach((item) => { item.pause(); item.removeAttribute("src"); item.load(); }); this.root.remove(); }
}

function pcm16Base64(floatData, inputRate, outputRate, state) {
  const merged = new Float32Array(state.tail.length + floatData.length);
  merged.set(state.tail); merged.set(floatData, state.tail.length);
  const step = inputRate / outputRate;
  const samples = [];
  let cursor = state.position;
  while (cursor + 1 < merged.length) {
    const index = Math.floor(cursor), fraction = cursor - index;
    const sample = merged[index] * (1 - fraction) + merged[index + 1] * fraction;
    samples.push(Math.round(Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767)));
    cursor += step;
  }
  const consumed = Math.min(Math.floor(cursor), merged.length);
  state.tail = merged.slice(consumed); state.position = cursor - consumed;
  const bytes = new Uint8Array(samples.length * 2), view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  let binary = "";
  for (let index = 0; index < bytes.length; index++) binary += String.fromCharCode(bytes[index]);
  return btoa(binary);
}

class RealtimePanel {
  constructor(panel) {
    this.panel = panel; this.node = panel.node; this.epoch = 0; this.sources = new Set();
    this.nextPlayTime = 0; this.waiting = false; this.muted = false; this.turns = []; this.draft = "";
    this.root = el("div", { class: "ea-realtime" });
    this.state = el("span", {}, "未连接"); this.dot = el("i", { class: "ea-live" });
    this.meter = el("div", { class: "ea-meter" }, el("i"));
    this.muteButton = button("静音", () => this.toggleMute()); this.muteButton.disabled = true;
    this.start = button("开始语音", () => this.connect(true), true);
    this.transcript = el("div", { class: "ea-transcript", role: "log", ariaLive: "polite" }, "在浏览器允许麦克风后开始实时对话。");
    this.text = el("input", { placeholder: "也可输入文字…", onkeydown: (event) => { if (event.key === "Enter") this.sendText(); } });
    const snapshots = el("select", { title: "恢复已冻结的对话", onchange: () => {
      if (snapshots.value) { setValue(this.node, "snapshot_id", snapshots.value); this.panel.status.textContent = "已恢复冻结对话，可执行工作流读取。"; }
    } });
    const refreshSnapshots = async () => {
      try {
        const data = await jsonRequest(`/easyapi/snapshots?${new URLSearchParams({ profile_id: String(value(this.node, "profile_id")) })}`,
          { headers: { "X-EasyAPI-Management": await bootstrap() } });
        snapshots.replaceChildren(el("option", { value: "" }, "选择已保存对话"), ...listOf(data, "snapshots").map((s) =>
          el("option", { value: s.snapshot_id }, `${new Date(s.created_at * 1000).toLocaleString()} · ${s.model}`)));
      } catch (error) { panel.error.textContent = error.message; }
    };
    this.root.append(el("div", { class: "ea-title" }, el("span", {}, this.dot, this.state)),
      el("div", { class: "ea-row" }, this.start, button("文字连接", () => this.connect(false)), this.muteButton, button("打断", () => this.interrupt())),
      this.meter, this.transcript, el("div", { class: "ea-row" }, this.text, button("发送", () => this.sendText())),
      el("div", { class: "ea-row" }, button("冻结本轮", () => this.freeze(false)), button("结束并应用", () => this.freeze(true))),
      el("div", { class: "ea-row" }, snapshots, button("恢复记录", refreshSnapshots)),
      el("div", { class: "ea-caption" }, "执行工作流读取已冻结的文本和音频。声纹/音色等参数在 params_json 中配置。"));
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.start.disabled = true;
      panel.error.textContent = "此页面无法访问麦克风。请通过可信 HTTPS 或 localhost 打开 ComfyUI；私网 HTTP 不支持语音采集。";
    }
  }
  send(event) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.socket.bufferedAmount > 512 * 1024) {
      this.panel.error.textContent = "网络发送积压，已停止会话以避免音频延迟持续增加。"; this.disconnect(); return false;
    }
    this.socket.send(JSON.stringify(event)); return true;
  }
  async audioContext() {
    if (!this.context || this.context.state === "closed") this.context = new AudioContext();
    await this.context.resume(); return this.context;
  }
  sessionConfig(params) {
    const { session = {}, voice, instructions, input_audio_format, output_audio_format } = params;
    const allowed = new Set(["modalities", "voice", "instructions", "speed", "speech_rate", "turn_detection",
      "input_audio_transcription", "input_audio_noise_reduction", "max_response_output_tokens", "temperature",
      "tools", "tool_choice", "max_history_turns", "enable_search", "audio", "include", "tracing", "reasoning"]);
    const top = Object.fromEntries(Object.entries(params).filter(([key]) => allowed.has(key)));
    // A complete native session (for example the newer nested audio protocol)
    // must not acquire incompatible legacy input_audio_format fields.
    if (session.audio || session.type || top.audio) return { ...top, ...session };
    const result = { modalities: ["text", "audio"], input_audio_format: input_audio_format || "pcm16",
      output_audio_format: output_audio_format || "pcm16",
      turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 700 },
      ...(voice ? { voice } : {}), ...(instructions ? { instructions } : {}), ...top, ...session };
    if (typeof result.turn_detection === "string") result.turn_detection = ["none", "manual", "off", "null", ""].includes(result.turn_detection)
      ? null : { type: result.turn_detection };
    if (result.turn_detection === false) result.turn_detection = null;
    return result;
  }
  async connect(withMic) {
    this.disconnect(); const epoch = ++this.epoch;
    this.panel.error.textContent = "";
    try {
      const params = objectValue(value(this.node, "params_json", "{}"));
      const profileId = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
      if (!profileId || !model) throw new Error("请先选择连接配置和实时模型");
      this.inputRate = Number(params.input_sample_rate || (params.input_audio_format === "pcm" ? 16000 : 24000));
      this.outputRate = Number(params.output_sample_rate || 24000);
      if (![8000, 16000, 24000, 44100, 48000].includes(this.inputRate) || ![8000, 16000, 22050, 24000, 44100, 48000].includes(this.outputRate)) throw new Error("不支持的 PCM 采样率");
      const config = this.sessionConfig(params);
      const inputFormat = config.audio?.input?.format || config.input_audio_format;
      const outputFormat = config.audio?.output?.format || config.output_audio_format;
      const isPcm = (format) => ["pcm", "pcm16", "audio/pcm"].includes(typeof format === "object" ? format?.type : format);
      if (!isPcm(outputFormat) || !isPcm(inputFormat)) throw new Error("实时麦克风与播放当前使用 PCM16；请将音频格式设为 pcm16、pcm 或原生 audio/pcm");
      if (inputFormat?.rate) this.inputRate = Number(inputFormat.rate);
      if (outputFormat?.rate) this.outputRate = Number(outputFormat.rate);
      const ctx = await this.audioContext();
      if (withMic) {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("语音采集需要可信 HTTPS 或 localhost 页面");
        const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        if (epoch !== this.epoch) { stream.getTracks().forEach((t) => t.stop()); return; }
        this.stream = stream;
        await ctx.audioWorklet.addModule(new URL("./audio-worklet.mjs", import.meta.url));
        if (epoch !== this.epoch) return;
        this.micSource = ctx.createMediaStreamSource(stream);
        this.processor = new AudioWorkletNode(ctx, "easyapi-capture");
        this.silentGain = ctx.createGain(); this.silentGain.gain.value = 0;
        const resampler = { tail: new Float32Array(0), position: 0 };
        this.processor.port.onmessage = ({ data }) => {
          if (epoch !== this.epoch || this.muted || !this.started) return;
          const samples = new Float32Array(data);
          let peak = 0; for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
          this.meter.style.setProperty("--level", String(Math.min(1, peak * 4)));
          this.send({ type: "input_audio_buffer.append", audio: pcm16Base64(samples, ctx.sampleRate, this.inputRate, resampler) });
        };
        this.micSource.connect(this.processor); this.processor.connect(this.silentGain); this.silentGain.connect(ctx.destination);
      }
      const token = await bootstrap(); if (epoch !== this.epoch) return;
      const relative = typeof api.apiURL === "function" ? api.apiURL("/easyapi/realtime") : "/easyapi/realtime";
      const url = new URL(relative, location.href); url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      url.search = new URLSearchParams({ profile_id: profileId, model, nonce: token }).toString();
      this.socket = new WebSocket(url); this.state.textContent = "连接中…";
      this.turns = []; this.draft = ""; this.renderTranscript();
      this.socket.addEventListener("message", ({ data }) => {
        if (epoch !== this.epoch) return;
        try { const event = JSON.parse(String(data));
          if (event.type === "easyapi.connected") {
            const session = JSON.parse(JSON.stringify(config));
            if (!withMic) { if (session.audio?.input) session.audio.input.turn_detection = null; else session.turn_detection = null; }
            this.send({ type: "easyapi.start", session, output_sample_rate: this.outputRate });
          }
          this.handle(event);
        } catch (error) { this.panel.error.textContent = "无法读取实时消息：" + error.message; }
      });
      this.socket.addEventListener("error", () => { if (epoch === this.epoch) this.panel.error.textContent = "实时连接失败，请检查后端服务和凭据。"; });
      this.socket.addEventListener("close", () => { if (epoch === this.epoch) { this.state.textContent = "已断开"; this.disconnect(); } });
    } catch (error) { if (epoch === this.epoch) { this.disconnect(); this.panel.error.textContent = error.message; } }
  }
  handle(event) {
    const type = String(event.type || "");
    if (type === "easyapi.started") { this.started = true; this.state.textContent = this.stream ? "麦克风已开启" : "文字会话已连接"; this.dot.dataset.live = "true"; this.muteButton.disabled = !this.stream; }
    else if (type === "easyapi.snapshot") {
      setValue(this.node, "snapshot_id", event.snapshot_id);
      this.panel.status.textContent = `已冻结对话 · 第 ${event.revision} 份快照${event.audio_truncated ? " · 音频达到保留上限" : ""}`;
      if (this.endAfterFreeze) { this.send({ type: "easyapi.stop" }); this.disconnect(); }
    } else if (["easyapi.error", "easyapi.expired", "error"].includes(type)) {
      const message = event.error?.message || event.message || "实时服务错误";
      if (!/no active response/i.test(message)) this.panel.error.textContent = message;
      if (type === "easyapi.expired") this.disconnect();
    } else if (type === "response.created") { this.waiting = true; this.draft = ""; }
    else if (["response.audio.delta", "response.output_audio.delta"].includes(type)) this.enqueueAudio(event.delta || event.audio || "");
    else if (["response.audio_transcript.delta", "response.output_audio_transcript.delta", "response.text.delta", "response.output_text.delta"].includes(type)) { this.draft += event.delta || ""; this.renderTranscript(); }
    else if (["response.audio_transcript.done", "response.output_audio_transcript.done", "response.text.done", "response.output_text.done"].includes(type)) {
      const text = event.transcript || event.text || this.draft;
      if (text) this.turns.push("助手：" + text); this.draft = ""; this.renderTranscript();
    } else if (type === "conversation.item.input_audio_transcription.completed") { if (event.transcript) this.turns.push("用户：" + event.transcript); this.renderTranscript(); }
    else if (type === "input_audio_buffer.speech_started") this.interrupt();
    else if (type === "response.done") this.waiting = false;
    else if (type === "easyapi.interrupted") this.clearPlayback();
  }
  renderTranscript() {
    this.turns = this.turns.slice(-200);
    this.transcript.textContent = [...this.turns, ...(this.draft ? ["助手：" + this.draft] : [])].join("\n\n") || "等待对话…";
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }
  enqueueAudio(encoded) {
    if (!encoded || !this.context || !this.started) return;
    const ctx = this.context;
    if (this.nextPlayTime - ctx.currentTime > 8) { this.panel.error.textContent = "播放积压超过 8 秒，已打断当前回答。"; this.interrupt(); return; }
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const view = new DataView(bytes.buffer); const samples = new Float32Array(Math.floor(bytes.length / 2));
    for (let index = 0; index < samples.length; index++) samples[index] = view.getInt16(index * 2, true) / 32768;
    const buffer = ctx.createBuffer(1, samples.length, this.outputRate); buffer.copyToChannel(samples, 0);
    const source = ctx.createBufferSource(); source.buffer = buffer; source.connect(ctx.destination);
    const at = Math.max(ctx.currentTime, this.nextPlayTime); this.nextPlayTime = at + buffer.duration;
    this.sources.add(source); source.addEventListener("ended", () => this.sources.delete(source)); source.start(at);
  }
  clearPlayback() { for (const source of this.sources) { try { source.stop(); } catch {} } this.sources.clear(); this.nextPlayTime = 0; }
  interrupt() { if (this.waiting) this.send({ type: "easyapi.interrupt" }); this.waiting = false; this.clearPlayback(); }
  toggleMute() { this.muted = !this.muted; this.stream?.getTracks().forEach((track) => { track.enabled = !this.muted; }); this.muteButton.textContent = this.muted ? "取消静音" : "静音"; }
  sendText() {
    const text = this.text.value.trim(); if (!text || !this.started) return;
    if (this.send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } })) {
      this.turns.push("用户：" + text); this.renderTranscript(); this.send({ type: "response.create" }); this.text.value = "";
    }
  }
  freeze(end) {
    if (!this.started) { this.panel.error.textContent = "请先连接并开始一次对话"; return; }
    this.endAfterFreeze = end;
    if (end) { this.interrupt(); this.stopMic(); }
    this.send({ type: "easyapi.freeze" });
  }
  stopMic() {
    this.stream?.getTracks().forEach((track) => track.stop()); this.stream = null;
    if (this.processor) this.processor.port.onmessage = null;
    this.processor?.disconnect(); this.micSource?.disconnect(); this.silentGain?.disconnect();
    this.processor = this.micSource = this.silentGain = null; this.meter.style.setProperty("--level", "0");
  }
  disconnect() {
    ++this.epoch; this.started = false; this.waiting = false; this.endAfterFreeze = false;
    this.stopMic(); this.clearPlayback(); this.socket?.close(); this.socket = null;
    this.dot.dataset.live = "false"; this.muteButton.disabled = true; this.muted = false; this.muteButton.textContent = "静音"; this.state.textContent = "未连接";
    if (this.context) { this.context.close().catch(() => {}); this.context = null; }
  }
  dispose() { this.disconnect(); }
}

app.registerExtension({
  name: "EasyAPI.Creator",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "EasyAPIPreview") {
      const created = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (...args) {
        const result = created?.apply(this, args); this.easyapiPanel = new PreviewPanel();
        this.addDOMWidget("easyapi_preview", "easyapi_preview", this.easyapiPanel.root, { serialize: false, getMinHeight: () => 120 });
        const removed = this.onRemoved; this.onRemoved = (...values) => { this.easyapiPanel.dispose(); removed?.apply(this, values); };
        return result;
      };
      const executed = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message, ...args) { executed?.call(this, message, ...args); this.easyapiPanel?.update(message); };
      return;
    }
    if (!nodeData.name?.startsWith("EasyAPI") || !nodeData.input?.required?.profile_id) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const result = created?.apply(this, args);
      const panel = new NodePanel(this); this.easyapiPanel = panel;
      const dom = this.addDOMWidget("easyapi_controls", "easyapi_controls", panel.root,
        { serialize: false, hideOnZoom: false, getMinHeight: () => panel.realtime ? 470 : 250 });
      dom.computeSize = (width) => [Math.max(width || 430, 380), panel.realtime ? 500 : 270];
      const removed = this.onRemoved;
      this.onRemoved = (...values) => { panel.dispose(); removed?.apply(this, values); };
      const configure = this.onConfigure;
      this.onConfigure = (...values) => { configure?.apply(this, values); panel.refreshProfiles(); };
      this.size = [Math.max(this.size?.[0] || 0, 440), this.size?.[1] || 600];
      return result;
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message, ...args) {
      executed?.call(this, message, ...args);
      const text = message?.easyapi_text?.join("\n") || "";
      if (this.easyapiPanel) this.easyapiPanel.preview.textContent = text.slice(0, 12000);
    };
  },
  beforeConfigureGraph() { for (const panel of panels) panel.realtime?.disconnect(); },
});
window.addEventListener("pagehide", () => { for (const panel of panels) panel.realtime?.disconnect(); });
