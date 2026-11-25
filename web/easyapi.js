import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, textKey, hostLocale, localeQuery } from "./ui-i18n.js";
import { parseParams, draftKey, selectProfile, persistDraft, readDraft, normalizedSchema, requiredDefaults, VIDEO_PORTS, videoPortState, inferImageOperation, migrateImageWorkflow, IMAGE_LAYOUT_VERSION, migrateVideoWorkflow, VIDEO_LAYOUT_VERSION } from "./ui-state.js";
import { makeFieldHelp, renderSchemaForm } from "./ui-forms.js";
import { ProfileManager, EASYAPI_SIDEBAR_ID } from "./profile-panel.js";
import { PresetPanel } from "./preset-panel.js";
import { PromptEditor } from "./prompt-editor.js";
import { createModelCatalog } from "./model-catalog.js";
import { createVideoTuningClient } from "./video-tuning-client.js";
import { installVideoTunerNode } from "./video-tuner-node.js";
import { estimateFacts, NodeBilling } from "./node-billing.js";

const sheet = document.createElement("link");
sheet.rel = "stylesheet";
sheet.href = new URL("./easyapi.css", import.meta.url).href;
document.head.append(sheet);
const panels = new Set();
const registeredOperations = new Map();
const PRESET_NODES = new Set(["EasyAPIChat", "EasyAPIImages", "EasyAPIVideo", "EasyAPISpeech", "EasyAPITranscribe", "EasyAPIRealtime", "EasyAPICountTokens"]);
const translatedText = new Set();
const portRules = new Map();
const portSheet = document.createElement("style");
portSheet.dataset.easyapiPortVisibility = "true";
document.head.append(portSheet);
let bootstrapPending;
// Nodes 2.0 forwards wheel events to the graph during capture, before the form can scroll.
window.addEventListener("wheel", (event) => {
  if (event.ctrlKey || event.metaKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  const target = event.target;
  const panel = target.closest?.(".ea-wan-content,.ea-prompt-popover,.ea-node-panel");
  if (!panel) return;
  if (panel.scrollHeight > panel.clientHeight ||
    (target.tagName === "TEXTAREA" && target.scrollHeight > target.clientHeight)) event.stopPropagation();
}, { capture: true, passive: true });

const el = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value !== undefined && value !== null) {
      const translation = typeof value === "string" && ["placeholder", "title", "ariaLabel", "alt"].includes(key) ? textKey(value) : null;
      node[key] = translation ? t(translation) : value;
      if (translation) translatedText.add({ element: node, attribute: key, key: translation });
    }
  }
  for (const child of children.flat()) {
    if (child instanceof Node) node.append(child);
    else {
      const key = textKey(String(child));
      const text = document.createTextNode(key ? t(key) : String(child));
      node.append(text);
      if (key) translatedText.add({ element: text, key });
    }
  }
  return node;
};
const button = (text, action, primary = false) => el("button", { type: "button", class: primary ? "ea-primary" : "", onclick: action }, text);
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
  try { return parseParams(text); } catch (error) { throw new Error(t(error.message)); }
}
function relabel() {
  for (const entry of translatedText) {
    if (!entry.element.isConnected) { translatedText.delete(entry); continue; }
    if (entry.attribute) entry.element[entry.attribute] = t(entry.key);
    else entry.element.textContent = t(entry.key);
  }
}
async function jsonRequest(path, options = {}) {
  const response = await api.fetchApi(path, options);
  if (!response.ok) {
    const body = await response.text();
    let message = body, code = null;
    try { const parsed = JSON.parse(body); message = parsed.error?.message || parsed.message || parsed.error || body; code = parsed.code || parsed.error?.code || null; } catch {}
    const error = new Error(String(message || `HTTP ${response.status}`).slice(0, 350));
    error.code = code; error.status = response.status; throw error;
  }
  return response.json();
}
const modelCatalog = createModelCatalog(jsonRequest);
const videoTuningClient = createVideoTuningClient({ request: jsonRequest, bootstrap });
let nodeProfilesSnapshot;
function nodeProfiles() {
  if (nodeProfilesSnapshot?.expires > Date.now()) return nodeProfilesSnapshot.request;
  const snapshot = { expires: Infinity };
  nodeProfilesSnapshot = snapshot;
  snapshot.request = jsonRequest("/easyapi/profiles").then((data) => {
    snapshot.expires = Date.now() + 15_000;
    return data;
  }).catch((error) => {
    if (nodeProfilesSnapshot === snapshot) nodeProfilesSnapshot = undefined;
    throw error;
  });
  return snapshot.request;
}
async function bootstrap() {
  // The backend may have restarted while the workflow and this module stayed open.
  bootstrapPending ??= jsonRequest("/easyapi/bootstrap", { method: "POST" }).then(({ management_nonce }) => {
    if (!management_nonce) throw new Error(t("未获取连接管理凭据，请刷新页面"));
    return management_nonce;
  }).finally(() => { bootstrapPending = undefined; });
  return bootstrapPending;
}
async function requestEstimate(payload, signal) {
  const management = await bootstrap();
  signal.throwIfAborted();
  return jsonRequest("/easyapi/billing/estimate", { method: "POST", signal,
    headers: { "X-EasyAPI-Management": management, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}
async function promptModels(profileId, signal, onUpdate, { profiles, refresh = false } = {}) {
  profiles ||= listOf(await nodeProfiles(), "profiles");
  signal?.throwIfAborted();
  const profile = profiles.find((row) => (row.id || row.profile_id) === profileId);
  if (!profile) throw new Error(t("profileUnavailable"));
  const scope = { profileId, revision: profile.model_catalog_revision, family: "text", operation: "chat",
    locale: localeQuery(), purpose: "prompt" };
  if (onUpdate && signal) {
    const unsubscribe = modelCatalog.subscribe(scope, (state) => onUpdate({ ...state,
      models: state.value ? listOf(state.value, "models") : undefined }));
    signal.addEventListener("abort", unsubscribe, { once: true });
  }
  return listOf(await modelCatalog.get({ ...scope, signal, refresh }), "models");
}
async function videoModels(profileId, signal, onUpdate, { profiles, refresh = false } = {}) {
  profiles ||= listOf(await nodeProfiles(), "profiles");
  signal?.throwIfAborted();
  const profile = profiles.find((row) => (row.id || row.profile_id) === profileId);
  if (!profile) throw new Error(t("profileUnavailable"));
  const scope = { profileId, revision: profile.model_catalog_revision, family: "video",
    operation: "video-generation", locale: localeQuery() };
  if (onUpdate && signal) {
    const unsubscribe = modelCatalog.subscribe(scope, state => onUpdate({ ...state,
      models: state.value ? listOf(state.value, "models") : undefined }));
    signal.addEventListener("abort", unsubscribe, { once: true });
  }
  return listOf(await modelCatalog.get({ ...scope, signal, refresh }), "models");
}

function videoTuningScope(node) {
  node.properties ??= {};
  let id = node.properties.easyapi_video_tuning_id;
  const owner = (node.graph?._nodes || []).find(other => other.properties?.easyapi_video_tuning_id === id);
  if (typeof id !== "string" || id.length < 10 || owner && owner !== node) {
    id = globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    node.properties.easyapi_video_tuning_id = id;
  }
  return id;
}

function tunerNodeContext(node) {
  const context = node.easyapiTunerPanel.getContext();
  const media = nodePromptMedia(node, context.params || {});
  for (const kind of ["image", "video", "audio"]) {
    const input = node.inputs?.find(port => port.name === kind);
    if (input?.link != null) media.push(...promptSourceMedia(promptInputNode(node, input), kind,
      "reference_" + kind, "tuner-" + node.id + "-" + kind));
  }
  const diagnostic = node.inputs?.find(port => port.name === "diagnostic_video");
  return { ...context, scopeId: String(value(node, "scope_id")), nonce: Number(value(node, "revision", 0)), media,
    resultMedia: diagnostic?.link != null ? promptSourceMedia(promptInputNode(node, diagnostic), "video", "result_video", "tuner-diagnosis-" + node.id) : [],
    operation: "video-generation" };
}

function showTunerDetails({ node, result }) {
  const panel = node.easyapiTunerPanel;
  if (!node.easyapiTuningEditor) {
    const form = panel.getFormState();
    const editor = node.easyapiTuningEditor = new PromptEditor({
      value: tunerNodeContext(node).prompt, t, getLocale: hostLocale, onInput() {},
      getContext: () => tunerNodeContext(node), loadModels: promptModels,
      transform: transformPrompt, resolveMedia: resolvePromptMedia, videoTuning: videoTuningClient,
      tuningScopeId: () => String(value(node, "scope_id")),
      videoPanelOptions: { readOnlyApply: true, initialResult: result,
        preferences: { profileId: form.profile_id, model: form.text_model, analysisModel: form.analysis_model,
          domain: form.domain, mode: form.mode, output: form.output },
        externalRun: async (settings, retry) => {
          panel.setFormState({ profile_id: settings.profileId, text_model: settings.model,
            analysis_model: settings.analysisModel, domain: settings.domain, mode: settings.mode,
            output: settings.output, analyze: settings.analyze, issue: settings.issue,
            issue_start: settings.start === "" ? -1 : Number(settings.start),
            issue_end: settings.end === "" ? -1 : Number(settings.end) });
          return panel.run(retry);
        } },
    });
    editor.root.style.display = "contents";
    editor.textarea.hidden = editor.promptLabel.hidden = editor.toolbar.hidden = true;
    panel.root.append(editor.root);
  }
  const editor = node.easyapiTuningEditor;
  editor.buttons.wan.disabled = false; editor.open("wan");
  if (result && editor.wanPanel?.setResult) editor.wanPanel.setResult(result, { stale: false });
}
async function transformPrompt(payload, signal, onEvent) {
  const management = await bootstrap();
  signal?.throwIfAborted();
  const options = {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-EasyAPI-Management": management },
    body: JSON.stringify({ ...payload, locale: localeQuery() }),
  };
  if (!payload.stream) return jsonRequest("/easyapi/prompt/transform", options);
  const response = await api.fetchApi("/easyapi/prompt/transform", options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || body.error || `HTTP ${response.status}`);
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream")) return response.json();
  const { readEventStream } = await import("./wan-tuner-core.js");
  return readEventStream(response, onEvent, signal);
}
async function resolvePromptMedia(payload, signal) {
  const management = await bootstrap();
  signal?.throwIfAborted();
  return jsonRequest("/easyapi/prompt/media", {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-EasyAPI-Management": management },
    body: JSON.stringify(payload),
  });
}

function promptFileMedia(path, kind, role, id, defaultType = "input") {
  if (typeof path !== "string" || !path) return null;
  const marker = path.match(/\s+\[(input|output|temp)\]$/);
  const clean = path.replace(/\s+\[(input|output|temp)\]$/, "").replaceAll("\\", "/");
  const slash = clean.lastIndexOf("/");
  const filename = clean.slice(slash + 1), subfolder = slash < 0 ? "" : clean.slice(0, slash);
  const type = marker?.[1] || defaultType;
  const query = new URLSearchParams({ filename, subfolder, type });
  const url = new URL("/view?" + query, globalThis.location?.href || "http://localhost/").href;
  return { id, kind, role, filename, subfolder, type, url, previewUrl: url, ready: true, name: clean };
}

function promptUrlMedia(url, kind, role, id) {
  if (typeof url !== "string" || !url) return null;
  try {
    const page = new URL(globalThis.location?.href || "http://localhost/");
    const absolute = new URL(url, page);
    if (absolute.origin === page.origin && absolute.pathname === "/view" && absolute.searchParams.get("filename")) return promptFileMedia(
      [absolute.searchParams.get("subfolder"), absolute.searchParams.get("filename")].filter(Boolean).join("/"),
      kind, role, id, absolute.searchParams.get("type") || "input");
    if (absolute.origin === page.origin && absolute.pathname.startsWith("/easyapi/files/")) {
      const filename = decodeURIComponent(absolute.pathname.slice("/easyapi/files/".length));
      return { id, kind, role, filename, subfolder: "", type: "easyapi", url: absolute.href,
        previewUrl: absolute.href, ready: true, name: filename };
    }
    if (/^(https?:|data:)$/.test(absolute.protocol)) return { id, kind, role, url: absolute.href,
      previewUrl: absolute.href, ready: true, name: id };
  } catch { /* An unresolved source remains visible as pending media. */ }
  return null;
}

function promptOutputMedia(node, kind, role, id) {
  const output = node?.easyapiPromptOutput || app.nodeOutputs?.[node?.id] || {};
  const records = [...(output.easyapi_media || []), ...(output.images || []), ...(output.audio || [])];
  const entries = records.flatMap((record, index) => {
    const filename = record.filename || "";
    const recordKind = record.kind || record.metadata?.kind || (/\.(mp4|webm|mov)$/i.test(filename) ? "video" :
      /\.(wav|mp3|flac|opus|ogg|m4a)$/i.test(filename) ? "audio" : "image");
    if (kind && recordKind !== kind) return [];
    const media = record.filename ? promptFileMedia([record.subfolder, record.filename].filter(Boolean).join("/"),
      recordKind, role, id + "-" + index, record.type || "output") : promptUrlMedia(record.url, recordKind, role, id + "-" + index);
    return media ? [media] : [];
  });
  const task = output.easyapi_task?.[0]?.latest?.data;
  const taskData = task?.data || task?.output || task;
  const taskUrl = taskData?.download_url || taskData?.video_url || taskData?.output?.video_url;
  if (!entries.length && taskUrl) {
    const media = promptUrlMedia(taskUrl, kind || "video", role, id + "-task-result");
    if (media) entries.push(media);
  }
  if (entries.length || kind === "video") return entries;
  return (node?.imgs || []).flatMap((image, index) => {
    const media = promptUrlMedia(image.currentSrc || image.src, "image", role, id + "-preview-" + index);
    return media ? [media] : [];
  });
}

function promptInputNode(node, input) {
  const link = node.graph?.links?.get?.(input.link) || node.graph?.links?.[input.link];
  return link ? node.graph?.getNodeById?.(link.origin_id) : node.getInputNode?.(node.inputs.indexOf(input));
}

function promptSourceMedia(source, kind, role, id, seen = new Set()) {
  if (!source || seen.has(source.id) || seen.size > 12) return [];
  const pathSeen = new Set(seen);
  pathSeen.add(source.id);
  const type = source.comfyClass || source.type;
  if (["LoadImage", "LoadImageOutput", "LoadVideo", "LoadAudio"].includes(type)) {
    const name = type === "LoadVideo" ? "file" : type === "LoadAudio" ? "audio" : "image";
    const media = promptFileMedia(value(source, name), kind, role, id,
      type === "LoadImageOutput" ? "output" : "input");
    return media ? [media] : [];
  }
  if (type === "EasyAPIMedia") {
    const sourceKind = value(source, "kind", kind), selectedRole = value(source, "role", role);
    const sourceRole = selectedRole && selectedRole !== "reference" ? selectedRole : "reference_" + sourceKind;
    const entries = [];
    for (const name of ["references", "image", "audio", "video"]) {
      const input = source.inputs?.find(port => port.name === name);
      if (input?.link == null) continue;
      const inputKind = name === "references" ? sourceKind : name;
      const inputRole = name === "references" || selectedRole && selectedRole !== "reference"
        ? sourceRole : "reference_" + inputKind;
      entries.push(...promptSourceMedia(promptInputNode(source, input), inputKind, inputRole, id + "-" + name, pathSeen));
    }
    const url = promptUrlMedia(value(source, "url"), sourceKind, sourceRole, id);
    if (url) entries.push(url);
    if (entries.length) return entries;
  }
  const ready = promptOutputMedia(source, kind, role, id);
  return ready.length ? ready : [{ id, kind, role, ready: false, name: source.title || type || id }];
}

function nodePromptMedia(node, params) {
  const media = [];
  for (const name of [...Object.keys(VIDEO_PORTS), "references"]) {
    const input = node.inputs?.find(port => port.name === name);
    if (input?.link == null) continue;
    const role = VIDEO_PORTS[input.name] || "reference_image";
    const kind = role.includes("video") ? "video" : role.includes("audio") ? "audio" : "image";
    media.push(...promptSourceMedia(promptInputNode(node, input), kind, role, "node-" + node.id + "-" + input.name));
  }
  if (!media.length) for (const [index, reference] of (Array.isArray(params.input_references) ? params.input_references : []).entries()) {
    const entry = promptUrlMedia(reference.url, reference.type || "image", reference.role || "reference_image", "param-" + index);
    if (entry) media.push(entry);
  }
  return media;
}

function promptSchemaConstraints(definition) {
  const result = {};
  for (const key of ["type", "enum", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems", "format", "required"]) {
    if (definition[key] !== undefined) result[key] = JSON.parse(JSON.stringify(definition[key]));
  }
  if (definition.items && typeof definition.items === "object") result.items = promptSchemaConstraints(definition.items);
  if (definition.properties && typeof definition.properties === "object") result.properties = Object.fromEntries(
    Object.entries(definition.properties).map(([name, field]) => [name, promptSchemaConstraints(field || {})]));
  return result;
}

function promptSchemaCapabilities(metadata, scenario) {
  if (!metadata || metadata.matched === false || metadata.declared === false) return { known: false };
  const variant = metadata.video?.variants?.[scenario];
  const schema = variant?.request_schema || metadata.request_schema || metadata.schema;
  if (!schema?.properties) return { known: false };
  const result = { known: true, ...promptSchemaConstraints(schema) };
  if (scenario) result.scenario = scenario;
  const roles = variant?.roles || metadata.video?.roles;
  if (Array.isArray(roles)) result.inputRoles = [...roles];
  return result;
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

let profileManager = null;
let managerSidebarRegistered = false;
function getProfileManager() {
  if (!profileManager) profileManager = new ProfileManager({
    request: jsonRequest, bootstrap,
    createPresets: () => new PresetPanel({
      request: jsonRequest, mutation: (path, body, method) => profileManager.mutation(path, body, method),
      catalog: modelCatalog,
      modelPicker: (onSelect) => new ModelPicker(onSelect), getOperations: () => [...registeredOperations.values()],
      onChanged: () => Promise.allSettled([...panels].map((panel) => panel.refreshPresets())),
    }),
    onChanged: async (profileId) => {
      nodeProfilesSnapshot = undefined;
      if (profileId) modelCatalog.invalidate(profileId);
      window.dispatchEvent(new Event("easyapi:profiles"));
      const updates = [...panels].map(async (panel) => {
        if (profileId && String(value(panel.node, "profile_id")) === profileId) panel.realtime?.disconnect();
        await panel.refreshProfiles();
      });
      const presetPanel = profileManager?.presetPanel;
      if (profileId && presetPanel?.profile.value === profileId) updates.push(presetPanel.refreshModels());
      await Promise.allSettled(updates);
    },
  });
  return profileManager;
}
function registerManagerSidebar() {
  if (managerSidebarRegistered || !app.extensionManager?.registerSidebarTab) return;
  app.extensionManager.registerSidebarTab({
    id: EASYAPI_SIDEBAR_ID, icon: "easyapi-sidebar-icon", title: "EasyAPI", tooltip: t("managerTitle"), type: "custom",
    render: (container) => {
      const manager = getProfileManager(); container.replaceChildren(manager.root); manager.refresh(undefined, { refreshBalances: true });
    },
    destroy: () => { profileManager?.destroy(); profileManager = null; },
  });
  managerSidebarRegistered = true;
}
async function openProfiles(profileId = null, section = "connections") {
  registerManagerSidebar();
  const manager = getProfileManager();
  if (profileId) manager.requestedId = profileId;
  manager.showSection(section, profileId);
  const id = "Workspace.ToggleSidebarTab." + EASYAPI_SIDEBAR_ID;
  const command = app.extensionManager?.command?.commands?.find((item) => item.id === id);
  if (!command) { app.extensionManager?.toast?.add?.({ severity: "warn", summary: "EasyAPI", detail: t("sidebarUnavailable") }); return; }
  if (!command.active?.()) await app.extensionManager.command.execute(id);
  if (manager.root.isConnected) await manager.refresh(profileId || undefined, { refreshBalances: section === "connections" });
}

let pickerSequence = 0;
class ModelPicker {
  constructor(onSelect) {
    this.items = []; this.onSelect = onSelect; this.selected = ""; this.active = -1;
    const id = ++pickerSequence;
    this.root = el("div", { class: "ea-model-picker" });
    this.root.dataset.captureWheel = "true";
    this.input = el("input", { id: `easyapi-model-${id}`, role: "combobox", autocomplete: "off", spellcheck: false });
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.setAttribute("aria-expanded", "false");
    // Clicking the native scrollbar may move focus from the input to the list.
    this.list = el("div", { class: "ea-model-list", role: "listbox", id: `easyapi-models-${id}`, tabIndex: -1, hidden: true });
    this.input.setAttribute("aria-controls", this.list.id);
    const reopen = () => {
      if (!this.list.hidden) return;
      this.input.select();
      this.open("");
    };
    this.input.addEventListener("focus", reopen);
    this.input.addEventListener("click", reopen);
    this.input.addEventListener("input", () => this.open(this.input.value));
    this.root.addEventListener("focusout", (event) => {
      if (!this.root.contains(event.relatedTarget)) this.close();
    });
    this.root.addEventListener("keydown", (event) => this.handleKey(event));
    this.list.addEventListener("wheel", (event) => {
      // Leave canvas pinch zoom and horizontal panning available in ComfyUI.
      if (!event.ctrlKey && !event.metaKey && Math.abs(event.deltaY) >= Math.abs(event.deltaX)) event.stopPropagation();
    }, { passive: true });
    this.root.append(this.input, this.list);
    this.translate();
  }
  handleKey(event) {
    if (event.isComposing) return;
    const arrow = event.key === "ArrowDown" || event.key === "ArrowUp";
    if (!arrow && (this.list.hidden || !["Enter", "Escape"].includes(event.key))) return;
    event.preventDefault();
    event.stopPropagation();
    if (arrow) {
      if (this.list.hidden) this.open("");
      if (!this.filtered.length) return;
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const last = this.filtered.length - 1;
      this.active = this.active < 0 ? (direction > 0 ? 0 : last) : Math.max(0, Math.min(last, this.active + direction));
      this.highlight();
    } else if (event.key === "Enter") {
      const exact = this.items.find((item) => item.id === this.input.value.trim());
      const candidate = this.filtered[this.active] || exact || (this.filtered.length === 1 ? this.filtered[0] : null);
      if (candidate) this.choose(candidate);
    } else {
      this.input.focus();
      this.close();
    }
  }
  translate() { this.input.placeholder = t("searchModels"); this.input.setAttribute("aria-label", t("model")); }
  setItems(items, selected) {
    this.items = items.filter((item) => item?.id).map((item) => ({ ...item, id: String(item.id) }));
    this.selected = String(selected || ""); this.close();
  }
  label(item) { return item?.display_name || item?.name || item?.id || ""; }
  close() {
    this.list.hidden = true; this.input.setAttribute("aria-expanded", "false"); this.input.removeAttribute("aria-activedescendant");
    const selected = this.items.find((item) => item.id === this.selected);
    this.input.value = selected && this.label(selected) !== selected.id ? this.label(selected) + " · " + selected.id : this.selected;
  }
  open(term) {
    const query = String(term || "").toLowerCase().trim();
    this.filtered = this.items.filter((item) => `${item.id} ${this.label(item)}`.toLowerCase().includes(query));
    this.active = -1; this.list.replaceChildren();
    this.input.removeAttribute("aria-activedescendant");
    this.filtered.forEach((item, index) => {
      const label = this.label(item);
      const selected = item.id === this.selected;
      const content = el("span", { class: "ea-model-option-content" }, el("span", { class: "ea-model-name" }, label));
      const check = el("span", { class: "ea-model-check", hidden: !selected }, "✓");
      check.setAttribute("aria-hidden", "true");
      const option = el("button", { type: "button", class: "ea-model-option", role: "option", tabIndex: -1, id: this.list.id + "-" + index },
        content, check);
      if (label !== item.id) content.append(el("small", { class: "ea-model-id" }, item.id));
      option.setAttribute("aria-selected", String(selected));
      const capability = item.capability_status;
      if (capability === "unknown" || capability === "inferred") content.append(el("small", { class: "ea-model-capability" }, t(capability === "unknown" ? "unknownCapability" : "inferredCapability")));
      option.addEventListener("mousedown", (event) => event.preventDefault());
      option.addEventListener("click", () => this.choose(item)); this.list.append(option);
    });
    if (!this.filtered.length) this.list.append(el("div", { class: "ea-caption ea-model-empty" }, t("noMatches")));
    this.list.hidden = false; this.input.setAttribute("aria-expanded", "true");
    this.fitList();
    this.list.scrollTop = 0;
    if (!query) this.revealOption(this.list.children[this.filtered.findIndex((item) => item.id === this.selected)]);
  }
  fitList() {
    const panel = this.root.closest(".ea-node-panel");
    if (!panel || !this.root.offsetWidth) return;
    const anchor = this.root.getBoundingClientRect();
    const scale = anchor.width / this.root.offsetWidth;
    if (!scale) return;
    const panelTop = panel.getBoundingClientRect().top + panel.clientTop * scale;
    const top = Math.max(0, panelTop);
    const bottom = Math.min(window.innerHeight, panelTop + panel.clientHeight * scale);
    const above = (anchor.top - top) / scale - 5;
    const below = (bottom - anchor.bottom) / scale - 5;
    const openAbove = below < Math.min(this.list.scrollHeight + 2, 120) && above > below;
    this.list.classList.toggle("ea-model-list-above", openAbove);
    this.list.style.setProperty("--ea-model-list-height", `${Math.max(0, Math.floor(openAbove ? above : below))}px`);
  }
  highlight() {
    [...this.list.children].forEach((item, index) => item.classList.toggle("ea-active", index === this.active));
    const current = this.list.children[this.active];
    if (!current) return;
    this.input.setAttribute("aria-activedescendant", current.id);
    this.revealOption(current);
  }
  revealOption(option) {
    if (!option) return;
    const top = option.offsetTop, bottom = top + option.offsetHeight;
    if (top < this.list.scrollTop) this.list.scrollTop = top;
    else if (bottom > this.list.scrollTop + this.list.clientHeight) this.list.scrollTop = bottom - this.list.clientHeight;
  }
  choose(item) {
    const changed = this.selected !== item.id;
    this.selected = item.id;
    this.input.focus();
    this.close();
    if (changed) this.onSelect(item.id);
  }
}

class NodePanel {
  constructor(node) {
    this.node = node; this.disposed = false; this.sequence = 0; this.schemaSequence = 0; this.mutating = false;
    this.profileSequence = 0;
    this.targetField = widget(node, "request_body") ? "request_body" : "params_json";
    this.root = el("section", { class: "easyapi-panel ea-node-panel" });
    this.content = el("div", { class: "ea-node-content" });
    this.root.append(this.content);
    this.picker = new ModelPicker((id) => this.chooseModel(id));
    this.picker.input.disabled = true;
    this.status = el("div", { class: "ea-status", role: "status" });
    this.error = el("div", { class: "ea-error", role: "alert" });
    this.catalogStatus = el("span", { class: "ea-caption", role: "status" });
    this.retryModels = button(t("retryModels"), () => this.refreshModels(true));
    this.retryModels.hidden = true;
    this.schema = el("div", { class: "ea-schema" });
    this.preview = el("pre", { class: "ea-preview" });
    this.modelInfo = el("span");
    const modelHeading = el("div", { class: "ea-field-name ea-model-heading" },
      el("label", { htmlFor: this.picker.input.id }, t("model")), this.modelInfo);
    this.modelLabel = el("div", { class: "ea-inline-field ea-model-field" }, modelHeading, this.picker.root);
    this.content.append(this.modelLabel, this.status,
      el("div", { class: "ea-model-updates" }, this.catalogStatus, this.retryModels), this.error);
    this.createPrompt();
    this.hasModel = Boolean(widget(node, "model")); this.isVideo = (node.comfyClass || node.type) === "EasyAPIVideo";
    this.isImage = (node.comfyClass || node.type) === "EasyAPIImages";
    if (this.isImage) {
      this.imageMode = el("div", { class: "ea-caption" });
      this.content.append(this.imageMode);
    }
    this.presetEligible = this.hasModel && PRESET_NODES.has(node.comfyClass || node.type);
    this.autoPresetCandidate = this.presetEligible && !String(value(node, "model")) && value(node, this.targetField, "{}").replace(/\s/g, "") === "{}";
    if (!this.hasModel) this.modelLabel.hidden = true;
    if (this.presetEligible) this.createPresetControls();
    if (this.isVideo) this.createVideoControls();
    if (widget(node, "task_id")) this.addTaskSelector();
    this.paramsHint = el("div", { class: "ea-caption", hidden: true }, t("linkedInput"));
    this.content.append(this.schema, this.paramsHint);
    if (this.hasModel) {
      this.billing = new NodeBilling({ request: requestEstimate, t, locale: hostLocale });
      this.modelLabel.append(this.billing.root);
    }
    this.createAdvanced();
    if ((node.comfyClass || node.type) === "EasyAPIRealtime") {
      this.realtime = new RealtimePanel(this); this.content.append(this.realtime.root);
    } else this.content.append(this.preview);
    this.maxPanelHeight = this.realtime ? 640 : 480;
    this.root.style.setProperty("--ea-max-panel-height", this.maxPanelHeight + "px");
    if (typeof ResizeObserver !== "undefined") {
      this.contentObserver = new ResizeObserver(() => this.resize());
      this.contentObserver.observe(this.content);
    }
    this.currentIdentity = this.identity(); this.imageOperation = this.isImage ? this.operationId() : null; this.persistCurrent();
    this.updateImageMode();
    for (const name of ["profile_id", "model", "params_json", "request_body", "mode"]) {
      const target = widget(node, name); if (target) target.hidden = true;
    }
    for (const name of ["timeout", "wait_for_result"]) if (widget(node, name)) widget(node, name).advanced = true;
    if ((this.isVideo || this.isImage) && widget(node, "operation")) widget(node, "operation").hidden = true;
    if (this.isImage) for (const name of ["wait_for_result", "timeout"]) if (widget(node, name)) widget(node, name).hidden = true;
    for (const name of ["profile_id", "model", "operation", "params_json", "request_body", "mode", "prompt"]) {
      const target = widget(node, name); if (!target) continue;
      const original = target.callback;
      target.callback = (...args) => {
        original?.apply(target, args);
        if (this.mutating || app.configuringGraph || this.disposed) return;
        if (name === "prompt") { this.promptEditor?.setValue(target.value); return; }
        if (["profile_id", "model", "operation"].includes(name)) {
          if (name !== "profile_id") this.autoPresetCandidate = false;
          this.transition();
          if (!this.syncImageOperation()) this.refreshModels();
        }
        else if (name === "mode") { this.persistCurrent(); this.refreshSchema(); }
        else { if (!this.syncImageOperation()) { this.persistCurrent(); this.syncAdvanced(); this.renderForm(); } }
      };
    }
    panels.add(this); this.updateBacking(); this.refreshProfiles();
  }
  identity() { return draftKey(value(this.node, "profile_id"), value(this.node, "model"), this.operationId()); }
  updateImageMode() { if (this.imageMode) this.imageMode.textContent = t(this.operationId() === "image-edit" ? "imageAutoEdit" : "imageAutoGeneration"); }
  syncImageOperation() {
    if (!this.isImage) return false;
    const operation = this.operationId();
    this.updateImageMode();
    if (operation === this.imageOperation) return false;
    this.imageOperation = operation;
    // Reference links and image URLs change the route without discarding the
    // prompt, image URL or other parameters that selected that route.
    this.transition(true); this.clearSchema(); this.syncAdvanced();
    this.promptEditor?.contextChanged();
    this.refreshModels();
    return true;
  }
  write(name, next) { this.mutating = true; try { setValue(this.node, name, next); } finally { this.mutating = false; } }
  params() { return objectValue(value(this.node, this.targetField, "{}")); }
  persistCurrent() {
    if (!widget(this.node, this.targetField)) return;
    try { persistDraft(this.node, this.currentIdentity, value(this.node, this.targetField, "{}"), value(this.node, "mode", "auto")); }
    catch { /* Preserve a valid stored draft while an external editor holds incomplete JSON. */ }
  }
  transition(preserve = false) {
    const next = this.identity();
    if (next === this.currentIdentity) return;
    this.persistCurrent();
    const restored = readDraft(this.node, next);
    if (restored && !preserve) {
      this.write(this.targetField, JSON.stringify(restored.params, null, 2)); this.write("mode", restored.mode);
    } else if (!preserve) { this.write(this.targetField, "{}"); this.write("mode", "auto"); }
    this.clearSchema();
    this.currentIdentity = this.isImage ? this.identity() : next; this.updateImageMode();
    this.jsonDirty = false; this.persistCurrent(); this.syncAdvanced();
    if (this.jsonStatus) this.jsonStatus.textContent = "";
  }
  clearSchema() {
    this.schemaAbort?.abort(); ++this.schemaSequence; this.metadata = null; this.formSchema = null; this.schemaState = null;
    this.billing?.update(null);
    this.schema.replaceChildren();
    if (this.isVideo) this.updateVideo();
  }
  configure() {
    // Backend widget values are the authoritative selected draft when loading an old workflow.
    this.wasConfigured = true; this.preserveLoadedParams = true; this.autoPresetCandidate = false;
    this.layoutHeight = undefined;
    this.currentIdentity = this.identity(); this.persistCurrent(); this.jsonDirty = false;
    this.imageOperation = this.isImage ? this.operationId() : null; this.updateImageMode();
    if (this.isImage && this.node.properties?.easyapi_image_disconnected) this.error.textContent = t("imageLinksMigrated", { count: this.node.properties.easyapi_image_disconnected });
    if (this.isVideo && this.node.properties?.easyapi_video_disconnected) this.error.textContent = t("videoLinksMigrated", { count: this.node.properties.easyapi_video_disconnected });
    this.promptEditor?.setValue(value(this.node, "prompt"), { reset: true });
    this.syncAdvanced(); this.updateBacking(); this.resize(); this.refreshProfiles();
  }
  createPrompt() {
    if (!widget(this.node, "prompt")) return;
    this.promptEditor = new PromptEditor({
      value: value(this.node, "prompt"), t, getLocale: hostLocale,
      onInput: (prompt) => {
        this.write("prompt", prompt);
        this.node.graph?.change?.();
        this.refreshBilling();
      },
      getContext: () => this.promptContext(),
      loadModels: promptModels, transform: transformPrompt, resolveMedia: resolvePromptMedia,
      videoTuning: videoTuningClient, tuningScopeId: () => videoTuningScope(this.node),
      historyKey: (create) => this.promptHistoryKey(create),
    });
    this.promptHint = el("div", { class: "ea-caption", hidden: true }, t("linkedInput"));
    this.content.append(this.promptEditor.root, this.promptHint);
  }
  promptContext() {
    let params = {};
    try { params = this.params(); } catch { /* Invalid JSON is still editable in the node. */ }
    const model = this.connected("model") ? "" : String(value(this.node, "model"));
    const mode = String(value(this.node, "mode", "auto"));
    const scenario = this.metadata?.video ? videoPortState(this.node.inputs || [], this.metadata.video, mode).selected : "";
    return {
      profileId: this.connected("profile_id") ? "" : String(value(this.node, "profile_id")),
      modelId: model, targetModel: model, mode,
      params, operation: this.operationId(), media: nodePromptMedia(this.node, params),
      capabilities: promptSchemaCapabilities(this.metadata, scenario),
      resultMedia: promptOutputMedia(this.node, "video", "result_video", "node-result-" + this.node.id),
      profiles: (this.profiles || []).map(profile => {
        const id = profile.id || profile.profile_id;
        return { id, name: profile.name || id, model_catalog_revision: profile.model_catalog_revision };
      }),
    };
  }
  promptHistoryKey(create) {
    const id = this.node.id;
    if (id == null || String(id) === "-1") return "";
    let key = this.node.properties?.easyapi_prompt_history_id;
    if (typeof key !== "string" || !/^[a-z0-9-]{10,64}$/i.test(key)) {
      if (!create) return "";
      key = globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
      this.node.properties ??= {};
      this.node.properties.easyapi_prompt_history_id = key;
      this.node.graph?.change?.();
    }
    return key + "." + id;
  }
  connected(name) { return (this.node.inputs || []).some((input) => input.name === name && input.link != null); }
  updateBacking() {
    const id = String(this.node.id).replace(/["\\]/g, "\\$&");
    const rules = [];
    for (const name of ["profile_id", "model", "params_json", "request_body", "mode", ...((this.isVideo || this.isImage) ? ["operation"] : []), "prompt"]) {
      const target = widget(this.node, name); if (!target) continue;
      const linked = this.connected(name); target.hidden = !linked;
      const index = (this.node.inputs || []).findIndex((input) => input.name === name);
      if (!linked && index >= 0) rules.push(`[data-node-id="${id}"] [data-testid="node-widget"]:has([data-slot-key="${id}-in-${index}"]){display:none!important}`);
    }
    this.backingCSS = rules.join("\n");
    if (this.promptEditor) {
      const linked = this.connected("prompt");
      this.promptEditor.root.hidden = linked;
      this.promptHint.hidden = !linked;
      this.promptEditor.setLinked(linked);
      this.promptEditor.setValue(value(this.node, "prompt"));
    }
    this.modelDriven = this.connected("profile_id") || this.connected("model");
    const paramsDriven = this.connected(this.targetField);
    this.schema.hidden = paramsDriven;
    this.paramsHint.hidden = !paramsDriven;
    if (this.advanced) this.advanced.hidden = paramsDriven;
    if (this.presetControls) this.presetControls.hidden = this.modelDriven || paramsDriven;
    this.writePortCSS();
    this.refreshBilling();
  }
  writePortCSS() { portRules.set(this, (this.backingCSS || "") + "\n" + (this.videoCSS || "")); portSheet.textContent = [...portRules.values()].join("\n"); }
  chooseProfile(id) {
    if (this.connected("profile_id") || !this.profiles?.some((profile) => (profile.id || profile.profile_id) === id) ||
      String(value(this.node, "profile_id")) === id) return;
    this.preserveLoadedParams = false;
    this.realtime?.disconnect(); this.write("profile_id", id); this.transition();
    if (!this.syncImageOperation()) this.refreshModels();
  }
  chooseModel(id) {
    if (String(value(this.node, "model")) === id) return;
    this.preserveLoadedParams = false; this.autoPresetCandidate = false; this.realtime?.disconnect(); this.write("model", id); this.transition();
    if (!this.syncImageOperation()) this.refreshSchema();
    this.updateModelStatus();
  }
  createAdvanced() {
    if (!widget(this.node, this.targetField)) return;
    this.advanced = el("details", { class: "ea-advanced" });
    this.jsonEditor = el("textarea", { rows: 7, spellcheck: false });
    this.jsonEditor.setAttribute("aria-label", t("advancedJson"));
    this.jsonStatus = el("div", { class: "ea-caption", role: "status" });
    this.jsonEditor.addEventListener("input", () => { this.jsonDirty = true; this.jsonStatus.textContent = t("unappliedJson"); });
    const apply = () => {
      try { const parsed = objectValue(this.jsonEditor.value); this.autoPresetCandidate = false; this.jsonDirty = false; this.commitParams(parsed); this.jsonStatus.textContent = t("jsonApplied"); this.renderForm(); }
      catch (error) { this.jsonStatus.textContent = error.message; }
    };
    this.jsonEditor.addEventListener("change", apply);
    this.advanced.append(el("summary", {}, t("advancedJson")), el("p", { class: "ea-caption" }, t("jsonHint")),
      this.jsonEditor, el("div", { class: "ea-row" }, button(t("applyJson"), apply), this.jsonStatus));
    this.advanced.addEventListener("toggle", () => this.resize());
    this.content.append(this.advanced); this.syncAdvanced();
  }
  syncAdvanced() { if (this.jsonEditor && !this.jsonDirty) this.jsonEditor.value = value(this.node, this.targetField, "{}"); }
  commitParams(next) {
    let safe = objectValue(JSON.stringify(next));
    const changesImageRoute = this.isImage && inferImageOperation(this.node.inputs || [], safe) !== this.operationId();
    if (!changesImageRoute && this.metadata?.matched !== false && this.metadata?.declared !== false && this.formSchema) safe = requiredDefaults(this.formSchema, safe);
    this.write(this.targetField, JSON.stringify(safe, null, 2));
    const changedOperation = this.syncImageOperation();
    if (!changedOperation) this.persistCurrent();
    this.syncAdvanced(); this.node.graph?.change?.();
    this.promptEditor?.contextChanged();
    this.refreshBilling();
  }
  async refreshProfiles() {
    const sequence = ++this.profileSequence;
    try {
      const data = await nodeProfiles();
      if (this.disposed || sequence !== this.profileSequence) return;
      this.profiles = listOf(data, "profiles"); this.defaultId = data.default_profile_id || null;
      this.promptEditor?.contextChanged();
      const current = String(value(this.node, "profile_id"));
      const selected = selectProfile(current, this.profiles, this.defaultId);
      if (selected !== current && !this.connected("profile_id")) { this.write("profile_id", selected); this.transition(true); }
      await this.refreshModels();
    } catch (error) { if (!this.disposed && sequence === this.profileSequence) this.error.textContent = error.message; }
  }
  updateModelStatus() {
    const current = String(value(this.node, "model"));
    const found = this.models?.find((model) => model.id === current);
    this.status.textContent = current && !found ? t("missingModel") : "";
    if (!this.models?.length && !current) this.status.textContent = t("noModels");
  }
  async refreshModels(force = false) {
    const sequence = ++this.sequence;
    this.modelAbort?.abort();
    this.modelUnsubscribe?.();
    const profileId = String(value(this.node, "profile_id"));
    this.loadingModels = false;
    this.error.textContent = "";
    this.catalogStatus.textContent = ""; this.retryModels.hidden = true;
    if (this.presetSelect) {
      ++this.presetSequence;
      this.presetSelect.disabled = true;
      this.applyPresetButton.disabled = true;
    }
    const profile = this.profiles?.find((row) => (row.id || row.profile_id) === profileId);
    if (this.modelDriven || !profile) {
      this.models = []; this.picker.input.disabled = true;
      this.picker.setItems([], value(this.node, "model"));
      this.clearSchema();
      this.status.textContent = t(this.modelDriven ? "linkedSelection" : profileId ? "profileUnavailable" :
        this.profiles?.length > 1 ? "nodeDefaultConnectionNeeded" : "nodeConnectionNeeded");
      return;
    }
    if (!this.hasModel) {
      this.status.textContent = "";
      this.clearSchema(); this.syncAdvanced(); return;
    }
    this.modelAbort = new AbortController();
    const scope = { profileId, family: familyFor(this.node), operation: this.operationId(),
      locale: localeQuery(), revision: profile.model_catalog_revision };
    const active = () => !this.disposed && sequence === this.sequence && !this.modelDriven && !this.modelAbort.signal.aborted;
    let applied, initialized = false;
    const loadDetails = async () => {
      const current = String(value(this.node, "model"));
      const available = this.models.some((row) => row.id === current);
      await Promise.all([this.refreshPresets(true), available ? this.refreshSchema(force) : Promise.resolve()]);
      if (active() && !current && value(this.node, "model")) await this.refreshSchema(force);
    };
    const applyModels = (data) => {
      if (!active() || data === applied) return;
      applied = data;
      this.models = listOf(data, "models").map((model) => typeof model === "string" ? { id: model } : { ...model, id: model.id || model.name });
      this.picker.input.disabled = !this.models.length;
      this.picker.setItems(this.models, value(this.node, "model"));
      this.updateModelStatus();
      if (!this.models.some((row) => row.id === String(value(this.node, "model")))) this.clearSchema();
      if (initialized) loadDetails().catch((error) => { if (active()) this.error.textContent = error.message; });
    };
    const cached = modelCatalog.peek(scope);
    if (cached) applyModels(cached.value);
    else {
      this.models = []; this.picker.input.disabled = true;
      this.picker.setItems([], value(this.node, "model")); this.status.textContent = t("loadingModels");
      this.loadingModels = true;
    }
    this.modelUnsubscribe = modelCatalog.subscribe(scope, (state) => {
      if (!active()) return;
      if (state.invalidated) {
        this.modelAbort.abort();
        this.models = []; this.picker.setItems([], value(this.node, "model")); this.picker.input.disabled = true;
        this.clearSchema(); this.status.textContent = t("authorityError");
      } else if (state.value) applyModels(state.value);
      this.loadingModels = state.updating && !state.value;
      this.catalogStatus.textContent = state.error ? t("modelUpdateFailed") : state.updating && state.value ? t("updatingModels") : "";
      this.retryModels.hidden = !state.error;
      if (state.error) this.error.textContent = state.error.message;
      else if (state.value) this.error.textContent = "";
    });
    try {
      const data = await modelCatalog.get({ ...scope, refresh: force, signal: this.modelAbort.signal });
      if (!active()) return;
      applyModels(data); initialized = true;
      await loadDetails();
    } catch (error) {
      if (error.name !== "AbortError" && sequence === this.sequence && !this.disposed) {
        if (!this.models.length) this.status.textContent = t("authorityError");
        this.error.textContent = error.message;
        this.retryModels.hidden = false;
      }
    } finally {
      if (sequence === this.sequence && !this.disposed) this.loadingModels = false;
    }
  }
  async refreshSchema(force = false) {
    const profile = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
    if (!model || !this.hasModel || this.modelDriven ||
      !this.models?.some((row) => row.id === model) ||
      !this.profiles?.some((connection) => (connection.id || connection.profile_id) === profile)) return;
    const sequence = ++this.schemaSequence;
    this.schemaAbort?.abort(); this.schemaAbort = new AbortController();
    const mode = value(this.node, "mode", "auto") || "auto";
    const provisional = this.isVideo ? videoPortState(this.node.inputs || [], this.metadata?.video, mode).selected : "";
    const query = { profile_id: profile, model, operation: this.operationId(), locale: localeQuery() };
    if (force) query.refresh = "1";
    if (provisional) query.variant = provisional;
    if (this.isImage) {
      this.metadata = null; this.formSchema = null; this.schemaState = "loading";
      this.error.textContent = ""; this.billing?.update(null); this.renderForm();
    }
    try {
      const metadata = await jsonRequest(`/easyapi/schema?${new URLSearchParams(query)}`, { signal: this.schemaAbort.signal });
      if (this.disposed || sequence !== this.schemaSequence) return;
      this.metadata = metadata;
      this.schemaState = null;
      let schema = normalizedSchema(metadata);
      if (this.isVideo) {
        const state = this.updateVideo();
        schema = metadata.video?.variants?.[state.selected]?.request_schema || schema;
      }
      this.formSchema = schema;
      if (!this.connected(this.targetField) && !this.preserveLoadedParams && metadata.matched !== false && metadata.declared !== false) {
        const before = this.params(), withDefaults = requiredDefaults(schema, before);
        if (JSON.stringify(before) !== JSON.stringify(withDefaults)) this.commitParams(withDefaults);
      }
      this.renderForm(); this.syncAdvanced();
    } catch (error) {
      if (error.name !== "AbortError" && !this.disposed && sequence === this.schemaSequence) {
        this.metadata = null; this.formSchema = {}; this.schemaState = "error";
        this.renderForm(); this.error.textContent = error.message;
      }
    }
  }
  renderForm() {
    if (this.isImage && !this.modelDriven && (this.schemaState || this.formSchema && !Object.keys(this.formSchema.properties || {}).length)) {
      const unavailable = this.metadata?.source === "unavailable" || this.metadata?.matched !== true;
      const message = this.schemaState === "loading" ? "loadingParameters" : this.schemaState === "error" ? "parameterLoadFailed" : unavailable ? "imageParametersFetchUnavailable" : "imageParametersUnavailable";
      this.schema.replaceChildren(el("p", { class: "ea-caption" }, t(message)));
      this.resize(); this.refreshBilling(); return;
    }
    if (!widget(this.node, this.targetField) || !this.formSchema || this.modelDriven) return;
    renderSchemaForm(this.schema, this.formSchema, () => this.params(), (next) => { this.autoPresetCandidate = false; this.commitParams(next); }, t, () => this.resize());
    this.refreshBilling();
  }
  refreshBilling() {
    if (!this.billing) return;
    const profileId = String(value(this.node, "profile_id"));
    const model = String(value(this.node, "model"));
    const profile = this.profiles?.find((profile) => (profile.id || profile.profile_id) === profileId);
    if (!profile || !model || !this.formSchema || this.modelDriven) { this.billing.update(null); return; }
    const paramsLinked = this.connected(this.targetField);
    let params = {};
    try { if (!paramsLinked) params = this.params(); } catch { this.billing.update(null); return; }
    const facts = estimateFacts({ model, operation: this.operationId(), params, schema: this.formSchema,
      inputs: this.node.inputs || [], prompt: this.connected("prompt") ? undefined : widget(this.node, "prompt")?.value, paramsLinked });
    this.billing.update({ profile_id: profileId, locale: localeQuery(), facts, rates_only: paramsLinked }, profile.model_catalog_revision);
  }
  operationId() {
    if ((this.node.comfyClass || this.node.type) === "EasyAPIImages") {
      let params = {};
      try { if (!this.connected(this.targetField)) params = this.params(); } catch { /* Keep incomplete JSON editable. */ }
      return inferImageOperation(this.node.inputs || [], params);
    }
    return String(value(this.node, "operation", (this.node.comfyClass || this.node.type) === "EasyAPIRealtime" ? "realtime" : ""));
  }
  createPresetControls() {
    this.presetSequence = 0;
    this.presetSelect = el("select", { disabled: true, onchange: () => { this.applyPresetButton.disabled = !this.presetSelect.value; } },
      el("option", { value: "", disabled: true }, t("choosePreset")));
    this.presetSelect.setAttribute("aria-label", t("presetsTab"));
    this.applyPresetButton = button(t("applyPreset"), () => {
      const preset = this.presets?.find((record) => record.id === this.presetSelect.value);
      if (preset) this.applyPresetRecord(preset, false).catch((error) => { this.error.textContent = error.message; });
    }); this.applyPresetButton.disabled = true;
    this.presetControls = el("details", { class: "ea-preset-controls" }, el("summary", {}, t("presetsTab")),
      el("div", { class: "ea-preset-fields" }, this.presetSelect,
        el("div", { class: "ea-row" }, this.applyPresetButton, button(t("saveAsPreset"), async () => {
        try {
          if (this.jsonDirty) throw new Error(t("applyJsonFirst"));
          const profile = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
          if (!profile || !model) throw new Error(t("selectPresetScope"));
          await openProfiles(profile, "presets");
          await getProfileManager().presetPanel.newFromNode({ profile_id: profile, operation: this.operationId(), model, params: this.params() });
        } catch (error) { this.error.textContent = error.message; }
      }))));
    this.presetControls.addEventListener("toggle", () => this.resize());
    this.content.append(this.presetControls);
  }
  async refreshPresets(considerDefault = false) {
    if (!this.presetEligible || this.modelDriven || this.connected(this.targetField)) return;
    const profile = String(value(this.node, "profile_id")), operation = this.operationId();
    if (!profile || !operation) return;
    const sequence = ++this.presetSequence;
    try {
      const data = await jsonRequest(`/easyapi/presets?${new URLSearchParams({ profile_id: profile, operation })}`);
      if (this.disposed || this.presetSequence !== sequence || this.modelDriven || this.connected(this.targetField) ||
        String(value(this.node, "profile_id")) !== profile || this.operationId() !== operation) return;
      this.presets = (data.presets || []).filter((record) => record.profile_id === profile && record.operation === operation);
      const selected = this.presetSelect.value;
      this.presetSelect.replaceChildren(el("option", { value: "", disabled: true, hidden: this.presets.length > 0 }, t("choosePreset")), ...this.presets.map((record) =>
        el("option", { value: record.id }, record.name + " · " + record.model + (data.default_preset_ids?.[operation] === record.id ? " · " + t("defaultPreset") : ""))));
      this.presetSelect.value = this.presets.some((record) => record.id === selected) ? selected : "";
      this.presetSelect.disabled = !this.presets.length;
      this.applyPresetButton.disabled = !this.presetSelect.value;
      if (considerDefault && !this.autoPresetChecked && this.autoPresetCandidate && !this.wasConfigured && !value(this.node, "model") && Object.keys(this.params()).length === 0) {
        this.autoPresetChecked = true;
        const preset = this.presets.find((record) => record.id === data.default_preset_ids?.[operation]);
        if (preset && this.models?.some((model) => model.id === preset.model)) { await this.applyPresetRecord(preset, true); this.presetSelect.value = preset.id; this.applyPresetButton.disabled = false; }
        else if (preset) this.status.textContent = t("defaultPresetUnavailable");
      }
    } catch (error) { if (!this.disposed && this.presetSequence === sequence) this.error.textContent = error.message; }
  }
  async applyPresetRecord(preset, automatic) {
    if (preset.profile_id !== String(value(this.node, "profile_id")) || preset.operation !== this.operationId()) throw new Error(t("presetScopeMismatch"));
    if (!this.models?.some((model) => model.id === preset.model)) throw new Error(t("defaultPresetUnavailable"));
    const params = objectValue(JSON.stringify(preset.params));
    if (automatic && (this.wasConfigured || !this.autoPresetCandidate || value(this.node, "model") || Object.keys(this.params()).length)) return;
    this.realtime?.disconnect(); this.write("model", preset.model); this.transition(); this.write("mode", "auto");
    this.preserveLoadedParams = false;
    this.jsonDirty = false; this.commitParams(params); this.autoPresetCandidate = false; this.picker.setItems(this.models, preset.model);
    this.updateModelStatus(); if (!automatic) await this.refreshSchema();
  }
  createVideoControls() {
    this.videoBox = el("div", { class: "ea-video-inputs", hidden: true });
    this.modeSelect = el("select", { onchange: () => { this.write("mode", this.modeSelect.value); this.persistCurrent(); this.refreshSchema(); } });
    this.modeSelect.setAttribute("aria-label", t("videoMode"));
    this.modeLabel = el("div", { class: "ea-inline-field" }, el("span", {}, t("videoMode")), this.modeSelect);
    this.videoError = el("div", { class: "ea-error", role: "status" });
    this.videoBox.append(this.modeLabel, this.videoError); this.content.append(this.videoBox);
  }
  updateVideo() {
    const inputs = this.node.inputs || [], video = this.metadata?.video || {};
    const selectedMode = value(this.node, "mode", "auto") || "auto";
    const state = videoPortState(inputs, video, selectedMode);
    const scenarios = video.scenarios || [];
    this.modeSelect.replaceChildren(el("option", { value: "auto" }, t("videoAuto")), ...scenarios.map((scenario) =>
      el("option", { value: scenario }, video.variants?.[scenario]?.label || t("scenario." + scenario))));
    if (selectedMode !== "auto" && !scenarios.includes(selectedMode)) this.modeSelect.append(el("option", { value: selectedMode }, t("scenario." + selectedMode)));
    this.modeSelect.value = selectedMode;
    this.modeLabel.hidden = !state.ambiguous && selectedMode === "auto";
    const info = state.known ? t("supportedRoles", { roles: (video.roles || []).map((role) => t("port." + role)).join(" · ") || t("scenario.text-to-video") }) : t("videoUndeclared");
    this.modelInfo.replaceChildren(...(this.metadata ? [makeFieldHelp(info, t("videoInputs")).element] : []));
    const problems = [];
    if (state.invalid.length) problems.push(t("incompatibleReferences", { ports: state.invalid.map((name) => this.portLabel(name)).join(", ") }));
    if (state.selected && scenarios.length && !scenarios.includes(state.selected)) problems.push(t("unsupportedScenario", { mode: t("scenario." + state.selected) }));
    if (state.ambiguous) problems.push(t("videoAmbiguous"));
    this.videoError.textContent = problems.join("\n");
    this.videoBox.hidden = !this.metadata || (this.modeLabel.hidden && !problems.length);
    // Visibility uses the model-wide union: hiding all image ports in the initial
    // text-only inferred state would make switching to image input impossible.
    const unionKnown = Array.isArray(video.roles) && (video.roles.length > 0 || scenarios.length > 0);
    const id = String(this.node.id).replace(/["\\]/g, "\\$&");
    const rules = [];
    inputs.forEach((input, index) => {
      const role = VIDEO_PORTS[input.name]; if (!role) return;
      const unsupported = unionKnown && !video.roles.includes(role);
      const invalid = state.invalid.includes(input.name);
      const label = this.portLabel(input.name);
      input.label = input.localized_name = label + (invalid ? " ⚠" : "");
      input.tooltip = unsupported || invalid ? t("portUnsupported") : label;
      const selector = `[data-node-id="${id}"] .lg-slot--input:has([data-slot-key="${id}-in-${index}"])`;
      if (unsupported && input.link == null) rules.push(selector + "{display:none!important}");
      else if (invalid) rules.push(selector + "{color:#e99081!important;outline:1px solid #e99081;border-radius:4px}");
    });
    this.videoCSS = rules.join("\n"); this.writePortCSS();
    this.node.graph?.setDirtyCanvas(true, true); return state;
  }
  portLabel(name) { const role = VIDEO_PORTS[name] || name; const ordinal = name.match(/_(\d+)$/)?.[1]; return t("port." + role, role) + (ordinal ? " " + ordinal : ""); }
  addTaskSelector() {
    const select = el("select", { title: "已保存任务", onchange: () => {
      const handle = this.savedTasks?.find((task) => JSON.stringify([task.profile_id, task.task_id]) === select.value);
      if (!handle) return;
      this.write("task_id", handle.task_id); if (handle.profile_id) this.write("profile_id", handle.profile_id);
      if (handle.operation && widget(this.node, "operation")?.options?.values?.includes(handle.operation)) this.write("operation", handle.operation);
      this.transition(); this.refreshModels();
    } });
    const refresh = async () => {
      try { this.savedTasks = listOf(await jsonRequest("/easyapi/tasks"), "tasks");
        if (!this.disposed) select.replaceChildren(el("option", { value: "" }, t("选择已保存任务")), ...this.savedTasks.map((task) =>
          el("option", { value: JSON.stringify([task.profile_id, task.task_id]) }, `${task.task_id} · ${task.state || "submitted"}`)));
      } catch (error) { this.error.textContent = error.message; }
    };
    this.content.append(el("div", { class: "ea-row" }, select, button("刷新任务", refresh))); refresh();
  }
  translate() {
    this.updateImageMode();
    this.picker.translate();
    this.promptEditor?.translate(); this.jsonEditor?.setAttribute("aria-label", t("advancedJson"));
    this.modeSelect?.setAttribute("aria-label", t("videoMode"));
    this.realtime?.translate?.(); this.billing?.render(); this.refreshProfiles();
  }
  minHeight() { return Math.min(this.content.offsetHeight || 160, this.maxPanelHeight); }
  resize() {
    if (this.disposed || this.resizing) return;
    this.resizing = true;
    requestAnimationFrame(() => {
      this.resizing = false; if (this.disposed) return;
      const height = this.node.computeSize?.()[1];
      if (height && height !== this.layoutHeight) {
        this.layoutHeight = height;
        this.node.setSize?.([this.node.size?.[0] || 360, height]);
      }
      this.node.graph?.setDirtyCanvas(true, true);
    });
  }
  dispose() {
    this.disposed = true; this.modelAbort?.abort(); this.schemaAbort?.abort(); clearTimeout(this.connectionTimer);
    this.modelUnsubscribe?.();
    this.contentObserver?.disconnect();
    this.billing?.dispose();
    this.promptEditor?.dispose();
    this.realtime?.dispose(); panels.delete(this); portRules.delete(this); portSheet.textContent = [...portRules.values()].join("\n"); this.root.remove();
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
      if (item.kind === "image") return el("img", { src: relative, alt: t("生成结果"), style: "max-width:100%;max-height:240px" });
      return el("a", { href: relative, target: "_blank", rel: "noopener" }, t("打开结果文件"));
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
    this.state = el("span", {}, t("未连接")); this.dot = el("i", { class: "ea-live" });
    this.meter = el("div", { class: "ea-meter" }, el("i"));
    this.muteButton = button(t("静音"), () => this.toggleMute()); this.muteButton.disabled = true;
    this.start = button(t("开始语音"), () => this.connect(true), true);
    this.transcript = el("div", { class: "ea-transcript", role: "log", ariaLive: "polite" }, t("在浏览器允许麦克风后开始实时对话。"));
    this.text = el("input", { placeholder: t("也可输入文字…"), onkeydown: (event) => { if (event.key === "Enter") this.sendText(); } });
    const snapshots = el("select", { title: t("恢复已冻结的对话"), onchange: () => {
      if (snapshots.value) { setValue(this.node, "snapshot_id", snapshots.value); this.panel.status.textContent = t("已恢复冻结对话，可执行工作流读取。"); }
    } });
    const refreshSnapshots = async () => {
      try {
        const data = await jsonRequest(`/easyapi/snapshots?${new URLSearchParams({ profile_id: String(value(this.node, "profile_id")) })}`,
          { headers: { "X-EasyAPI-Management": await bootstrap() } });
        snapshots.replaceChildren(el("option", { value: "" }, t("选择已保存对话")), ...listOf(data, "snapshots").map((s) =>
          el("option", { value: s.snapshot_id }, `${new Date(s.created_at * 1000).toLocaleString()} · ${s.model}`)));
      } catch (error) { panel.error.textContent = error.message; }
    };
    this.root.append(el("div", { class: "ea-title" }, el("span", {}, this.dot, this.state)),
      el("div", { class: "ea-row" }, this.start, button(t("文字连接"), () => this.connect(false)), this.muteButton, button(t("打断"), () => this.interrupt())),
      this.meter, this.transcript, el("div", { class: "ea-row" }, this.text, button(t("发送"), () => this.sendText())),
      el("div", { class: "ea-row" }, button(t("冻结本轮"), () => this.freeze(false)), button(t("结束并应用"), () => this.freeze(true))),
      el("div", { class: "ea-row" }, snapshots, button(t("恢复记录"), refreshSnapshots)),
      el("div", { class: "ea-caption" }, t("执行工作流读取已冻结的文本和音频。声纹/音色等参数在 params_json 中配置。")));
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.start.disabled = true;
      panel.error.textContent = t("此页面无法访问麦克风。请通过可信 HTTPS 或 localhost 打开 ComfyUI；私网 HTTP 不支持语音采集。");
    }
  }
  send(event) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.socket.bufferedAmount > 512 * 1024) {
      this.panel.error.textContent = t("网络发送积压，已停止会话以避免音频延迟持续增加。"); this.disconnect(); return false;
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
      if (!profileId || !model) throw new Error(t("请先选择连接配置和实时模型"));
      this.inputRate = Number(params.input_sample_rate || (params.input_audio_format === "pcm" ? 16000 : 24000));
      this.outputRate = Number(params.output_sample_rate || 24000);
      if (![8000, 16000, 24000, 44100, 48000].includes(this.inputRate) || ![8000, 16000, 22050, 24000, 44100, 48000].includes(this.outputRate)) throw new Error(t("不支持的 PCM 采样率"));
      const config = this.sessionConfig(params);
      const inputFormat = config.audio?.input?.format || config.input_audio_format;
      const outputFormat = config.audio?.output?.format || config.output_audio_format;
      const isPcm = (format) => ["pcm", "pcm16", "audio/pcm"].includes(typeof format === "object" ? format?.type : format);
      if (!isPcm(outputFormat) || !isPcm(inputFormat)) throw new Error(t("实时麦克风与播放当前使用 PCM16；请将音频格式设为 pcm16、pcm 或原生 audio/pcm"));
      if (inputFormat?.rate) this.inputRate = Number(inputFormat.rate);
      if (outputFormat?.rate) this.outputRate = Number(outputFormat.rate);
      const ctx = await this.audioContext();
      if (withMic) {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error(t("语音采集需要可信 HTTPS 或 localhost 页面"));
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
      this.socket = new WebSocket(url); this.state.textContent = t("连接中…");
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
        } catch (error) { this.panel.error.textContent = t("无法读取实时消息：") + error.message; }
      });
      this.socket.addEventListener("error", () => { if (epoch === this.epoch) this.panel.error.textContent = t("实时连接失败，请检查后端服务和凭据。"); });
      this.socket.addEventListener("close", () => { if (epoch === this.epoch) { this.state.textContent = t("已断开"); this.disconnect(); } });
    } catch (error) { if (epoch === this.epoch) { this.disconnect(); this.panel.error.textContent = error.message; } }
  }
  handle(event) {
    const type = String(event.type || "");
    if (type === "easyapi.started") { this.started = true; this.state.textContent = this.stream ? t("麦克风已开启") : t("文字会话已连接"); this.dot.dataset.live = "true"; this.muteButton.disabled = !this.stream; }
    else if (type === "easyapi.snapshot") {
      setValue(this.node, "snapshot_id", event.snapshot_id);
      this.panel.status.textContent = t("frozenSnapshot", { revision: event.revision }) + (event.audio_truncated ? t("audioTruncated") : "");
      if (this.endAfterFreeze) { this.send({ type: "easyapi.stop" }); this.disconnect(); }
    } else if (["easyapi.error", "easyapi.expired", "error"].includes(type)) {
      const message = event.error?.message || event.message || t("实时服务错误");
      if (!/no active response/i.test(message)) this.panel.error.textContent = message;
      if (type === "easyapi.expired") this.disconnect();
    } else if (type === "response.created") { this.waiting = true; this.draft = ""; }
    else if (["response.audio.delta", "response.output_audio.delta"].includes(type)) this.enqueueAudio(event.delta || event.audio || "");
    else if (["response.audio_transcript.delta", "response.output_audio_transcript.delta", "response.text.delta", "response.output_text.delta"].includes(type)) { this.draft += event.delta || ""; this.renderTranscript(); }
    else if (["response.audio_transcript.done", "response.output_audio_transcript.done", "response.text.done", "response.output_text.done"].includes(type)) {
      const text = event.transcript || event.text || this.draft;
      if (text) this.turns.push(t("助手：") + text); this.draft = ""; this.renderTranscript();
    } else if (type === "conversation.item.input_audio_transcription.completed") { if (event.transcript) this.turns.push(t("用户：") + event.transcript); this.renderTranscript(); }
    else if (type === "input_audio_buffer.speech_started") this.interrupt();
    else if (type === "response.done") this.waiting = false;
    else if (type === "easyapi.interrupted") this.clearPlayback();
  }
  renderTranscript() {
    this.turns = this.turns.slice(-200);
    this.transcript.textContent = [...this.turns, ...(this.draft ? [t("助手：") + this.draft] : [])].join("\n\n") || t("等待对话…");
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }
  enqueueAudio(encoded) {
    if (!encoded || !this.context || !this.started) return;
    const ctx = this.context;
    if (this.nextPlayTime - ctx.currentTime > 8) { this.panel.error.textContent = t("播放积压超过 8 秒，已打断当前回答。"); this.interrupt(); return; }
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
  toggleMute() { this.muted = !this.muted; this.stream?.getTracks().forEach((track) => { track.enabled = !this.muted; }); this.muteButton.textContent = this.muted ? t("取消静音") : t("静音"); }
  sendText() {
    const text = this.text.value.trim(); if (!text || !this.started) return;
    if (this.send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } })) {
      this.turns.push(t("用户：") + text); this.renderTranscript(); this.send({ type: "response.create" }); this.text.value = "";
    }
  }
  freeze(end) {
    if (!this.started) { this.panel.error.textContent = t("请先连接并开始一次对话"); return; }
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
    this.dot.dataset.live = "false"; this.muteButton.disabled = true; this.muted = false; this.muteButton.textContent = t("静音"); this.state.textContent = t("未连接");
    if (this.context) { this.context.close().catch(() => {}); this.context = null; }
  }
  dispose() { this.disconnect(); }
  translate() {
    this.muteButton.textContent = t(this.muted ? "取消静音" : "静音");
    this.state.textContent = t(this.started ? (this.stream ? "麦克风已开启" : "文字会话已连接") : "未连接");
  }
}

app.registerExtension({
  name: "EasyAPI.Creator",
  commands: [{ id: "EasyAPI.OpenManager", label: () => t("managerTitle"), function: () => openProfiles() }],
  menuCommands: [{ path: ["EasyAPI"], commands: ["EasyAPI.OpenManager"] }],
  getNodeMenuItems(node) {
    const panel = node.easyapiPanel;
    if (!(panel instanceof NodePanel)) return [];
    const options = [];
    const profiles = panel.profiles || [];
    const current = String(value(node, "profile_id"));
    const hasCurrent = profiles.some((profile) => (profile.id || profile.profile_id) === current);
    if (panel.hasModel && hasCurrent && !panel.modelDriven) {
      options.push({ content: t("refreshModelList"), disabled: panel.loadingModels, callback: () => panel.refreshModels(true) });
    }
    if (!panel.connected("profile_id") && (profiles.length > 1 || !hasCurrent)) {
      const choices = profiles.map(({ id, profile_id }) => id || profile_id).filter((id) => /^[\w.-]+$/.test(id || ""))
        .map((id) => ({ content: id === current ? "✓ " + id : id, callback: () => panel.chooseProfile(id) }));
      if (choices.length) options.push({ content: t("connection"), submenu: { options: choices }, has_submenu: true });
      if (!hasCurrent) options.push({ content: t("connectionSettings"), callback: () => openProfiles() });
    }
    return options;
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "EasyAPIVideoPromptTuner") {
      installVideoTunerNode(nodeType, { app, api, t, loadProfiles: nodeProfiles,
        loadPromptModels: promptModels, loadVideoModels: videoModels,
        modelPicker: onSelect => new ModelPicker(onSelect),
        loadTargetSchema: ({ profileId, modelId, locale }, signal) => jsonRequest("/easyapi/schema?" +
          new URLSearchParams({ profile_id: profileId, model: modelId, operation: "video-generation", locale: locale || localeQuery() }), { signal }),
        openDetails: showTunerDetails,
        onResult: ({ node, result }) => node.easyapiTuningEditor?.wanPanel?.setResult(result, { stale: false }),
        onError: ({ node, error }) => node.easyapiTuningEditor?.wanPanel?.failProgress(error),
        onDispose: ({ node }) => { node.easyapiTuningEditor?.dispose(); node.easyapiTuningEditor?.root.remove(); },
      });
      return;
    }
    if (PRESET_NODES.has(nodeData.name)) {
      const specification = nodeData.input?.required?.operation || nodeData.input?.optional?.operation;
      const ids = Array.isArray(specification?.[0]) ? specification[0] : specification?.[1]?.options || [];
      for (const id of ids) registeredOperations.set(id, { id, hasModel: true });
      if (nodeData.name === "EasyAPIRealtime") registeredOperations.set("realtime", { id: "realtime", hasModel: true });
      if (nodeData.name === "EasyAPIImages") for (const id of ["image-generation", "image-edit"]) registeredOperations.set(id, { id, hasModel: true });
    }
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
      if (panel.isImage) { this.properties ??= {}; this.properties.easyapi_image_layout = IMAGE_LAYOUT_VERSION; }
      if (panel.isVideo) { this.properties ??= {}; this.properties.easyapi_video_layout = VIDEO_LAYOUT_VERSION; }
      const dom = this.addDOMWidget("easyapi_controls", "easyapi_controls", panel.root,
        { serialize: false, hideOnZoom: false, margin: 0, getMinHeight: () => panel.minHeight() });
      if (panel.isImage) dom.serialize = false;
      const widgetIndex = this.widgets.indexOf(dom);
      if (widgetIndex >= 0) { this.widgets.splice(widgetIndex, 1); this.widgets.unshift(dom); }
      const removed = this.onRemoved;
      this.onRemoved = (...values) => { panel.dispose(); removed?.apply(this, values); };
      const configure = this.onConfigure;
      this.onConfigure = (...values) => {
        configure?.apply(this, values);
        if (panel.isImage) { this.properties ??= {}; this.properties.easyapi_image_layout = IMAGE_LAYOUT_VERSION; }
        if (panel.isVideo) { this.properties ??= {}; this.properties.easyapi_video_layout = VIDEO_LAYOUT_VERSION; }
        panel.configure();
      };
      const added = this.onAdded;
      this.onAdded = (...values) => { added?.apply(this, values); panel.updateBacking(); panel.resize(); };
      const connected = this.onConnectionsChange;
      this.onConnectionsChange = (...values) => {
        connected?.apply(this, values);
        const wasDriven = panel.modelDriven;
        panel.updateBacking(); panel.resize();
        panel.promptEditor?.contextChanged();
        clearTimeout(panel.connectionTimer);
        if (panel.syncImageOperation()) return;
        if (wasDriven !== panel.modelDriven) {
          if (panel.modelDriven || value(this, "profile_id")) panel.refreshModels();
          else panel.refreshProfiles();
        }
        else panel.connectionTimer = setTimeout(() => { if (!panel.disposed) { panel.refreshSchema(); panel.refreshPresets(); } }, 80);
      };
      this.size = [Math.max(this.size?.[0] || 0, 360), this.size?.[1] || 300];
      return result;
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message, ...args) {
      executed?.call(this, message, ...args);
      this.easyapiPromptOutput = message;
      const text = message?.easyapi_text?.join("\n") || "";
      if (this.easyapiPanel) this.easyapiPanel.preview.textContent = text.slice(0, 12000);
    };
  },
  setup() {
    registerManagerSidebar();
    app.ui?.settings?.addEventListener("Comfy.Locale.change", () => { relabel(); window.dispatchEvent(new Event("easyapi:locale")); for (const panel of panels) panel.translate(); profileManager?.translate(); });
  },
  beforeConfigureGraph(graphData) {
    for (const panel of panels) panel.realtime?.disconnect();
    const videoReport = migrateVideoWorkflow(graphData);
    if (videoReport.disconnected) {
      const detail = t("videoLinksMigrated", { count: videoReport.disconnected });
      app.extensionManager?.toast?.add?.({ severity: "warn", summary: t("videoNodeUpdated"), detail, life: 15000 });
      console.warn("[EasyAPI] " + detail);
    }
    const report = migrateImageWorkflow(graphData);
    if (report.disconnected) {
      const detail = t("imageLinksMigrated", { count: report.disconnected });
      app.extensionManager?.toast?.add?.({ severity: "warn", summary: t("imageNodeUpdated"), detail, life: 15000 });
      console.warn("[EasyAPI] " + detail);
      this.imageMigrationNotice = detail;
    }
  },
});
window.addEventListener("pagehide", () => {
  for (const panel of panels) {
    panel.promptEditor?.finishTyping();
    panel.realtime?.disconnect();
  }
});
