import { parseParams, normalizedSchema, selectProfile } from "./ui-state.js";
import { renderSchemaForm } from "./ui-forms.js";
import { readTuningDraft, writeTuningDraft, tuningFingerprint } from "./video-tuning-state.js";

const FIELDS = ["profile_id", "text_model", "analysis_model", "target_profile_id", "target_model", "target_params_json",
  "prompt", "domain", "mode", "output", "analyze", "issue", "issue_start", "issue_end", "locale", "scope_id", "revision"];
const DOMAINS = ["general", "drama", "ecommerce"];
const MODES = ["auto", "text-to-video", "first-frame", "first-last-frame", "reference", "v2v"];
const OUTPUTS = ["quick", "full", "diagnose"];
const panels = new Set();
let backingStyle;
let pickerId = 0;

const clone = value => JSON.parse(JSON.stringify(value));
const widget = (node, name) => node.widgets?.find(item => item.name === name);
const value = (node, name, fallback = "") => widget(node, name)?.value ?? fallback;
const linked = (node, name) => node.inputs?.some(input => input.name === name && input.link != null) || false;
const list = data => Array.isArray(data) ? data : Array.isArray(data?.models) ? data.models : [];
const visual = model => model.supports_image === true || model.input_modalities?.includes("image");

function uuid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; ++index) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

export function ensureTunerScope(node, createId = uuid) {
  const scope = widget(node, "scope_id");
  if (!scope) throw new Error("The video tuning node has no scope_id input");
  const duplicate = (node.graph?._nodes || []).some(other => other !== node &&
    (other.comfyClass || other.type) === "EasyAPIVideoPromptTuner" && value(other, "scope_id") === scope.value);
  if (!scope.value || duplicate) scope.value = createId();
  scope.hidden = true;
  const revision = widget(node, "revision"); if (revision) revision.hidden = true;
  return scope.value;
}

// A preview exists only in this API prompt. The canvas workflow and downstream
// widgets are untouched, and ComfyUI resolves connected inputs through upstream nodes.
export async function queueVideoTunerNode(node, { app, api, retry = false, createId = uuid }) {
  ensureTunerScope(node, createId);
  if (retry) {
    const revision = widget(node, "revision");
    if (!revision || linked(node, "revision")) throw new Error("The tuning revision must be a local widget");
    const previous = Number(revision.value);
    if (!Number.isSafeInteger(previous) || previous < 0 || previous >= Number.MAX_SAFE_INTEGER) throw new Error("The tuning revision must be a nonnegative safe integer");
    revision.value = previous + 1;
  }
  const serialized = clone(await app.graphToPrompt());
  const id = String(node.id);
  if (serialized.output?.[id]?.class_type !== "EasyAPIVideoPromptTuner") throw new Error("The tuning node is muted or unavailable in this graph");
  const output = {};
  const visit = key => {
    key = String(key);
    if (Object.hasOwn(output, key)) return;
    const entry = serialized.output[key];
    if (!entry) throw new Error("A tuning input refers to a missing upstream node");
    output[key] = entry;
    for (const input of Object.values(entry.inputs || {})) {
      if (Array.isArray(input) && input.length === 2 && ["string", "number"].includes(typeof input[0]) && Number.isInteger(input[1])) visit(input[0]);
    }
  };
  visit(id);
  let helperId;
  do { helperId = "__easyapi_tune_preview_" + createId(); } while (Object.hasOwn(serialized.output, helperId));
  output[helperId] = { class_type: "EasyAPIPreview", inputs: { json_value: [id, 2] }, _meta: { title: "EasyAPI tuning preview" } };
  return api.queuePrompt(0, { output, workflow: serialized.workflow }, { partialExecutionTargets: [helperId] });
}

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, props); node.append(...children); return node;
}

function fallbackPicker(onChoose) {
  const id = "ea-tuner-model-options-" + ++pickerId;
  const options = element("datalist", { id });
  const input = element("input", { type: "text", autocomplete: "off" });
  input.setAttribute("list", id);
  input.addEventListener("change", () => onChoose(input.value.trim()));
  return { input, root: element("div", { className: "ea-model-picker" }, input, options),
    setItems(items, selected) {
      options.replaceChildren(...items.map(model => element("option", { value: model.id, label: model.display_name || model.id })));
      input.value = selected || "";
    }, dispose() {} };
}

