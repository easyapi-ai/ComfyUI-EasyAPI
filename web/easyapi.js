import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, textKey, hostLocale, localeQuery } from "./ui-i18n.js";
import { parseParams, draftKey, selectProfile, persistDraft, readDraft, normalizedSchema, requiredDefaults, VIDEO_PORTS, videoPortState } from "./ui-state.js";
import { renderSchemaForm } from "./ui-forms.js";
import { ProfileManager, EASYAPI_SIDEBAR_ID } from "./profile-panel.js";
import { PresetPanel } from "./preset-panel.js";

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
let nonce = "";
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
async function bootstrap() {
  // The backend may have restarted while the workflow and this module stayed open.
  nonce = (await jsonRequest("/easyapi/bootstrap", { method: "POST" })).management_nonce;
  if (!nonce) throw new Error(t("未获取连接管理凭据，请刷新页面"));
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

let profileManager = null;
let managerSidebarRegistered = false;
function getProfileManager() {
  if (!profileManager) profileManager = new ProfileManager({
    request: jsonRequest, bootstrap,
    createPresets: () => new PresetPanel({
      request: jsonRequest, mutation: (path, body, method) => profileManager.mutation(path, body, method),
      modelPicker: (onSelect) => new ModelPicker(onSelect), getOperations: () => [...registeredOperations.values()],
      onChanged: () => Promise.allSettled([...panels].map((panel) => panel.refreshPresets())),
    }),
    onChanged: async (profileId) => {
      await Promise.allSettled([...panels].map(async (panel) => {
        if (profileId && String(value(panel.node, "profile_id")) === profileId) panel.realtime?.disconnect();
        await panel.refreshProfiles();
      }));
    },
  });
  return profileManager;
}
function registerManagerSidebar() {
  if (managerSidebarRegistered || !app.extensionManager?.registerSidebarTab) return;
  app.extensionManager.registerSidebarTab({
    id: EASYAPI_SIDEBAR_ID, icon: "icon-[lucide--settings]", title: "EasyAPI", tooltip: t("managerTitle"), type: "custom",
    render: (container) => {
      const manager = getProfileManager(); container.replaceChildren(manager.root); manager.refresh();
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
  if (manager.root.isConnected) await manager.refresh(profileId || undefined);
}

let pickerSequence = 0;
class ModelPicker {
  constructor(onSelect) {
    this.items = []; this.onSelect = onSelect; this.selected = ""; this.active = -1;
    this.root = el("div", { class: "ea-model-picker" });
    this.input = el("input", { role: "combobox", autocomplete: "off", spellcheck: false });
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.setAttribute("aria-expanded", "false");
    this.list = el("div", { class: "ea-model-list", role: "listbox", id: "easyapi-models-" + ++pickerSequence, hidden: true });
    this.input.setAttribute("aria-controls", this.list.id);
    this.input.addEventListener("focus", () => { this.input.select(); this.open(""); });
    this.input.addEventListener("input", () => this.open(this.input.value));
    this.input.addEventListener("blur", () => setTimeout(() => { if (!this.root.contains(document.activeElement)) this.close(); }, 120));
    this.input.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); if (this.list.hidden) this.open("");
        this.active = Math.max(0, Math.min(this.filtered.length - 1, this.active + (event.key === "ArrowDown" ? 1 : -1))); this.highlight();
      } else if (event.key === "Enter") {
        event.preventDefault();
        const exact = this.items.find((item) => item.id === this.input.value.trim());
        const candidate = exact || this.filtered?.[Math.max(this.active, 0)];
        if (candidate && !this.list.hidden) this.choose(candidate);
      } else if (event.key === "Escape") { event.preventDefault(); this.close(); }
    });
    this.root.append(this.input, this.list);
    this.translate();
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
    this.filtered = this.items.filter((item) => `${item.id} ${this.label(item)}`.toLowerCase().includes(query)).slice(0, 100);
    this.active = -1; this.list.replaceChildren();
    this.filtered.forEach((item, index) => {
      const label = this.label(item);
      const option = el("button", { type: "button", class: "ea-model-option", role: "option", id: this.list.id + "-" + index },
        el("span", { class: "ea-model-name" }, label));
      if (label !== item.id) option.append(el("small", { class: "ea-model-id" }, item.id));
      option.setAttribute("aria-selected", String(item.id === this.selected));
      const capability = item.capability_status;
      if (capability === "unknown" || capability === "inferred") option.append(el("small", { class: "ea-model-capability" }, t(capability === "unknown" ? "unknownCapability" : "inferredCapability")));
      option.addEventListener("mousedown", (event) => event.preventDefault());
      option.addEventListener("click", () => this.choose(item)); this.list.append(option);
    });
    if (!this.filtered.length) this.list.append(el("div", { class: "ea-caption ea-model-empty" }, t("noMatches")));
    this.list.hidden = false; this.input.setAttribute("aria-expanded", "true");
  }
  highlight() {
    [...this.list.children].forEach((item, index) => item.classList.toggle("ea-active", index === this.active));
    const current = this.list.children[this.active];
    if (current) { this.input.setAttribute("aria-activedescendant", current.id); current.scrollIntoView?.({ block: "nearest" }); }
  }
  choose(item) { this.selected = item.id; this.close(); this.onSelect(item.id); }
}

