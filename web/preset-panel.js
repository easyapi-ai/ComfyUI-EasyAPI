import { t, localeQuery } from "./ui-i18n.js";
import { parseParams, selectProfile, normalizedSchema, requiredDefaults } from "./ui-state.js";
import { renderSchemaForm } from "./ui-forms.js";

function make(tag, props = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, props);
  for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
export class PresetPanel {
  constructor({ request, mutation, modelPicker, getOperations, onChanged = () => {} }) {
    this.request = request; this.mutation = mutation; this.getOperations = getOperations; this.onChanged = onChanged;
    this.presets = []; this.defaults = {}; this.params = {}; this.models = []; this.profiles = []; this.labels = [];
    this.root = make("section", { className: "ea-manager-view ea-presets", hidden: true });
    this.profile = make("select"); this.operation = make("select"); this.name = make("input", { autocomplete: "off" });
    this.picker = modelPicker((id) => { this.editId = null; this.model = id; this.params = {}; this.defaultCheck.checked = false; this.syncJSON(); this.refreshSchema(); });
    this.modelField = this.field("model", this.picker.root);
    this.notice = make("div", { className: "ea-status", role: "status" });
    this.list = make("div", { className: "ea-preset-list" }); this.schema = make("div", { className: "ea-schema" });
    this.defaultCheck = make("input", { type: "checkbox" });
    this.jsonEditor = make("textarea", { rows: 7, spellcheck: false });
    this.jsonEditor.addEventListener("change", () => {
      try { this.params = parseParams(this.jsonEditor.value); this.renderForm(); this.notice.textContent = ""; }
      catch (error) { this.notice.textContent = t(error.message); }
    });
    this.jsonDetails = make("details", { className: "ea-advanced" }, this.label("summary", "advancedJson"), this.jsonEditor);
    this.saveButton = this.action("savePreset", () => this.save(), true);
    this.deleteButton = this.action("deletePreset", () => this.remove());
    const changeScope = () => { this.editId = null; this.model = ""; this.params = {}; this.defaultCheck.checked = false; this.syncJSON(); this.refresh(); this.refreshModels(); };
    this.profile.addEventListener("change", changeScope); this.operation.addEventListener("change", changeScope);
    this.root.append(this.label("p", "presetDescription", { className: "ea-caption" }), this.field("connection", this.profile),
      this.field("presetOperation", this.operation), this.list, this.action("newPreset", () => this.newPreset()),
      this.field("presetName", this.name), this.modelField, this.notice, this.schema, this.jsonDetails,
      make("label", { className: "ea-checkbox" }, this.defaultCheck, this.label("span", "makeDefaultPreset")),
      make("div", { className: "ea-row" }, this.saveButton, this.deleteButton));
    this.syncOperations(); this.syncJSON(); this.updateDisabled();
  }
  label(tag, key, props = {}) { const node = make(tag, props, t(key)); this.labels.push({ node, key }); return node; }
  field(key, input) { return make("label", {}, this.label("span", key), input); }
  action(key, handler, primary = false) {
    const node = this.label("button", key, { type: "button", className: primary ? "ea-primary" : "" });
    node.addEventListener("click", () => Promise.resolve(handler()).catch((error) => { this.notice.className = "ea-error"; this.notice.textContent = t(error.message); })); return node;
  }
  syncOperations(preferred) {
    const current = preferred || this.operation.value;
    this.operations = this.getOperations();
    this.operation.replaceChildren(...this.operations.map((operation) => make("option", { value: operation.id }, t("operation." + operation.id, operation.label || operation.id))));
    this.operation.value = this.operations.some((operation) => operation.id === current) ? current : this.operations[0]?.id || "";
  }
  requiresModel() { return this.operations.find((operation) => operation.id === this.operation.value)?.hasModel !== false; }
  setProfiles(profiles, defaultId, preferred) {
    const current = preferred || this.profile.value;
    this.profiles = profiles;
    this.profile.replaceChildren(make("option", { value: "" }, t("chooseConnection")), ...profiles.map((profile) => make("option", { value: profile.id }, profile.name || profile.id)));
    const next = selectProfile(current, profiles, defaultId);
    this.profile.value = next;
    if (next && this.loadedProfile !== next) { this.loadedProfile = next; this.editId = null; this.refresh(); this.refreshModels(); }
  }
  async refresh() {
    const profile = this.profile.value, operation = this.operation.value;
    if (!profile) { this.presets = []; this.renderList(); this.updateDisabled(); return; }
    const sequence = (this.listSequence || 0) + 1; this.listSequence = sequence;
    const data = await this.request(`/easyapi/presets?${new URLSearchParams({ profile_id: profile, operation })}`);
    if (this.destroyed || sequence !== this.listSequence) return;
    this.presets = data.presets || []; this.defaults = data.default_preset_ids || {}; this.renderList(); this.updateDisabled();
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
    this.metadata = this.formSchema = null; this.schema.replaceChildren(); this.syncJSON(); this.picker.setItems(this.models, ""); this.updateDisabled();
  }
  async newFromNode(record) {
    this.syncOperations(record.operation); this.profile.value = record.profile_id; this.loadedProfile = record.profile_id;
    this.editId = null; this.name.value = ""; this.model = record.model || ""; this.params = parseParams(record.params);
    this.models = []; this.metadata = this.formSchema = null; this.schema.replaceChildren(); this.updateDisabled();
    this.defaultCheck.checked = false; this.notice.textContent = t("presetFromNode"); this.syncJSON();
    await this.refresh(); await this.refreshModels();
  }
  async edit(preset) {
    this.editId = preset.id; this.profile.value = preset.profile_id; this.syncOperations(preset.operation);
    this.name.value = preset.name; this.model = preset.model || ""; this.params = parseParams(preset.params);
    this.models = []; this.metadata = this.formSchema = null; this.schema.replaceChildren(); this.updateDisabled();
    this.defaultCheck.checked = this.defaults[preset.operation] === preset.id; this.syncJSON();
    await this.refreshModels();
  }
  async refreshModels() {
    const profile = this.profile.value, operation = this.operation.value;
    this.modelField.hidden = !this.requiresModel();
    if (!profile || !operation) { this.updateDisabled(); return; }
    const sequence = (this.modelSequence || 0) + 1; this.modelSequence = sequence;
    if (!this.requiresModel()) { this.models = []; this.model = ""; this.schema.replaceChildren(); this.notice.textContent = t("toolModelManaged"); this.updateDisabled(); return; }
    this.loadingModels = true; this.metadata = this.formSchema = null; this.schema.replaceChildren(); this.notice.textContent = t("loadingModels"); this.updateDisabled();
    try {
      const data = await this.request(`/easyapi/models?${new URLSearchParams({ profile_id: profile, operation, locale: localeQuery() })}`);
      if (this.destroyed || sequence !== this.modelSequence) return;
      this.models = (data.models || []).map((item) => typeof item === "string" ? { id: item } : { ...item, id: item.id || item.name });
      this.picker.setItems(this.models, this.model);
      this.notice.textContent = this.model && !this.models.some((item) => item.id === this.model) ? t("missingModel") : "";
      this.updateDisabled(); await this.refreshSchema();
    } catch (error) { this.notice.textContent = error.message; this.models = []; }
    finally { if (sequence === this.modelSequence) { this.loadingModels = false; this.updateDisabled(); } }
  }
  async refreshSchema() {
    if (!this.profile.value || !this.model) { this.schema.replaceChildren(); return; }
    const key = JSON.stringify([this.profile.value, this.operation.value, this.model]); this.schemaKey = key;
    this.loadingSchema = true; this.metadata = this.formSchema = null; this.schema.replaceChildren(); this.updateDisabled();
    try {
      const metadata = await this.request(`/easyapi/schema?${new URLSearchParams({ profile_id: this.profile.value, model: this.model, operation: this.operation.value, locale: localeQuery() })}`);
      if (this.destroyed || this.schemaKey !== key) return;
      this.metadata = metadata; this.formSchema = normalizedSchema(metadata);
      this.renderForm(); this.updateDisabled();
    } catch (error) { this.notice.textContent = error.message; }
    finally { if (key === this.schemaKey) { this.loadingSchema = false; this.updateDisabled(); } }
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
    await this.refresh(); await this.onChanged(); this.notice.className = "ea-status"; this.notice.textContent = t("presetSaved");
  }
  async remove() {
    if (!this.editId) return;
    await this.mutation(`/easyapi/presets/${encodeURIComponent(this.editId)}?${new URLSearchParams({ profile_id: this.profile.value })}`, undefined, "DELETE");
    this.newPreset(); await this.refresh(); await this.onChanged();
  }
  translate() {
    for (const entry of this.labels) entry.node.textContent = t(entry.key);
    this.syncOperations(); this.renderList(); this.picker.translate(); this.refreshModels();
  }
  destroy() { this.destroyed = true; ++this.listSequence; ++this.modelSequence; this.root.remove(); }
}
