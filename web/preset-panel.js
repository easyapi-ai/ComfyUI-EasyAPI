import { t, localeQuery } from "./ui-i18n.js";
import { parseParams, selectProfile, normalizedSchema, requiredDefaults } from "./ui-state.js";
import { renderSchemaForm } from "./ui-forms.js";

function make(tag, props = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, props);
  for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
export class PresetPanel {
  constructor({ request, mutation, modelPicker, getOperations, catalog, onChanged = () => {} }) {
    this.request = request; this.mutation = mutation; this.getOperations = getOperations; this.onChanged = onChanged;
    this.catalog = catalog;
    this.presets = []; this.defaults = {}; this.params = {}; this.models = []; this.profiles = []; this.labels = [];
    this.model = ""; this.listSequence = 0; this.modelSequence = 0; this.schemaSequence = 0;
    this.root = make("section", { className: "ea-manager-view ea-presets", hidden: true });
    this.profile = make("select"); this.operation = make("select"); this.name = make("input", { autocomplete: "off" });
    this.picker = modelPicker((id) => { this.editId = null; this.model = id; this.params = {}; this.defaultCheck.checked = false; this.syncJSON(); this.refreshSchema(); });
    this.picker.input.disabled = true;
    this.modelField = make("div", {}, this.label("label", "model", { htmlFor: this.picker.input.id }), this.picker.root);
    this.notice = make("div", { className: "ea-status", role: "status" });
    this.catalogStatus = make("span", { className: "ea-caption", role: "status" });
    this.retryModels = this.action("retryModels", () => this.refreshModels(true)); this.retryModels.hidden = true;
    this.list = make("div", { className: "ea-preset-list" }); this.schema = make("div", { className: "ea-schema" });
    this.defaultCheck = make("input", { type: "checkbox" });
    this.jsonEditor = make("textarea", { rows: 7, spellcheck: false });
    this.jsonEditor.addEventListener("change", () => {
      try { this.params = parseParams(this.jsonEditor.value); this.renderForm(); this.showNotice(); }
      catch (error) { this.showNotice(t(error.message), true); }
    });
    this.jsonDetails = make("details", { className: "ea-advanced" }, this.label("summary", "advancedJson"), this.jsonEditor);
    this.saveButton = this.action("savePreset", () => this.save(), true);
    this.deleteButton = this.action("deletePreset", () => this.remove());
    this.profile.addEventListener("change", () => { this.renderProfiles(this.profile.value); this.changeScope(); });
    this.operation.addEventListener("change", () => this.changeScope());
    this.root.append(this.label("p", "presetDescription", { className: "ea-caption" }), this.field("connection", this.profile),
      this.field("presetOperation", this.operation), this.list, this.action("newPreset", () => this.newPreset()),
      this.field("presetName", this.name), this.modelField, this.notice,
      make("div", { className: "ea-model-updates" }, this.catalogStatus, this.retryModels), this.schema, this.jsonDetails,
      make("label", { className: "ea-checkbox" }, this.defaultCheck, this.label("span", "makeDefaultPreset")),
      make("div", { className: "ea-row" }, this.saveButton, this.deleteButton));
    this.renderProfiles(""); this.syncOperations(); this.syncJSON(); this.updateDisabled();
  }
  label(tag, key, props = {}) { const node = make(tag, props, t(key)); this.labels.push({ node, key }); return node; }
  field(key, input) { return make("label", {}, this.label("span", key), input); }
  action(key, handler, primary = false) {
    const node = this.label("button", key, { type: "button", className: primary ? "ea-primary" : "" });
    node.addEventListener("click", () => Promise.resolve(handler()).catch((error) => this.showNotice(t(error.message), true))); return node;
  }
  showNotice(text = "", error = false) { this.notice.className = error ? "ea-error" : "ea-status"; this.notice.textContent = text; }
  syncOperations(preferred) {
    const current = preferred || this.operation.value;
    this.operations = this.getOperations();
    this.operation.replaceChildren(...this.operations.map((operation) => make("option", { value: operation.id }, t("operation." + operation.id, operation.label || operation.id))));
    this.operation.value = this.operations.some((operation) => operation.id === current) ? current : this.operations[0]?.id || "";
  }
  requiresModel() { return this.operations.find((operation) => operation.id === this.operation.value)?.hasModel !== false; }
  setProfiles(profiles, defaultId, preferred) {
    const current = this.profile.value;
    const revision = this.profiles.find((row) => row.id === current)?.model_catalog_revision;
    this.profiles = profiles;
    const existing = [preferred, current].find((id) => profiles.some((profile) => profile.id === id));
    const next = selectProfile(existing, profiles, defaultId);
    this.renderProfiles(next);
    if (current !== next) this.changeScope();
    else if (revision !== profiles.find((row) => row.id === next)?.model_catalog_revision) this.refreshModels();
  }
  renderProfiles(selected) {
    const options = this.profiles.map((profile) => make("option", { value: profile.id }, profile.name || profile.id));
    if (!selected) options.unshift(make("option", { value: "", disabled: true, hidden: true }, t("chooseConnection")));
    this.profile.replaceChildren(...options);
    this.profile.value = selected;
    this.profile.disabled = !this.profiles.length;
  }
  changeScope() {
    this.editId = null; this.model = ""; this.params = {}; this.defaultCheck.checked = false;
    this.presets = []; this.defaults = {}; this.list.replaceChildren(); this.syncJSON();
    this.refresh(); this.refreshModels();
  }
  async refresh() {
    const profile = this.profile.value, operation = this.operation.value;
    const sequence = ++this.listSequence;
    if (!profile || !operation) { this.presets = []; this.defaults = {}; this.renderList(); this.updateDisabled(); return; }
    try {
      const data = await this.request(`/easyapi/presets?${new URLSearchParams({ profile_id: profile, operation })}`);
      if (this.destroyed || sequence !== this.listSequence) return;
      this.presets = data.presets || []; this.defaults = data.default_preset_ids || {}; this.renderList(); this.updateDisabled();
    } catch (error) {
      if (!this.destroyed && sequence === this.listSequence) this.showNotice(error.message, true);
    }
  }
  renderList() {
    this.list.replaceChildren(...this.presets.map((preset) => {
      const node = make("button", { type: "button", className: "ea-preset-card" },
        make("span", {}, preset.name), make("small", { className: "ea-caption" }, preset.model || t("toolModelManaged")),
        ...(this.defaults[preset.operation] === preset.id ? [make("small", { className: "ea-default-badge" }, t("defaultPreset"))] : []));
      node.addEventListener("click", () => this.edit(preset)); return node;
    }));
    if (!this.presets.length) this.list.append(make("p", { className: "ea-caption" }, t("noPresets")));
  }
  newPreset() {
    this.editId = null; this.name.value = ""; this.model = ""; this.params = {}; this.defaultCheck.checked = false;
    this.clearSchema(); this.syncJSON(); this.picker.setItems(this.models, ""); this.updateDisabled();
  }
  async newFromNode(record) {
    const params = parseParams(record.params);
    if (this.profile.value !== record.profile_id || this.operation.value !== record.operation) {
      this.presets = []; this.defaults = {}; this.list.replaceChildren();
    }
    this.syncOperations(record.operation); this.renderProfiles(record.profile_id);
    this.editId = null; this.name.value = ""; this.model = record.model || ""; this.params = params;
    this.defaultCheck.checked = false; this.syncJSON();
    await Promise.all([this.refresh(), this.refreshModels()]);
    if (!this.destroyed && this.params === params && !this.notice.textContent) this.showNotice(t("presetFromNode"));
  }
  async edit(preset) {
    this.editId = preset.id; this.profile.value = preset.profile_id; this.syncOperations(preset.operation);
    this.name.value = preset.name; this.model = preset.model || ""; this.params = parseParams(preset.params);
    this.defaultCheck.checked = this.defaults[preset.operation] === preset.id; this.syncJSON();
    await this.refreshModels();
  }
  async refreshModels(refresh = false) {
    this.modelUnsubscribe?.(); this.modelAbort?.abort();
    this.modelAbort = new AbortController();
    const profile = this.profile.value, operation = this.operation.value;
    const sequence = ++this.modelSequence;
    const needsModel = this.requiresModel();
    this.modelField.hidden = !needsModel;
    this.clearSchema(); this.models = []; this.picker.setItems([], this.model); this.picker.input.disabled = true;
    this.loadingModels = Boolean(profile && operation && needsModel);
    this.updateDisabled();
    if (!profile || !operation) { this.showNotice(); return; }
    if (!needsModel) { this.model = ""; this.showNotice(t("toolModelManaged")); return; }
    this.showNotice(t("loadingModels")); this.catalogStatus.textContent = ""; this.retryModels.hidden = true;
    let applied, initialized = false;
    const active = () => !this.destroyed && sequence === this.modelSequence && !this.modelAbort.signal.aborted;
    const applyModels = (data) => {
      if (!active() || applied === data) return;
      applied = data;
      this.models = (data.models || []).map((item) => typeof item === "string" ? { id: item } : { ...item, id: item.id || item.name });
      this.loadingModels = false;
      this.picker.setItems(this.models, this.model);
      this.picker.input.disabled = !this.models.length;
      this.showNotice(this.model && !this.models.some((item) => item.id === this.model) ? t("missingModel") : this.models.length ? "" : t("noModels"));
      this.updateDisabled();
      if (initialized && this.models.some((item) => item.id === this.model)) this.refreshSchema();
      else if (initialized) this.clearSchema();
    };
    const family = operation.startsWith("image-") ? "image" : operation.startsWith("video-") ? "video" : /^(audio-|realtime)/.test(operation) ? "audio" : "text";
    const scope = { profileId: profile, operation, family, locale: localeQuery(),
      revision: this.profiles.find((row) => row.id === profile)?.model_catalog_revision };
    if (this.catalog) this.modelUnsubscribe = this.catalog.subscribe(scope, (state) => {
      if (!active()) return;
      if (state.invalidated) {
        this.modelAbort.abort(); this.loadingModels = false;
        this.models = []; this.picker.setItems([], this.model); this.picker.input.disabled = true; this.clearSchema();
        this.showNotice(t("authorityError"), true); this.updateDisabled();
      } else if (state.value) applyModels(state.value);
      this.catalogStatus.textContent = state.error ? t("modelUpdateFailed") : state.updating && state.value ? t("updatingModels") : "";
      this.retryModels.hidden = !state.error;
    });
    try {
      const data = this.catalog ? await this.catalog.get({ ...scope, refresh, signal: this.modelAbort.signal }) :
        await this.request(`/easyapi/models?${new URLSearchParams({ profile_id: profile, operation, locale: localeQuery() })}`);
      if (!active()) return;
      applyModels(data); initialized = true;
      if (this.models.some((item) => item.id === this.model)) await this.refreshSchema();
      else this.clearSchema();
    } catch (error) {
      if (!this.destroyed && sequence === this.modelSequence) {
        this.loadingModels = false; this.updateDisabled(); this.showNotice(error.message, true); this.retryModels.hidden = false;
      }
    }
  }
  clearSchema() {
    ++this.schemaSequence; this.loadingSchema = false;
    this.metadata = this.formSchema = null; this.schema.replaceChildren();
  }
  async refreshSchema() {
    this.clearSchema();
    const profile = this.profile.value, operation = this.operation.value, model = this.model;
    this.loadingSchema = Boolean(profile && operation && model);
    this.updateDisabled();
    if (!this.loadingSchema) return;
    const sequence = this.schemaSequence;
    try {
      const metadata = await this.request(`/easyapi/schema?${new URLSearchParams({ profile_id: profile, model, operation, locale: localeQuery() })}`);
      if (this.destroyed || sequence !== this.schemaSequence) return;
      this.metadata = metadata; this.formSchema = normalizedSchema(metadata);
      this.renderForm();
    } catch (error) {
      if (!this.destroyed && sequence === this.schemaSequence) this.showNotice(error.message, true);
    } finally {
      if (!this.destroyed && sequence === this.schemaSequence) { this.loadingSchema = false; this.updateDisabled(); }
    }
  }
  renderForm() {
    renderSchemaForm(this.schema, this.formSchema || {}, () => this.params, (next) => { this.params = parseParams(next); this.syncJSON(); }, t);
  }
  syncJSON() { this.jsonEditor.value = JSON.stringify(this.params, null, 2); }
  updateDisabled() {
    this.saveButton.disabled = Boolean(this.loadingModels || this.loadingSchema || !this.profile.value || !this.operation.value || (this.requiresModel() && !this.models.some((item) => item.id === this.model)));
    this.deleteButton.disabled = !this.editId;
  }
  async save() {
    if (this.saveButton.disabled) return;
    if (!this.name.value.trim()) throw new Error(t("presetNameRequired"));
    let params = parseParams(this.jsonEditor.value);
    if (this.metadata?.matched && this.metadata?.declared) params = requiredDefaults(this.formSchema || {}, params);
    const body = { profile_id: this.profile.value, operation: this.operation.value, model: this.model || "", name: this.name.value.trim(), params, make_default: this.defaultCheck.checked };
    const result = await this.mutation(this.editId ? `/easyapi/presets/${encodeURIComponent(this.editId)}` : "/easyapi/presets", body, this.editId ? "PUT" : "POST");
    this.editId = result.preset?.id || result.id || this.editId; this.params = params; this.syncJSON();
    await this.refresh(); await this.onChanged(); this.showNotice(t("presetSaved"));
  }
  async remove() {
    if (!this.editId) return;
    await this.mutation(`/easyapi/presets/${encodeURIComponent(this.editId)}?${new URLSearchParams({ profile_id: this.profile.value })}`, undefined, "DELETE");
    this.newPreset(); await this.refresh(); await this.onChanged();
  }
  translate() {
    for (const entry of this.labels) entry.node.textContent = t(entry.key);
    this.renderProfiles(this.profile.value); this.syncOperations(); this.renderList(); this.picker.translate(); this.refreshModels();
  }
  destroy() { this.destroyed = true; this.modelUnsubscribe?.(); this.modelAbort?.abort(); ++this.listSequence; ++this.modelSequence; ++this.schemaSequence; this.root.remove(); }
}