export class VideoTunerNodePanel {
  constructor(node, services) {
    this.node = node; Object.assign(this, services);
    this.profiles = []; this.controls = {}; this.labels = []; this.fieldLabels = []; this.loads = {}; this.optionLabels = {}; this.writing = false; this.allowAnalysisDefault = true;
    this.usePromptModelDefaults = !this.app?.configuringGraph;
    this.root = element("section", { className: "easyapi-panel ea-node-panel ea-tuner-node" });
    this.content = element("div", { className: "ea-tuner-content" }); this.root.append(this.content);
    this.status = element("p", { className: "ea-status", role: "status" });
    this.profile = this.select("profile_id", []); this.targetProfile = this.select("target_profile_id", []);
    const picker = services.modelPicker || fallbackPicker;
    this.textPicker = picker(id => this.chooseModel("text_model", id));
    this.analysisPicker = picker(id => this.chooseModel("analysis_model", id));
    this.targetPicker = picker(id => this.chooseModel("target_model", id));
    this.textPicker.input.setAttribute("aria-label", this.text("promptTransformModel", "Text model"));
    this.analysisPicker.input.setAttribute("aria-label", this.text("wanAnalysisModel", "Media analysis model"));
    this.targetPicker.input.setAttribute("aria-label", this.text("tunerTargetModel", "Video model"));
    const tuning = this.section("tunerConnectionTitle", "Tuning models", this.grid(
      this.field("tunerConnection", "Tuning connection", this.profile), this.field("promptTransformModel", "Text model", this.textPicker.root),
      this.field("wanAnalysisModel", "Media analysis model", this.analysisPicker.root)));
    this.modelsStatus = element("span", { className: "ea-caption", role: "status" });
    this.modelPreferenceStatus = element("p", { className: "ea-caption", role: "status", hidden: true });
    this.refreshButton = this.action("refresh", "Refresh models", () => this.refreshModels(true));
    tuning.append(element("div", { className: "ea-model-updates" }, this.modelsStatus, this.refreshButton), this.modelPreferenceStatus);
    this.schema = element("div", { className: "ea-schema" });
    this.paramsEditor = element("textarea", { rows: 3, spellcheck: false });
    this.paramsEditor.setAttribute("aria-label", this.text("tunerTargetParams", "Target video parameters"));
    this.paramsEditor.addEventListener("change", () => {
      try { const parsed = parseParams(this.paramsEditor.value); this.write("target_params_json", JSON.stringify(parsed)); this.paramsEditor.value = JSON.stringify(parsed, null, 2); this.renderParams(); }
      catch (error) { this.notice(error.message, true); }
    });
    const target = this.section("tunerTargetTitle", "Target video", this.grid(
      this.field("tunerTargetConnection", "Target connection", this.targetProfile), this.field("tunerTargetModel", "Video model", this.targetPicker.root)),
      this.action("tunerCopyTarget", "Copy selected video node settings", () => this.copySelectedVideo()), this.schema,
      element("details", { className: "ea-advanced" }, this.label("summary", "tunerTargetParams", "Target video parameters"), this.paramsEditor));
    this.prompt = element("textarea", { rows: 3, spellcheck: false }); this.controls.prompt = this.prompt;
    this.prompt.addEventListener("input", () => this.write("prompt", this.prompt.value));
    this.promptHint = this.label("p", "tunerLinkedPrompt", "The upstream prompt will be resolved when this node runs.", { className: "ea-caption", hidden: true });
    this.domain = this.select("domain", DOMAINS, option => this.text("wanDomain_" + option, option));
    this.mode = this.select("mode", MODES, option => this.text(option === "auto" ? "videoAuto" : "wanMode_" + option, option));
    this.output = this.select("output", OUTPUTS, option => this.text("wanOutput_" + option, option));
    this.analyze = element("input", { type: "checkbox" }); this.controls.analyze = this.analyze;
    this.analyze.addEventListener("change", () => this.write("analyze", this.analyze.checked));
    this.issue = element("textarea", { rows: 2, placeholder: this.text("wanIssuePlaceholder", "Describe what went wrong, for example, the logo changes at 3 seconds.") }); this.controls.issue = this.issue;
    this.issue.addEventListener("input", () => { this.write("issue", this.issue.value); this.updateIssue(); });
    const range = ["issue_start", "issue_end"].map(name => {
      const input = element("input", { type: "number", min: "0", step: "0.1" }); this.controls[name] = input;
      input.addEventListener("change", () => this.write(name, input.value === "" ? -1 : Number(input.value)));
      return this.field(name === "issue_start" ? "wanStart" : "wanEnd", name === "issue_start" ? "Start (seconds)" : "End (seconds)", input);
    });
    this.issueRange = this.grid(...range);
    this.issueFields = element("div", { className: "ea-tuner-issue" },
      this.field("wanIssue", "BadCase problem description (optional; enables diagnosis)", this.issue), this.issueRange);
    this.runButton = this.action("tunerRunOnly", "Tune this node", () => this.queue(false), true);
    this.retryButton = this.action("tunerRerun", "Tune again", () => this.queue(true));
    this.copyButton = this.action("wanCopyPrompt", "Copy prompt", () => this.copyPrompt()); this.copyButton.disabled = true;
    this.detailsButton = this.action("tunerDetails", "Open details", () => this.openDetails?.({ node: this.node, result: this.completed, context: this.getContext() }));
    this.detailsButton.disabled = !this.openDetails;
    this.resultPrompt = element("textarea", { rows: 4, readOnly: true });
    this.resultPrompt.setAttribute("aria-label", this.text("wanPromptResult", "Optimized prompt"));
    this.resultAnalysis = element("pre", { className: "ea-preview" });
    this.result = element("div", { className: "ea-tuner-output", hidden: true }, this.resultPrompt, this.resultAnalysis,
      element("div", { className: "ea-tuner-actions ea-row" }, this.copyButton));
    this.content.append(tuning, target, this.field("prompt", "Original prompt", this.prompt), this.promptHint,
      this.grid(this.field("wanDomain", "Scenario", this.domain), this.field("wanMode", "Generation mode", this.mode), this.field("wanOutput", "Task", this.output)),
      element("label", { className: "ea-checkbox" }, this.analyze, this.label("span", "wanAnalyzeMedia", "Analyze ready media first")), this.issueFields,
      this.label("p", "tunerUpstreamNotice", "Runs this tuner and its required upstream nodes. Upstream generators may run and incur charges; downstream video generation does not run.", { className: "ea-caption" }),
      element("div", { className: "ea-tuner-actions ea-row" }, this.runButton, this.retryButton, this.detailsButton), this.status, this.result);
    this.profile.addEventListener("change", () => {
      this.write("profile_id", this.profile.value); this.write("text_model", ""); this.write("analysis_model", "");
      this.defaultAnalysisProfile = null; this.allowAnalysisDefault = true; this.refreshPromptModels();
    });
    this.targetProfile.addEventListener("change", () => { this.write("target_profile_id", this.targetProfile.value); this.refreshVideoModels(); this.refreshSchema(); });
    this.onExecutionError = event => {
      const detail = event.detail || {};
      if (this.promptId && detail.prompt_id === this.promptId || String(detail.node_id) === String(this.node.id)) {
        this.queued = false;
        const error = new Error(detail.exception_message || this.text("tunerExecutionFailed", "Tuning failed"));
        this.notice(error.message, true); this.syncRunButtons(); this.onError?.({ node: this.node, error });
      }
    };
    this.api?.addEventListener?.("execution_error", this.onExecutionError);
    this.api?.addEventListener?.("execution_interrupted", this.onExecutionError);
    this.onExecutionSuccess = event => {
      if (this.promptId && event.detail?.prompt_id === this.promptId) {
        this.queued = false; this.syncRunButtons();
        const cached = this.app.nodeOutputs?.[this.node.id]; if (cached?.easyapi_tuning) this.update(cached);
      }
    };
    this.api?.addEventListener?.("execution_success", this.onExecutionSuccess);
    this.onProfilesChanged = () => {
      for (const request of Object.values(this.loads)) request.abort(); this.schemaAbort?.abort();
      for (const [name, picker] of [["text_model", this.textPicker], ["analysis_model", this.analysisPicker], ["target_model", this.targetPicker]]) picker.setItems([], String(value(this.node, name)));
      this.refreshProfiles();
    };
    this.onLocaleChanged = () => { this.translate(); this.refreshModels(); this.refreshSchema(); };
    globalThis.window?.addEventListener?.("easyapi:profiles", this.onProfilesChanged);
    globalThis.window?.addEventListener?.("easyapi:locale", this.onLocaleChanged);
    panels.add(this); ensureTunerScope(node); this.sync(); this.restoreDraft(); this.refreshProfiles();
  }