class NodePanel {
  constructor(node) {
    this.node = node; this.disposed = false; this.sequence = 0; this.schemaSequence = 0; this.mutating = false;
    this.targetField = widget(node, "request_body") ? "request_body" : "params_json";
    this.root = el("section", { class: "easyapi-panel" });
    this.profile = el("select", { onchange: () => this.chooseProfile(this.profile.value) });
    this.profile.setAttribute("aria-label", t("connection"));
    this.picker = new ModelPicker((id) => this.chooseModel(id));
    this.status = el("div", { class: "ea-status", role: "status" });
    this.error = el("div", { class: "ea-error", role: "alert" });
    this.schema = el("div", { class: "ea-schema" });
    this.preview = el("pre", { class: "ea-preview" });
    this.modelLabel = field(t("model"), this.picker.root);
    this.root.append(el("div", { class: "ea-title" }, el("span", {}, t("connection")), button(t("connectionSettings"), () => openProfiles(value(node, "profile_id")))),
      el("div", { class: "ea-row" }, this.profile, button(t("refresh"), () => this.refreshModels(true))),
      this.modelLabel, this.status, this.error);
    this.createPrompt();
    this.hasModel = Boolean(widget(node, "model")); this.isVideo = (node.comfyClass || node.type) === "EasyAPIVideo";
    this.presetEligible = this.hasModel && PRESET_NODES.has(node.comfyClass || node.type);
    this.autoPresetCandidate = this.presetEligible && !String(value(node, "model")) && value(node, this.targetField, "{}").replace(/\s/g, "") === "{}";
    if (!this.hasModel) this.modelLabel.hidden = true;
    if (this.presetEligible) this.createPresetControls();
    if (this.isVideo) this.createVideoControls();
    if (widget(node, "task_id")) this.addTaskSelector();
    this.root.append(this.schema); this.createAdvanced();
    if ((node.comfyClass || node.type) === "EasyAPIRealtime") {
      this.realtime = new RealtimePanel(this); this.root.append(this.realtime.root);
    } else this.root.append(this.preview);
    this.currentIdentity = this.identity(); this.persistCurrent();
    for (const name of ["profile_id", "model", "params_json", "request_body", "mode"]) {
      const target = widget(node, name); if (target) target.hidden = true;
    }
    for (const name of ["timeout", "wait_for_result"]) if (widget(node, name)) widget(node, name).advanced = true;
    if (this.isVideo && widget(node, "operation")) widget(node, "operation").hidden = true;
    for (const name of ["profile_id", "model", "operation", "params_json", "request_body", "mode", "prompt"]) {
      const target = widget(node, name); if (!target) continue;
      const original = target.callback;
      target.callback = (...args) => {
        original?.apply(target, args);
        if (this.mutating || app.configuringGraph || this.disposed) return;
        if (name === "prompt") { if (this.promptEditor) this.promptEditor.value = target.value; return; }
        if (["profile_id", "model", "operation"].includes(name)) { if (name !== "profile_id") this.autoPresetCandidate = false; this.transition(); this.refreshModels(); }
        else if (name === "mode") { this.persistCurrent(); this.refreshSchema(); }
        else { this.persistCurrent(); this.syncAdvanced(); this.renderForm(); }
      };
    }
    panels.add(this); this.updateBacking(); this.refreshProfiles();
  }
  identity() { return draftKey(value(this.node, "profile_id"), value(this.node, "model"), value(this.node, "operation", this.realtime ? "realtime" : (this.node.comfyClass === "EasyAPIRealtime" ? "realtime" : ""))); }
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
    this.schemaAbort?.abort(); ++this.schemaSequence; this.metadata = null; this.formSchema = null; this.schema.replaceChildren();
    this.currentIdentity = next; this.jsonDirty = false; this.persistCurrent(); this.syncAdvanced();
  }
  configure() {
    // Backend widget values are the authoritative selected draft when loading an old workflow.
    this.wasConfigured = true; this.preserveLoadedParams = true; this.autoPresetCandidate = false;
    this.currentIdentity = this.identity(); this.persistCurrent(); this.jsonDirty = false;
    this.syncAdvanced(); this.updateBacking(); this.refreshProfiles();
  }
  createPrompt() {
    if (!widget(this.node, "prompt")) return;
    this.promptEditor = el("textarea", { rows: 4, value: value(this.node, "prompt"), oninput: () => this.write("prompt", this.promptEditor.value) });
    this.promptEditor.setAttribute("aria-label", t("prompt"));
    this.promptField = field(t("prompt"), this.promptEditor);
    this.promptHint = el("div", { class: "ea-caption", hidden: true }, t("linkedInput"));
    this.root.append(this.promptField, this.promptHint);
  }
  connected(name) { return (this.node.inputs || []).some((input) => input.name === name && input.link != null); }
  updateBacking() {
    const id = String(this.node.id).replace(/["\\]/g, "\\$&");
    const rules = [];
    for (const name of ["profile_id", "model", "params_json", "request_body", "mode", ...(this.isVideo ? ["operation"] : []), "prompt"]) {
      const target = widget(this.node, name); if (!target) continue;
      const linked = this.connected(name); target.hidden = !linked;
      const index = (this.node.inputs || []).findIndex((input) => input.name === name);
      if (!linked && index >= 0) rules.push(`[data-node-id="${id}"] [data-testid="node-widget"]:has([data-slot-key="${id}-in-${index}"]){display:none!important}`);
    }
    this.backingCSS = rules.join("\n");
    if (this.promptField) {
      this.promptField.hidden = this.connected("prompt"); this.promptHint.hidden = !this.connected("prompt");
      this.promptEditor.value = value(this.node, "prompt");
    }
    const driven = this.connected("profile_id") || this.connected("model");
    this.profile.disabled = this.connected("profile_id"); this.picker.input.disabled = driven;
    if (driven) this.status.textContent = t("linkedSelection");
    this.writePortCSS();
  }
  writePortCSS() { portRules.set(this, (this.backingCSS || "") + "\n" + (this.videoCSS || "")); portSheet.textContent = [...portRules.values()].join("\n"); }
  chooseProfile(id, automatic = false) {
    if (String(value(this.node, "profile_id")) === String(id)) return;
    if (!automatic) this.preserveLoadedParams = false;
    this.realtime?.disconnect(); this.write("profile_id", id); this.transition(automatic);
    this.profile.value = id; this.refreshModels(true);
  }
  chooseModel(id) {
    if (String(value(this.node, "model")) === id) return;
    this.preserveLoadedParams = false; this.autoPresetCandidate = false; this.realtime?.disconnect(); this.write("model", id); this.transition(); this.refreshSchema();
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
    this.root.append(this.advanced); this.syncAdvanced();
  }
  syncAdvanced() { if (this.jsonEditor && !this.jsonDirty) this.jsonEditor.value = value(this.node, this.targetField, "{}"); }
  commitParams(next) {
    let safe = objectValue(JSON.stringify(next));
    if (this.metadata?.matched !== false && this.metadata?.declared !== false && this.formSchema) safe = requiredDefaults(this.formSchema, safe);
    this.write(this.targetField, JSON.stringify(safe, null, 2)); this.persistCurrent(); this.syncAdvanced(); this.node.graph?.change?.();
  }
  async refreshProfiles() {
    try {
      const data = await jsonRequest("/easyapi/profiles"); if (this.disposed) return;
      this.profiles = listOf(data, "profiles"); this.defaultId = data.default_profile_id || null;
      let current = String(value(this.node, "profile_id"));
      this.profile.replaceChildren(el("option", { value: "" }, t("chooseConnection")), ...this.profiles.map((profile) => {
        const id = profile.id || profile.profile_id;
        return el("option", { value: id }, (profile.name || id) + (id === this.defaultId ? " · " + t("defaultConnection") : ""));
      }));
      const selected = selectProfile(current, this.profiles, this.defaultId);
      if (selected !== current) { this.write("profile_id", selected); this.transition(true); current = selected; }
      if (current && !this.profiles.some((profile) => (profile.id || profile.profile_id) === current)) {
        this.profile.append(el("option", { value: current }, current)); this.profile.value = current;
        this.status.textContent = t("profileUnavailable"); return;
      }
      this.profile.value = current;
      if (!current) {
        this.picker.input.disabled = true; this.picker.setItems([], value(this.node, "model"));
        this.status.textContent = t(this.profiles.length > 1 ? "chooseProfileMultiple" : "profileNeeded"); return;
      }
      await this.refreshModels();
    } catch (error) { if (!this.disposed) this.error.textContent = error.message; }
  }
  updateModelStatus() {
    const current = String(value(this.node, "model"));
    const found = this.models?.find((model) => model.id === current);
    this.status.textContent = current && !found ? t("missingModel") : t("modelCount", { count: this.models?.length || 0 });
    if (!this.models?.length && !current) this.status.textContent = t("noModels");
  }
  async refreshModels(force = false) {
    const profileId = String(value(this.node, "profile_id"));
    if (!profileId) return;
    if (this.connected("profile_id") || this.connected("model")) { this.status.textContent = t("linkedSelection"); this.schema.replaceChildren(); return; }
    if (!this.hasModel) {
      this.status.textContent = (this.node.comfyClass || this.node.type) === "EasyAPITools" ? t("toolModelManaged") : "";
      this.schema.replaceChildren(); this.syncAdvanced(); return;
    }
    const sequence = ++this.sequence;
    this.modelAbort?.abort(); this.modelAbort = new AbortController();
    this.status.textContent = t("loadingModels"); this.error.textContent = "";
    try {
      const data = await jsonRequest(`/easyapi/models?${new URLSearchParams({ profile_id: profileId, family: familyFor(this.node),
        operation: String(value(this.node, "operation", "realtime")), locale: localeQuery(), refresh: force ? "1" : "0" })}`, { signal: this.modelAbort.signal });
      if (this.disposed || sequence !== this.sequence) return;
      this.models = listOf(data, "models").map((model) => typeof model === "string" ? { id: model } : { ...model, id: model.id || model.name });
      this.picker.input.disabled = false; this.picker.setItems(this.models, value(this.node, "model"));
      this.updateModelStatus(); await this.refreshPresets(true); await this.refreshSchema();
    } catch (error) {
      if (error.name !== "AbortError" && sequence === this.sequence && !this.disposed) {
        this.picker.input.disabled = false; this.picker.setItems([], value(this.node, "model"));
        this.status.textContent = t("authorityError"); this.error.textContent = error.message;
      }
    }
  }
  async refreshSchema() {
    const profile = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
    if (!profile || !model || !this.hasModel) return;
    const sequence = ++this.schemaSequence;
    this.schemaAbort?.abort(); this.schemaAbort = new AbortController();
    const mode = value(this.node, "mode", "auto");
    const provisional = this.isVideo ? videoPortState(this.node.inputs || [], this.metadata?.video, mode).selected : "";
    const query = { profile_id: profile, model, operation: String(value(this.node, "operation", "realtime")), locale: localeQuery() };
    if (provisional) query.variant = provisional;
    try {
      const metadata = await jsonRequest(`/easyapi/schema?${new URLSearchParams(query)}`, { signal: this.schemaAbort.signal });
      if (this.disposed || sequence !== this.schemaSequence) return;
      this.metadata = metadata;
      let schema = normalizedSchema(metadata);
      if (this.isVideo) {
        const state = this.updateVideo();
        schema = metadata.video?.variants?.[state.selected]?.request_schema || schema;
      }
      this.formSchema = schema;
      if (!this.preserveLoadedParams && metadata.matched !== false && metadata.declared !== false) {
        const before = this.params(), withDefaults = requiredDefaults(schema, before);
        if (JSON.stringify(before) !== JSON.stringify(withDefaults)) this.commitParams(withDefaults);
      }
      this.renderForm(); this.syncAdvanced();
    } catch (error) {
      if (error.name !== "AbortError" && !this.disposed && sequence === this.schemaSequence) { this.formSchema = {}; this.renderForm(); this.error.textContent = error.message; }
    }
  }
  renderForm() {
    if (!widget(this.node, this.targetField) || !this.hasModel) return;
    renderSchemaForm(this.schema, this.formSchema || {}, () => this.params(), (next) => { this.autoPresetCandidate = false; this.commitParams(next); }, t, () => this.resize());
  }
  operationId() { return String(value(this.node, "operation", (this.node.comfyClass || this.node.type) === "EasyAPIRealtime" ? "realtime" : "")); }
  createPresetControls() {
    this.presetSelect = el("select", { onchange: () => { this.applyPresetButton.disabled = !this.presetSelect.value; } });
    this.presetSelect.setAttribute("aria-label", t("presetsTab"));
    this.applyPresetButton = button(t("applyPreset"), () => {
      const preset = this.presets?.find((record) => record.id === this.presetSelect.value);
      if (preset) this.applyPresetRecord(preset, false).catch((error) => { this.error.textContent = error.message; });
    }); this.applyPresetButton.disabled = true;
    this.root.append(el("div", { class: "ea-preset-controls" }, this.presetSelect,
      el("div", { class: "ea-row" }, this.applyPresetButton, button(t("saveAsPreset"), async () => {
        try {
          if (this.jsonDirty) throw new Error(t("applyJsonFirst"));
          const profile = String(value(this.node, "profile_id")), model = String(value(this.node, "model"));
          if (!profile || !model) throw new Error(t("selectPresetScope"));
          await openProfiles(profile, "presets");
          await getProfileManager().presetPanel.newFromNode({ profile_id: profile, operation: this.operationId(), model, params: this.params() });
        } catch (error) { this.error.textContent = error.message; }
      }))));
  }
  async refreshPresets(considerDefault = false) {
    if (!this.presetEligible) return;
    const profile = String(value(this.node, "profile_id")), operation = this.operationId();
    if (!profile || !operation) return;
    const key = JSON.stringify([profile, operation]); this.presetRequestKey = key;
    try {
      const data = await jsonRequest(`/easyapi/presets?${new URLSearchParams({ profile_id: profile, operation })}`);
      if (this.disposed || this.presetRequestKey !== key || String(value(this.node, "profile_id")) !== profile || this.operationId() !== operation) return;
      this.presets = (data.presets || []).filter((record) => record.profile_id === profile && record.operation === operation);
      const selected = this.presetSelect.value;
      this.presetSelect.replaceChildren(el("option", { value: "" }, t("choosePreset")), ...this.presets.map((record) =>
        el("option", { value: record.id }, record.name + " · " + record.model + (data.default_preset_ids?.[operation] === record.id ? " · " + t("defaultPreset") : ""))));
      this.presetSelect.value = this.presets.some((record) => record.id === selected) ? selected : "";
      this.applyPresetButton.disabled = !this.presetSelect.value;
      if (considerDefault && !this.autoPresetChecked && this.autoPresetCandidate && !this.wasConfigured && !value(this.node, "model") && Object.keys(this.params()).length === 0) {
        this.autoPresetChecked = true;
        const preset = this.presets.find((record) => record.id === data.default_preset_ids?.[operation]);
        if (preset && this.models?.some((model) => model.id === preset.model)) { await this.applyPresetRecord(preset, true); this.presetSelect.value = preset.id; this.applyPresetButton.disabled = false; }
        else if (preset) this.status.textContent = t("defaultPresetUnavailable");
      }
    } catch (error) { if (!this.disposed) this.error.textContent = error.message; }
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
    this.videoBox = el("div", { class: "ea-video-inputs" });
    this.modeSelect = el("select", { onchange: () => { this.write("mode", this.modeSelect.value); this.persistCurrent(); this.refreshSchema(); } });
    this.modeLabel = field(t("videoMode"), this.modeSelect);
    this.videoInfo = el("div", { class: "ea-caption" }); this.videoError = el("div", { class: "ea-error", role: "status" });
    this.videoBox.append(this.modeLabel, this.videoInfo, this.videoError); this.root.append(this.videoBox);
  }
  updateVideo() {
    const inputs = this.node.inputs || [], video = this.metadata?.video || {};
    const state = videoPortState(inputs, video, value(this.node, "mode", "auto"));
    const scenarios = video.scenarios || [];
    this.modeSelect.replaceChildren(el("option", { value: "auto" }, t("videoAuto")), ...scenarios.map((scenario) =>
      el("option", { value: scenario }, video.variants?.[scenario]?.label || t("scenario." + scenario))));
    const selectedMode = value(this.node, "mode", "auto");
    if (selectedMode !== "auto" && !scenarios.includes(selectedMode)) this.modeSelect.append(el("option", { value: selectedMode }, t("scenario." + selectedMode)));
    this.modeSelect.value = selectedMode;
    this.modeLabel.hidden = !state.ambiguous && selectedMode === "auto";
    this.videoInfo.textContent = state.known ? t("supportedRoles", { roles: (video.roles || []).map((role) => t("port." + role)).join(" · ") || t("scenario.text-to-video") }) : t("videoUndeclared");
    const problems = [];
    if (state.invalid.length) problems.push(t("incompatibleReferences", { ports: state.invalid.map((name) => this.portLabel(name)).join(", ") }));
    if (state.selected && scenarios.length && !scenarios.includes(state.selected)) problems.push(t("unsupportedScenario", { mode: t("scenario." + state.selected) }));
    if (state.ambiguous) problems.push(t("videoAmbiguous"));
    this.videoError.textContent = problems.join("\n");
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
      this.transition(); this.profile.value = handle.profile_id;
    } });
    const refresh = async () => {
      try { this.savedTasks = listOf(await jsonRequest("/easyapi/tasks"), "tasks");
        if (!this.disposed) select.replaceChildren(el("option", { value: "" }, t("选择已保存任务")), ...this.savedTasks.map((task) =>
          el("option", { value: JSON.stringify([task.profile_id, task.task_id]) }, `${task.task_id} · ${task.state || "submitted"}`)));
      } catch (error) { this.error.textContent = error.message; }
    };
    this.root.append(el("div", { class: "ea-row" }, select, button("刷新任务", refresh))); refresh();
  }
  translate() {
    this.picker.translate(); this.profile.setAttribute("aria-label", t("connection"));
    this.promptEditor?.setAttribute("aria-label", t("prompt")); this.jsonEditor?.setAttribute("aria-label", t("advancedJson"));
    this.realtime?.translate?.(); this.refreshProfiles();
  }
  minHeight() { return Math.max(this.realtime ? 550 : 245, Math.min(this.root.scrollHeight || 300, this.realtime ? 1000 : 820)); }
  resize() {
    if (this.disposed || this.resizing) return;
    this.resizing = true;
    requestAnimationFrame(() => {
      this.resizing = false; if (this.disposed) return;
      const height = this.node.computeSize?.()[1];
      if (height) this.node.setSize?.([Math.max(this.node.size?.[0] || 0, 440), height]);
      this.node.graph?.setDirtyCanvas(true, true);
    });
  }
  dispose() {
    this.disposed = true; this.modelAbort?.abort(); this.schemaAbort?.abort(); clearTimeout(this.connectionTimer);
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
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (PRESET_NODES.has(nodeData.name)) {
      const specification = nodeData.input?.required?.operation || nodeData.input?.optional?.operation;
      const ids = Array.isArray(specification?.[0]) ? specification[0] : specification?.[1]?.options || [];
      for (const id of ids) registeredOperations.set(id, { id, hasModel: true });
      if (nodeData.name === "EasyAPIRealtime") registeredOperations.set("realtime", { id: "realtime", hasModel: true });
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
      const dom = this.addDOMWidget("easyapi_controls", "easyapi_controls", panel.root,
        { serialize: false, hideOnZoom: false, getMinHeight: () => panel.minHeight() });
      const widgetIndex = this.widgets.indexOf(dom);
      if (widgetIndex >= 0) { this.widgets.splice(widgetIndex, 1); this.widgets.unshift(dom); }
      const removed = this.onRemoved;
      this.onRemoved = (...values) => { panel.dispose(); removed?.apply(this, values); };
      const configure = this.onConfigure;
      this.onConfigure = (...values) => { configure?.apply(this, values); panel.configure(); };
      const added = this.onAdded;
      this.onAdded = (...values) => { added?.apply(this, values); panel.updateBacking(); panel.resize(); };
      const connected = this.onConnectionsChange;
      this.onConnectionsChange = (...values) => { connected?.apply(this, values); panel.updateBacking();
        clearTimeout(panel.connectionTimer); panel.connectionTimer = setTimeout(() => { if (!panel.disposed) panel.refreshSchema(); }, 80); };
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
  setup() {
    registerManagerSidebar();
    app.ui?.settings?.addEventListener("Comfy.Locale.change", () => { relabel(); window.dispatchEvent(new Event("easyapi:locale")); for (const panel of panels) panel.translate(); profileManager?.translate(); });
  },
  beforeConfigureGraph() { for (const panel of panels) panel.realtime?.disconnect(); },
});
window.addEventListener("pagehide", () => { for (const panel of panels) panel.realtime?.disconnect(); });
