import { t } from "./ui-i18n.js";
import { selectProfile } from "./ui-state.js";

const MODEL_FIELDS = { optimize: "promptOptimize", tune: "promptTuningTextModel", analysis: "wanAnalysisModel" };
const modelOption = id => "model:" + id;
const modelLabel = ({ id, display_name }) => display_name && display_name !== id ? `${display_name} · ${id}` : id;

function element(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function option(value, textContent, disabled = false) {
  return element("option", { value, textContent, disabled });
}

export class PromptSettingsPanel {
  constructor({ loadModels, promptModelPreferences }) {
    Object.assign(this, { loadModels, promptModelPreferences });
    this.labels = [];
    this.profiles = [];
    this.models = [];
    this.profile = element("select");
    this.profile.addEventListener("change", () => {
      this.selectedProfile = this.profile.value;
      this.refreshModels();
    });
    this.modelFields = Object.fromEntries(Object.entries(MODEL_FIELDS).map(([role, label]) => {
      const input = element("select");
      input.addEventListener("change", () => this.save(role, input.value));
      return [role, { input, field: this.field(label, input) }];
    }));
    this.catalogStatus = element("span", { className: "ea-caption", role: "status" });
    this.saveStatus = element("p", { className: "ea-status", role: "status" });
    this.retry = this.label("button", "retryModels", { type: "button", hidden: true });
    this.retry.addEventListener("click", () => this.refreshModels(true));
    this.root = element("section", { className: "ea-manager-view", hidden: true },
      this.label("p", "promptSettingsDescription", { className: "ea-caption" }), this.field("connection", this.profile),
      ...Object.values(this.modelFields).map(({ field }) => field),
      element("div", { className: "ea-model-updates" }, this.catalogStatus, this.retry), this.saveStatus);
    this.renderProfiles("");
    this.renderModels();
  }

  label(tag, key, props = {}) {
    const node = element(tag, { ...props, textContent: t(key) });
    this.labels.push({ node, key });
    return node;
  }

  field(key, input) { return element("label", {}, this.label("span", key), input); }

  setProfiles(profiles, defaultId, preferred, { refresh = false } = {}) {
    const current = this.profile.value;
    const revision = this.profiles.find(row => row.id === current)?.model_catalog_revision;
    this.profiles = profiles;
    if (preferred) this.selectedProfile = preferred;
    const existing = profiles.find(row => row.id === this.selectedProfile)?.id;
    const next = selectProfile(existing, profiles, defaultId);
    this.renderProfiles(next);
    if (!this.root.hidden && (refresh || !this.modelRequest || current !== next ||
        revision !== profiles.find(row => row.id === next)?.model_catalog_revision)) void this.refreshModels(refresh);
  }

  renderProfiles(current) {
    this.profile.replaceChildren(option("", t("chooseConnection"), true),
      ...this.profiles.map(({ id, name }) => option(id, name || id)));
    this.profile.value = current;
    this.profile.disabled = !this.profiles.length;
  }

  setVisible(visible) {
    this.root.hidden = !visible;
    if (!visible) {
      this.modelRequest?.abort();
      this.modelRequest = null;
    } else if (!this.modelRequest) void this.refreshModels();
    else this.renderModels();
  }

  candidates(role) {
    return role === "analysis" ? this.models.filter(model => model.supports_image === true || model.input_modalities?.includes("image")) : this.models;
  }

  renderModels() {
    const profileId = this.profile.value;
    for (const [role, { input }] of Object.entries(this.modelFields)) {
      const candidates = this.candidates(role);
      const { fixed, recent } = this.promptModelPreferences.get(profileId, role);
      const remembered = recent === "" && role === "analysis" ? t("wanAnalysisOptional") :
        candidates.find(model => model.id === recent)?.id;
      const choices = [option("auto", remembered ? t("promptModelAutomaticRecent", { model: remembered }) : t("promptModelAutomatic"))];
      if (role === "analysis") choices.push(option("text", t("wanAnalysisOptional")));
      choices.push(...candidates.map(model => option(modelOption(model.id), modelLabel(model), this.stale)));
      if (fixed && !candidates.some(model => model.id === fixed)) {
        choices.push(option(modelOption(fixed), this.catalogKnown ? t("promptModelUnavailable", { model: fixed }) : fixed, true));
      }
      input.replaceChildren(...choices);
      input.value = fixed === undefined ? "auto" : fixed === "" ? "text" : modelOption(fixed);
      input.disabled = !profileId;
    }
  }

  setSaveStatus(key = "") {
    this.saveKey = key;
    this.saveStatus.textContent = key ? t(key) : "";
    this.saveStatus.className = key === "promptModelPreferenceSaveFailed" ? "ea-error" : "ea-status";
  }

  save(role, choice) {
    const profileId = this.profile.value;
    const candidate = this.candidates(role).find(model => modelOption(model.id) === choice);
    const textOnly = role === "analysis" && choice === "text";
    if (profileId && (choice === "auto" || textOnly || (candidate && !this.stale))) {
      const id = textOnly ? "" : candidate?.id;
      const saved = this.promptModelPreferences.setFixed(profileId, role, id);
      this.setSaveStatus(saved ? "promptModelPreferenceSaved" : "promptModelPreferenceSaveFailed");
    }
    this.renderModels();
  }

  async refreshModels(refresh = false) {
    this.modelRequest?.abort();
    const request = this.modelRequest = new AbortController();
    const profileId = this.profile.value;
    const apply = (models, stale = false) => {
      this.models = models.filter(model => typeof model?.id === "string" && model.id);
      this.catalogKnown = true;
      this.stale = stale;
      this.renderModels();
      this.catalogStatus.textContent = this.models.length ? "" : t("noModels");
    };
    this.models = [];
    this.catalogKnown = this.stale = false;
    this.renderModels();
    this.setSaveStatus();
    this.retry.hidden = true;
    this.catalogStatus.textContent = t(profileId ? "loadingModels" : "profileNeeded");
    if (!profileId) return;
    let lastModels;
    const update = state => {
      if (request.signal.aborted) return;
      if (state.invalidated) {
        request.abort();
        this.models = [];
        this.catalogKnown = this.stale = false;
        this.renderModels();
        this.catalogStatus.textContent = state.error?.message || t("authorityError");
      } else {
        if (state.models) {
          lastModels = state.models;
          apply(state.models, state.stale);
        }
        if (state.error) this.catalogStatus.textContent = this.models.length ? t("modelUpdateFailed") : state.error.message;
        else if (state.updating) this.catalogStatus.textContent = t(state.models ? "updatingModels" : "loadingModels");
      }
      this.retry.hidden = !state.error && !state.invalidated;
    };
    try {
      const models = await this.loadModels(profileId, request.signal, update, { profiles: this.profiles, refresh });
      if (!request.signal.aborted && models !== lastModels) apply(models);
    } catch (error) {
      if (request.signal.aborted) return;
      this.catalogStatus.textContent = error.message;
      this.retry.hidden = false;
    }
  }

  translate() {
    for (const { node, key } of this.labels) node.textContent = t(key);
    this.renderProfiles(this.profile.value);
    if (!this.root.hidden) {
      const saveKey = this.saveKey;
      void this.refreshModels();
      this.setSaveStatus(saveKey);
    }
  }

  showProfileError(error) {
    this.modelRequest?.abort();
    this.modelRequest = null;
    this.models = [];
    this.catalogKnown = this.stale = false;
    this.renderModels();
    this.catalogStatus.textContent = error.message;
    this.retry.hidden = true;
  }

  destroy() { this.setVisible(false); this.root.remove(); }
}