  text(key, fallback) { const translated = this.t?.(key); return translated && translated !== key ? translated : fallback; }
  label(tag, key, fallback, props = {}) { const node = element(tag, { ...props, textContent: this.text(key, fallback) }); this.labels.push({ node, key, fallback }); return node; }
  field(key, fallback, control) {
    control.setAttribute?.("aria-label", this.text(key, fallback)); this.fieldLabels.push({ control, key, fallback });
    return element("label", {}, this.label("span", key, fallback), control);
  }
  grid(...children) { return element("div", { className: "ea-tuner-grid" }, ...children); }
  section(key, fallback, ...children) { return element("section", { className: "ea-tuner-section" }, this.label("h4", key, fallback), ...children); }
  action(key, fallback, handler, primary = false) {
    const button = this.label("button", key, fallback, { type: "button", className: primary ? "ea-primary" : "" });
    button.addEventListener("click", () => Promise.resolve().then(handler).catch(error => this.notice(error.message, true))); return button;
  }
  select(name, options, label = option => option) {
    const input = element("select", {}, ...options.map(option => element("option", { value: option, textContent: label(option) })));
    this.controls[name] = input;
    this.optionLabels[name] = label;
    if (!["profile_id", "target_profile_id"].includes(name)) input.addEventListener("change", () => { this.write(name, input.value); this.sync(); });
    return input;
  }
  write(name, next) {
    if (linked(this.node, name)) return false;
    const target = widget(this.node, name); if (!target || target.value === next) return false;
    this.writing = true;
    try { target.value = next; target.callback?.(next); }
    finally { this.writing = false; }
    this.node.graph?.setDirtyCanvas?.(true, true); this.node.graph?.change?.();
    if (this.completed) this.notice(this.text("tunerInputsChanged", "Inputs changed. Run the tuner to refresh its output."));
    return true;
  }
  notice(text = "", error = false) { this.status.textContent = text; this.status.className = error ? "ea-error" : "ea-status"; this.resize(); }
  locale() { return this.getLocale?.() || String(value(this.node, "locale")) || document.documentElement?.lang || "en"; }
  params() { return parseParams(String(value(this.node, "target_params_json", "{}"))); }
  resize() { this.node.setDirtyCanvas?.(true, true); this.node.graph?.setDirtyCanvas?.(true, true); }
  minHeight() { return Math.max(420, Math.min(1250, this.root.scrollHeight || 740)); }
  syncRunButtons() { this.runButton.disabled = this.retryButton.disabled = Boolean(this.queued || this.disposed); }
  updateIssue() {
    this.issueRange.hidden = value(this.node, "output", "quick") !== "diagnose" &&
      !linked(this.node, "issue") && !String(value(this.node, "issue")).trim();
    this.resize();
  }
  sync() {
    if (this.disposed) return;
    for (const [name, control] of Object.entries(this.controls)) {
      const isLinked = linked(this.node, name), current = value(this.node, name);
      control.disabled = isLinked;
      if (control.type === "checkbox") control.checked = current === true;
      else control.value = isLinked && name === "prompt" ? "" : ["issue_start", "issue_end"].includes(name) && Number(current) < 0 ? "" : String(current);
    }
    this.promptHint.hidden = !linked(this.node, "prompt");
    this.prompt.hidden = linked(this.node, "prompt");
    this.paramsEditor.disabled = linked(this.node, "target_params_json");
    if (document.activeElement !== this.paramsEditor) this.paramsEditor.value = String(value(this.node, "target_params_json", "{}"));
    this.updateIssue();
    for (const [name, picker] of [["text_model", this.textPicker], ["analysis_model", this.analysisPicker], ["target_model", this.targetPicker]]) {
      picker.input.value = linked(this.node, name) ? "" : String(value(this.node, name)); picker.input.disabled = linked(this.node, name);
    }
    this.updateBacking(); this.syncRunButtons(); this.resize();
  }
  updateBacking() {
    const id = String(this.node.id).replace(/["\\]/g, "\\$&"), rules = [];
    for (const name of FIELDS) {
      const target = widget(this.node, name); if (target) target.hidden = !linked(this.node, name) || ["scope_id", "revision", "locale"].includes(name);
      const slot = this.node.inputs?.findIndex(input => input.name === name) ?? -1;
      if (slot >= 0 && target?.hidden) rules.push(`[data-node-id="${id}"] [data-testid="node-widget"]:has([data-slot-key="${id}-in-${slot}"]){display:none!important}`);
    }
    this.backingRules = rules.join("\n");
    if (!backingStyle) { backingStyle = element("style"); document.head.append(backingStyle); }
    backingStyle.textContent = [...panels].map(panel => panel.backingRules || "").join("\n");
  }
  async refreshProfiles() {
    try {
      const data = await this.loadProfiles(); if (this.disposed) return;
      this.profiles = Array.isArray(data) ? data : data.profiles || [];
      for (const [name, control] of [["profile_id", this.profile], ["target_profile_id", this.targetProfile]]) {
        const current = String(value(this.node, name)), selected = selectProfile(current, this.profiles, data.default_profile_id);
        control.replaceChildren(element("option", { value: "", textContent: this.text("chooseConnection", "Choose a connection") }),
          ...this.profiles.map(profile => element("option", { value: profile.id || profile.profile_id, textContent: profile.name || profile.id || profile.profile_id })));
        if (selected && !linked(this.node, name)) this.write(name, selected);
        control.value = selected;
      }
      this.sync(); await this.refreshModels();
    } catch (error) { if (!this.disposed) this.notice(error.message, true); }
  }
  async refreshModels(refresh = false) { await Promise.allSettled([this.refreshPromptModels(refresh), this.refreshVideoModels(refresh)]); }
  async loadCatalog(kind, profileId, loader, apply, refresh) {
    this.loads[kind]?.abort(); const controller = new AbortController(); this.loads[kind] = controller;
    const active = () => !this.disposed && this.loads[kind] === controller && !controller.signal.aborted;
    if (!profileId || !loader) { apply([]); return; }
    this.modelsStatus.textContent = this.text("loadingModels", "Loading models…");
    const update = state => {
      if (!active()) return;
      if (state.invalidated) { controller.abort(); apply([]); this.notice(this.text("authorityError", "Model permissions are unavailable"), true); return; }
      if (state.models || state.value) apply(list(state.models || state.value));
      this.modelsStatus.textContent = state.error ? this.text("modelUpdateFailed", "Could not update models") : state.updating && state.value ? this.text("updatingModels", "Updating models…") : "";
    };
    try {
      const models = await loader(profileId, controller.signal, update, { profiles: this.profiles, refresh });
      if (active()) { apply(list(models)); this.modelsStatus.textContent = ""; }
    } catch (error) { if (active()) this.notice(error.message, true); }
  }
  async refreshPromptModels(refresh = false) {
    const profile = linked(this.node, "profile_id") ? "" : String(value(this.node, "profile_id"));
    this.promptModels = [];
    this.unavailableModels = {};
    this.showModelPreferenceStatus();
    return this.loadCatalog("prompt", profile, this.loadPromptModels, models => {
      this.promptModels = models;
      const visualModels = models.filter(visual);
      const preferences = profile && this.usePromptModelDefaults ? this.promptModelPreferences : null;
      if (!value(this.node, "text_model") && !linked(this.node, "text_model")) {
        const fallback = models.length === 1 ? models[0].id : undefined;
        const tune = preferences?.resolve(profile, "tune", models, [fallback]);
        const selected = tune?.value ?? fallback;
        if (selected) this.write("text_model", selected);
        this.unavailableModels.tune = tune?.unavailable;
      }
      if (preferences && this.allowAnalysisDefault && !value(this.node, "analysis_model") && !linked(this.node, "analysis_model")) {
        const analysis = preferences.resolve(profile, "analysis", visualModels,
          [value(this.node, "text_model") || undefined, visualModels.length === 1 ? visualModels[0].id : undefined]);
        if (analysis.value) this.write("analysis_model", analysis.value);
        this.unavailableModels.analysis = analysis.unavailable;
      } else if (!preferences && models.length && this.allowAnalysisDefault && this.defaultAnalysisProfile !== profile) {
        this.defaultAnalysisProfile = profile;
        if (!value(this.node, "analysis_model")) {
          const preferred = visualModels.find(model => model.id === value(this.node, "text_model")) || (visualModels.length === 1 ? visualModels[0] : null);
          if (preferred) this.write("analysis_model", preferred.id);
        }
      }
      this.textPicker.setItems(models, String(value(this.node, "text_model")));
      this.analysisPicker.setItems(visualModels, String(value(this.node, "analysis_model")));
      this.showModelPreferenceStatus();
      this.sync();
    }, refresh);
  }
  async refreshVideoModels(refresh = false) {
    const profile = linked(this.node, "target_profile_id") ? "" : String(value(this.node, "target_profile_id"));
    return this.loadCatalog("video", profile, this.loadVideoModels, models => {
      this.videoModels = models;
      if (!value(this.node, "target_model") && models.length === 1) this.write("target_model", models[0].id);
      this.targetPicker.setItems(models, String(value(this.node, "target_model"))); this.sync(); this.refreshSchema();
    }, refresh);
  }
  chooseModel(name, id) {
    if (name === "analysis_model") this.allowAnalysisDefault = false;
    if (this.write(name, id)) { this.sync(); if (name === "target_model") this.refreshSchema(); }
    const role = name === "text_model" ? "tune" : name === "analysis_model" ? "analysis" : null;
    const profile = linked(this.node, "profile_id") ? "" : String(value(this.node, "profile_id"));
    if (!role || !profile || !this.promptModelPreferences || linked(this.node, name)) return;
    if (!(role === "analysis" && id === "") && !this.promptModels?.some(model => model.id === id && (role !== "analysis" || visual(model)))) return;
    if (this.unavailableModels) delete this.unavailableModels[role];
    this.showModelPreferenceStatus(!this.promptModelPreferences.remember(profile, role, id));
  }

  showModelPreferenceStatus(saveFailed = false) {
    const unavailable = [...new Set(Object.values(this.unavailableModels || {}).filter(Boolean))];
    const message = saveFailed ? this.text("promptModelPreferenceSaveFailed", "Could not remember the model selection.") :
      unavailable.map(model => this.t?.("promptModelUnavailable", { model }) || `The default model ${model} is unavailable.`).join(" ");
    this.modelPreferenceStatus.textContent = message;
    this.modelPreferenceStatus.hidden = !message;
  }
  async refreshSchema() {
    this.schemaAbort?.abort(); this.schemaAbort = new AbortController(); const request = this.schemaAbort;
    this.formSchema = null; this.renderParams();
    if (!this.loadTargetSchema || ["target_profile_id", "target_model", "target_params_json"].some(name => linked(this.node, name))) return;
    const targetProfileId = String(value(this.node, "target_profile_id")), modelId = String(value(this.node, "target_model"));
    if (!targetProfileId || !modelId) return;
    try {
      const metadata = await this.loadTargetSchema({ targetProfileId, profileId: targetProfileId, modelId, locale: this.locale() }, request.signal);
      if (request.signal.aborted || this.disposed) return;
      this.formSchema = metadata?.matched === false || metadata?.declared === false ? null : normalizedSchema(metadata);
      this.renderParams();
    } catch (error) { if (!request.signal.aborted && !this.disposed) this.notice(error.message, true); }
  }
  renderParams() {
    this.schema.replaceChildren(); if (!this.formSchema || linked(this.node, "target_params_json")) return;
    try {
      this.params();
      renderSchemaForm(this.schema, this.formSchema, () => this.params(), next => {
        this.write("target_params_json", JSON.stringify(next)); this.paramsEditor.value = JSON.stringify(next, null, 2);
      }, (key, argument) => {
        const translated = this.t?.(key, argument);
        return translated && translated !== key ? translated : typeof argument === "string" ? argument : key;
      }, () => this.resize());
    } catch (error) { this.notice(error.message, true); }
  }
  copySelectedVideo() {
    const selected = Object.values(this.app.canvas?.selected_nodes || {}).filter(node => (node.comfyClass || node.type) === "EasyAPIVideo");
    if (selected.length !== 1) throw new Error(this.text("tunerSelectVideo", "Select one EasyAPI Video node to copy its settings."));
    const source = selected[0];
    if (["profile_id", "model", "params_json"].some(name => linked(source, name))) throw new Error(this.text("tunerLinkedTarget", "The selected video settings come from links. Connect or enter resolved target settings explicitly."));
    if (["target_profile_id", "target_model", "target_params_json"].some(name => linked(this.node, name))) throw new Error(this.text("linkedInput", "A connected input supplies this value."));
    const params = parseParams(String(value(source, "params_json", "{}")));
    for (const [name, next] of Object.entries({ target_profile_id: String(value(source, "profile_id")), target_model: String(value(source, "model")), target_params_json: JSON.stringify(params) })) {
      this.write(name, next);
    }
    this.sync(); this.refreshVideoModels(); this.refreshSchema();
  }
  getContext() {
    const local = name => linked(this.node, name) ? "" : String(value(this.node, name));
    const profileId = local("target_profile_id"), modelId = local("target_model");
    let params = {}; if (!linked(this.node, "target_params_json")) try { params = this.params(); } catch { /* Keep invalid JSON in its editor. */ }
    const media = (this.node.inputs || []).filter(input => input.link != null && ["references", "image", "video", "audio", "diagnostic_video"].includes(input.name)).map(input => ({
      id: "linked-" + input.name, kind: input.name === "diagnostic_video" ? "video" : input.name === "references" ? "group" : input.name,
      role: input.name === "diagnostic_video" ? "result_video" : "reference_" + input.name, ready: false, analysisOnly: input.name === "diagnostic_video", name: input.name,
    }));
    const description = local("issue").trim();
    const issueRangeLinked = linked(this.node, "issue_start") || linked(this.node, "issue_end");
    const start = Number(value(this.node, "issue_start", -1)), end = Number(value(this.node, "issue_end", -1));
    const issue = description ? { description, ...(!issueRangeLinked ? {
      ...(start >= 0 ? { start } : {}), ...(end >= 0 ? { end } : {}),
    } : {}) } : null;
    return { profileId, targetProfileId: profileId, targetModel: modelId, modelId, prompt: local("prompt"), promptLinked: linked(this.node, "prompt"),
      params, paramsLinked: linked(this.node, "target_params_json"), profiles: this.profiles, operation: "video-generation", scopeId: value(this.node, "scope_id"),
      domain: local("domain"), mode: local("mode"), output: local("output"), issue, issueLinked: linked(this.node, "issue"), issueRangeLinked,
      locale: this.locale(), media, resultMedia: [],
      tuningProfileId: local("profile_id"), textModel: local("text_model"), analysisModel: local("analysis_model") };
  }
  formSnapshot() { return Object.fromEntries(FIELDS.filter(name => !["scope_id", "revision"].includes(name)).map(name => [name, linked(this.node, name) ? null : value(this.node, name)])); }
  getFormState() { return this.formSnapshot(); }
  setFormState(next) {
    const changed = new Set();
    if (Object.hasOwn(next || {}, "analysis_model")) this.allowAnalysisDefault = false;
    for (const [name, current] of Object.entries(next || {})) {
      if (!FIELDS.includes(name) || ["scope_id", "revision"].includes(name) || !["string", "number", "boolean"].includes(typeof current)) continue;
      if (this.write(name, current)) changed.add(name);
    }
    this.sync();
    if (changed.has("profile_id")) this.refreshPromptModels();
    if (changed.has("target_profile_id")) this.refreshVideoModels();
    if (changed.has("target_profile_id") || changed.has("target_model")) this.refreshSchema();
    else if (changed.has("target_params_json")) this.renderParams();
  }
  restoreDraft() {
    const draft = readTuningDraft(String(value(this.node, "scope_id")));
    if (draft?.completed) this.displayResult(draft.completed, true);
  }
  displayResult(record, restored = false) {
    const result = record.result && typeof record.result === "object" ? record.result : record;
    const prompt = record.prompt ?? result.prompt, analysis = record.analysis ?? result.analysis;
    if (typeof prompt !== "string") return;
    this.completed = { ...result, prompt, analysis: typeof analysis === "string" ? analysis : JSON.stringify(analysis || "") };
    this.resultPrompt.value = prompt; this.resultAnalysis.textContent = this.completed.analysis; this.result.hidden = false;
    this.copyButton.disabled = false; this.detailsButton.disabled = !this.openDetails; this.queued = false; this.syncRunButtons();
    this.notice(this.text(restored ? "tunerRestored" : "tunerReady", restored ? "Restored preview. Execution uses the server's validated cache." : "Tuning finished. The prompt output is ready."));
  }
  update(message) {
    const record = Array.isArray(message?.easyapi_tuning) ? message.easyapi_tuning[0] : message?.easyapi_tuning;
    if (!record) return;
    this.displayResult(record);
    if (this.completed) writeTuningDraft(String(value(this.node, "scope_id")), { completed: this.completed, partial: this.completed.prompt,
      form: this.formSnapshot(), contextFingerprint: tuningFingerprint(this.getContext()) });
    if (this.completed) this.onResult?.({ node: this.node, result: this.completed });
  }
  run(retry = false) { return this.queue(retry); }
  async queue(retry) {
    if (this.queued || this.disposed) return;
    if (!linked(this.node, "target_params_json")) {
      const params = parseParams(this.paramsEditor.value);
      this.write("target_params_json", JSON.stringify(params));
      this.paramsEditor.value = JSON.stringify(params, null, 2);
    }
    if (!linked(this.node, "locale")) this.write("locale", this.locale());
    this.queued = true; this.syncRunButtons(); this.notice(this.text("tunerQueued", "Tuner queued. Its upstream inputs will run if needed."));
    try { const response = await queueVideoTunerNode(this.node, { app: this.app, api: this.api, retry }); this.promptId = response.prompt_id; return response; }
    catch (error) { this.queued = false; this.syncRunButtons(); throw error; }
  }
  async copyPrompt() {
    if (!this.completed?.prompt) return;
    try { await navigator.clipboard.writeText(this.completed.prompt); }
    catch {
      const area = element("textarea", { value: this.completed.prompt, readOnly: true });
      area.style.position = "fixed"; area.style.opacity = "0"; document.body.append(area); area.select();
      try { if (!document.execCommand?.("copy")) throw new Error(this.text("wanCopyFailed", "Copy failed")); }
      finally { area.remove(); }
    }
  }
  configure() { this.usePromptModelDefaults = this.allowAnalysisDefault = false; ensureTunerScope(this.node); this.sync(); this.restoreDraft(); this.refreshProfiles(); }
  backingChanged(name) {
    if (this.writing || this.disposed) return;
    this.sync();
    if (name === "profile_id") this.refreshPromptModels();
    if (name === "target_profile_id") this.refreshVideoModels();
    if (["target_profile_id", "target_model"].includes(name)) this.refreshSchema();
  }
  translate() {
    for (const { node, key, fallback } of this.labels) node.textContent = this.text(key, fallback);
    for (const { control, key, fallback } of this.fieldLabels) control.setAttribute?.("aria-label", this.text(key, fallback));
    for (const name of ["domain", "mode", "output"]) for (const option of this.controls[name].children) option.textContent = this.optionLabels[name](option.value);
    this.textPicker.input.setAttribute("aria-label", this.text("promptTransformModel", "Text model"));
    this.analysisPicker.input.setAttribute("aria-label", this.text("wanAnalysisModel", "Media analysis model"));
    this.targetPicker.input.setAttribute("aria-label", this.text("tunerTargetModel", "Video model"));
    this.issue.placeholder = this.text("wanIssuePlaceholder", "Describe what went wrong, for example, the logo changes at 3 seconds.");
    this.sync();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; for (const request of Object.values(this.loads)) request.abort(); this.schemaAbort?.abort();
    for (const name of ["execution_error", "execution_interrupted"]) this.api?.removeEventListener?.(name, this.onExecutionError);
    this.api?.removeEventListener?.("execution_success", this.onExecutionSuccess);
    globalThis.window?.removeEventListener?.("easyapi:profiles", this.onProfilesChanged);
    globalThis.window?.removeEventListener?.("easyapi:locale", this.onLocaleChanged);
    for (const picker of [this.textPicker, this.analysisPicker, this.targetPicker]) picker.dispose?.();
    panels.delete(this); if (backingStyle) backingStyle.textContent = [...panels].map(panel => panel.backingRules || "").join("\n");
    this.onDispose?.({ node: this.node });
  }
}

export function installVideoTunerNode(nodeType, services) {
  const created = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function (...args) {
    const result = created?.apply(this, args);
    const panel = this.easyapiTunerPanel = new VideoTunerNodePanel(this, services);
    this.addDOMWidget("easyapi_video_tuning", "easyapi_video_tuning", panel.root, { serialize: false, hideOnZoom: false, margin: 0, getMinHeight: () => panel.minHeight() });
    for (const name of FIELDS) {
      const target = widget(this, name); if (!target) continue; const callback = target.callback;
      target.callback = function (...values) { const output = callback?.apply(this, values); panel.backingChanged(name); return output; };
    }
    const configure = this.onConfigure; this.onConfigure = (...values) => { configure?.apply(this, values); panel.configure(); };
    const added = this.onAdded; this.onAdded = (...values) => { added?.apply(this, values); ensureTunerScope(this); panel.sync(); };
    const connections = this.onConnectionsChange; this.onConnectionsChange = (...values) => { connections?.apply(this, values); panel.sync(); panel.refreshModels(); panel.refreshSchema(); };
    const executed = this.onExecuted; this.onExecuted = (message, ...values) => { executed?.call(this, message, ...values); panel.update(message); };
    const removed = this.onRemoved; this.onRemoved = (...values) => { panel.dispose(); removed?.apply(this, values); };
    this.size = [Math.max(430, this.size?.[0] || 0), Math.max(740, this.size?.[1] || 0)];
    return result;
  };
}
