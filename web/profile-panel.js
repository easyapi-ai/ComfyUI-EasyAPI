import { t, hostLocale } from "./ui-i18n.js";

export const OFFICIAL_GATEWAY = "https://token.easyapi.com";
export const EASYAPI_SIDEBAR_ID = "easyapi-connections";

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "dataset") Object.assign(node.dataset, value);
    else node[key] = value;
  }
  for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function publicProfile(value) {
  return {
    id: String(value.id || value.profile_id || ""), name: String(value.name || value.id || ""),
    base_url: String(value.base_url || ""), has_key: Boolean(value.has_key),
    key_hint: value.key_hint ? String(value.key_hint) : null,
    auth_method: ["manual", "official", "environment"].includes(value.auth_method) ? value.auth_method : "manual",
    credential_expires_at: value.credential_expires_at || null, read_only: Boolean(value.read_only),
    connection_status: ["ok", "error", "untested"].includes(value.connection_status) ? value.connection_status : "untested",
    last_checked_at: value.last_checked_at || null,
  };
}
export function credentialHint(profile) {
  const hint = String(profile.key_hint || "");
  return profile.has_key && /[•*…]/.test(hint) ? "••••" + hint.slice(-4) : t(profile.has_key ? "keyConfigured" : "keyMissing");
}
export function officialAuthorizationURL(address) {
  const url = new URL(address);
  if (url.origin !== "https://www.easyapi.com" || url.pathname !== "/cli/authorize" || url.username || url.password || url.hash)
    throw new Error(t("invalidAuthorizationURL"));
  return url.href;
}

export class ProfileManager {
  constructor({ request, bootstrap, onChanged = () => {}, createPresets = null, openExternal = (url) => window.open(url, "_blank", "noopener,noreferrer") }) {
    this.request = request; this.bootstrap = bootstrap; this.onChanged = onChanged; this.openExternal = openExternal;
    this.profiles = []; this.defaultId = null; this.selectedId = null; this.labels = []; this.flowVersion = 0;
    this.root = element("section", { className: "easyapi-manager easyapi-panel" });
    this.heading = this.label("h2", "managerTitle");
    this.profileList = element("div", { className: "ea-connections", role: "listbox" });
    this.notice = element("div", { className: "ea-manager-notice", role: "status" });
    this.id = element("input", { pattern: "[A-Za-z0-9_-]+", autocomplete: "off" });
    this.name = element("input", { autocomplete: "off" });
    this.base = element("input", { type: "url", value: OFFICIAL_GATEWAY, autocomplete: "url" });
    this.key = element("input", { type: "password", value: "", autocomplete: "new-password" });
    this.key.placeholder = t("留空保留已保存的密钥");
    this.makeDefault = element("input", { type: "checkbox" });
    this.summary = element("div", { className: "ea-credential-summary" });
    this.saveButton = this.action("保存连接", () => this.save(), true);
    this.deleteButton = this.action("删除此连接", () => this.remove());
    this.defaultButton = this.action("setDefaultConnection", () => this.setDefault());
    this.testButton = this.action("testConnection", () => this.test());
    this.testHint = element("small", { className: "ea-caption" });
    this.authorizeButton = this.action("authorizeOfficial", () => this.startAuthorization(), true);
    this.authNotice = this.label("p", "authorizationNotice", { className: "ea-caption" });
    this.authStatus = element("div", { className: "ea-status", role: "status" });
    this.authLink = this.label("a", "openAuthorization", { target: "_blank", rel: "noopener noreferrer", hidden: true });
    this.cancelAuthorization = this.action("cancelAuthorization", () => this.cancelAuth());
    this.cancelAuthorization.hidden = true;
    this.manual = element("details", { className: "ea-advanced" }, this.label("summary", "manualKey"), this.field("API 密钥", this.key));
    this.form = element("form", { className: "ea-profile-editor" });
    this.form.addEventListener("submit", (event) => { event.preventDefault(); this.save(); });
    this.form.append(this.summary, this.field("显示名称", this.name), this.field("连接别名", this.id), this.field("服务地址", this.base),
      this.authNotice, element("div", { className: "ea-row" }, this.authorizeButton, this.authLink, this.cancelAuthorization), this.authStatus,
      this.manual, element("label", { className: "ea-checkbox" }, this.makeDefault, this.label("span", "makeDefault")),
      element("div", { className: "ea-row" }, this.saveButton, this.defaultButton, this.deleteButton),
      element("div", { className: "ea-connection-test" }, this.testButton, this.testHint));
    this.connectionView = element("div", { className: "ea-manager-view" }, this.label("p", "managerDescription", { className: "ea-caption" }), this.profileList,
      this.action("newConnection", () => this.newProfile()), this.notice, this.form);
    this.presetPanel = createPresets?.();
    this.root.append(element("div", { className: "ea-title" }, this.heading, this.action("refresh", () => this.refresh())), this.connectionView);
    if (this.presetPanel) {
      this.connectionTab = this.action("connectionsTab", () => this.showSection("connections"));
      this.presetsTab = this.action("presetsTab", () => this.showSection("presets"));
      this.tabs = element("div", { className: "ea-manager-tabs", role: "tablist" }, this.connectionTab, this.presetsTab);
      this.root.insertBefore(this.tabs, this.connectionView); this.root.append(this.presetPanel.root); this.showSection("connections");
    }
    for (const input of [this.id, this.name, this.base, this.key]) input.addEventListener("input", () => { this.dirty = true; this.editorTouched = true; this.updateDisabled(); });
    this.newProfile();
  }
  label(tag, key, props = {}) {
    const node = element(tag, props, t(key)); this.labels.push({ node, key }); return node;
  }
  action(key, handler, primary = false) {
    const node = this.label("button", key, { type: "button", className: primary ? "ea-primary" : "" });
    node.addEventListener("click", () => Promise.resolve(handler()).catch((error) => this.showError(error)));
    return node;
  }
  field(key, input) { return element("label", {}, this.label("span", key), input); }
  async mutation(path, body, method = "POST") {
    const headers = { "X-EasyAPI-Management": await this.bootstrap() };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return this.request(path, { method, headers, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  }
  showError(error) { this.notice.className = "ea-manager-notice ea-error"; this.notice.textContent = (error.code ? error.code + ": " : "") + error.message; }
  selected() { return this.profiles.find((profile) => profile.id === this.selectedId); }
  async refresh(selectId) {
    try {
      const data = await this.request("/easyapi/profiles");
      this.profiles = (Array.isArray(data.profiles) ? data.profiles : []).map(publicProfile);
      this.defaultId = data.default_profile_id || null;
      const requested = selectId || this.requestedId || (!this.loaded && !this.editorTouched ? this.defaultId || this.profiles[0]?.id : null); this.requestedId = null;
      this.renderProfiles();
      const profile = this.profiles.find((value) => value.id === requested || (!requested && value.id === this.selectedId));
      if (profile && (!this.dirty || requested)) this.select(profile.id);
      else if (!this.dirty && this.selectedId && !profile) this.newProfile();
      this.loaded = true;
      this.presetPanel?.setProfiles(this.profiles, this.defaultId);
      return this.profiles;
    } catch (error) { this.showError(error); return []; }
  }
  showSection(section, profileId) {
    this.connectionView.hidden = section !== "connections";
    if (this.presetPanel) {
      this.presetPanel.root.hidden = section !== "presets";
      this.connectionTab?.setAttribute("aria-selected", String(section === "connections"));
      this.presetsTab?.setAttribute("aria-selected", String(section === "presets"));
      if (section === "presets") { this.presetPanel.setProfiles(this.profiles, this.defaultId, profileId); this.presetPanel.refresh(); }
    }
  }
  renderProfiles() {
    this.profileList.replaceChildren(...this.profiles.map((profile) => {
      const card = element("button", { type: "button", className: "ea-connection-card", role: "option" });
      card.setAttribute("aria-selected", String(profile.id === this.selectedId));
      card.append(element("span", { className: "ea-connection-name" }, profile.name),
        element("span", { className: "ea-connection-url" }, profile.base_url),
        element("span", { className: "ea-connection-meta" }, credentialHint(profile), " · ", t("authMethod." + profile.auth_method)),
        element("span", { className: "ea-connection-status", dataset: { status: profile.connection_status } }, t("connectionStatus." + profile.connection_status)));
      if (profile.id === this.defaultId) card.append(element("span", { className: "ea-default-badge" }, t("defaultConnection")));
      card.addEventListener("click", () => this.select(profile.id)); return card;
    }));
    if (!this.profiles.length) this.profileList.append(this.label("p", "noConnections", { className: "ea-caption" }));
  }
  newProfile() {
    if (this.flow?.status === "pending") { this.notice.textContent = t("finishAuthorization"); return; }
    this.selectedId = null; this.id.readOnly = false;
    let alias = "easyapi", count = 2; while (this.profiles.some((profile) => profile.id === alias)) alias = "easyapi-" + count++;
    this.id.value = alias; this.name.value = "EasyAPI"; this.base.value = OFFICIAL_GATEWAY; this.key.value = "";
    this.makeDefault.checked = false; this.dirty = true; this.editorTouched = false; this.manual.open = !window.isSecureContext;
    this.summary.textContent = t("newConnection"); this.notice.textContent = ""; this.renderProfiles(); this.updateDisabled();
  }
  select(profileId) {
    if (this.flow?.status === "pending" && profileId !== this.flow.profileId) { this.notice.textContent = t("finishAuthorization"); return; }
    const profile = this.profiles.find((value) => value.id === profileId); if (!profile) { this.requestedId = profileId; return; }
    this.selectedId = profile.id; this.id.value = profile.id; this.id.readOnly = true;
    this.name.value = profile.name; this.base.value = profile.base_url; this.key.value = "";
    this.makeDefault.checked = profile.id === this.defaultId; this.dirty = false; this.manual.open = profile.auth_method === "manual";
    const expiry = profile.credential_expires_at ? new Date(profile.credential_expires_at * 1000).toLocaleDateString(hostLocale()) : "";
    this.summary.textContent = [credentialHint(profile), t("authMethod." + profile.auth_method), expiry ? t("credentialExpires", { date: expiry }) : ""].filter(Boolean).join(" · ");
    this.renderProfiles(); this.updateDisabled();
  }
  updateDisabled() {
    const profile = this.selected(), readOnly = Boolean(profile?.read_only), pending = this.authorizing || this.flow?.status === "pending";
    this.name.readOnly = this.base.readOnly = this.key.readOnly = readOnly || pending;
    this.makeDefault.disabled = readOnly || pending;
    this.id.readOnly = Boolean(profile) || pending;
    this.saveButton.disabled = readOnly || pending; this.deleteButton.disabled = !profile || readOnly || pending;
    this.defaultButton.disabled = !profile || profile.id === this.defaultId || pending;
    this.testButton.disabled = !profile || this.dirty || this.testing || pending;
    this.testHint.textContent = readOnly ? t("environmentConnection") : this.dirty ? t("saveBeforeTest") : t("testReadOnly");
    const official = this.base.value.replace(/\/+$/, "") === OFFICIAL_GATEWAY;
    const secure = window.isSecureContext;
    this.authorizeButton.disabled = !official || !secure || readOnly || pending;
    const authNotice = secure ? "authorizationNotice" : "authorizationSecureContext";
    this.authNotice.textContent = t(official ? authNotice : "officialGatewayOnly");
  }
  async save() {
    if (this.saveButton.disabled) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(this.id.value.trim()) || !this.base.value.trim()) throw new Error(t("invalidConnectionFields"));
    if (!this.selected() && !this.key.value.trim()) { this.manual.open = true; throw new Error(t("authorizeOrEnterKey")); }
    const id = this.id.value.trim();
    this.saveButton.disabled = true;
    try {
      await this.mutation(`/easyapi/profiles/${encodeURIComponent(id)}`, { name: this.name.value.trim(), base_url: this.base.value.trim(), api_key: this.key.value, make_default: this.makeDefault.checked });
      this.key.value = "";
      if (!this.makeDefault.checked && this.defaultId === id) await this.mutation("/easyapi/default-profile", { profile_id: null });
      this.dirty = false; await this.refresh(id); this.notice.className = "ea-manager-notice"; this.notice.textContent = t("连接已保存。密钥仅保存在 ComfyUI 后端。");
      await this.onChanged(id);
    } finally { this.key.value = ""; this.updateDisabled(); }
  }
  async remove() {
    const profile = this.selected(); if (!profile || profile.read_only || this.deleteButton.disabled) return;
    await this.mutation(`/easyapi/profiles/${encodeURIComponent(profile.id)}`, undefined, "DELETE");
    this.dirty = false; this.selectedId = null; await this.refresh(); this.newProfile(); await this.onChanged(profile.id);
  }
  async setDefault() {
    const profile = this.selected(); if (!profile) return;
    await this.mutation("/easyapi/default-profile", { profile_id: profile.id });
    await this.refresh(profile.id); await this.onChanged(null);
  }
  async test() {
    const profile = this.selected(); if (!profile || this.testButton.disabled) return;
    this.testing = true; this.updateDisabled(); this.notice.textContent = t("testingConnection");
    try {
      const result = await this.mutation(`/easyapi/profiles/${encodeURIComponent(profile.id)}/test`, {});
      if (result.ok !== true) throw new Error(result.message || result.error || t("connectionStatus.error"));
      this.notice.className = "ea-manager-notice"; this.notice.textContent = t("connectionTestSuccess", { count: result.model_count ?? 0 });
      await this.refresh(profile.id);
    } catch (error) {
      await this.refresh(profile.id); throw error;
    } finally { this.testing = false; this.updateDisabled(); }
  }
  async startAuthorization() {
    if (this.authorizeButton.disabled) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(this.id.value.trim())) throw new Error(t("invalidConnectionFields"));
    const version = ++this.flowVersion;
    const payload = { profile_id: this.id.value.trim(), name: this.name.value.trim(), make_default: this.makeDefault.checked };
    this.authorizing = true; this.key.value = ""; this.authStatus.textContent = t("authorizationStarting"); this.updateDisabled();
    let result;
    try { result = await this.mutation("/easyapi/auth/start", payload); }
    finally { this.authorizing = false; this.updateDisabled(); }
    if (version !== this.flowVersion) return;
    if (result.status && result.status !== "pending") {
      this.flow = { id: result.flow_id || "", status: result.status, profileId: payload.profile_id };
      this.authStatus.textContent = result.message || result.error || t("authorization." + result.status);
      this.authLink.hidden = this.cancelAuthorization.hidden = true; this.updateDisabled();
      if (result.status === "authorized") { this.dirty = false; await this.refresh(result.profile?.id || payload.profile_id); await this.onChanged(payload.profile_id); }
      return;
    }
    const address = officialAuthorizationURL(result.authorize_url);
    this.flow = { id: result.flow_id, status: result.status || "pending", expiresAt: Date.now() + Number(result.expires_in || 300) * 1000, profileId: payload.profile_id };
    this.authLink.href = address; this.authLink.hidden = false; this.cancelAuthorization.hidden = false;
    this.authStatus.textContent = t("authorizationPending"); this.updateDisabled();
    this.openExternal(address); this.schedulePoll(result.retry_after, version);
  }
  schedulePoll(retry, version) {
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => this.pollAuthorization(version).catch((error) => {
      this.authStatus.textContent = error.message;
      if (version === this.flowVersion && this.flow?.status === "pending" && Date.now() < this.flow.expiresAt) this.schedulePoll(3, version);
      else if (this.flow) { this.flow.status = "expired"; this.authStatus.textContent = t("authorization.expired"); this.updateDisabled(); }
    }), Math.max(1, Math.min(Number(retry) || 2, 30)) * 1000);
  }
  async pollAuthorization(version) {
    if (!this.flow || version !== this.flowVersion || this.flow.status !== "pending") return;
    const result = await this.mutation(`/easyapi/auth/${encodeURIComponent(this.flow.id)}/poll`, {});
    if (version !== this.flowVersion) return;
    this.flow.status = result.status;
    if (result.status === "pending" && Date.now() < this.flow.expiresAt) { this.schedulePoll(result.retry_after, version); return; }
    if (result.status === "pending") this.flow.status = "expired";
    this.authStatus.textContent = t("authorization." + this.flow.status);
    this.authLink.hidden = true; this.cancelAuthorization.hidden = true; this.updateDisabled();
    if (this.flow.status === "authorized") {
      this.dirty = false; await this.refresh(result.profile?.id || this.flow.profileId); await this.onChanged(this.flow.profileId);
    }
  }
  async cancelAuth() {
    if (!this.flow || this.flow.status !== "pending") return;
    const flow = this.flow; ++this.flowVersion; clearTimeout(this.pollTimer);
    try { await this.mutation(`/easyapi/auth/${encodeURIComponent(flow.id)}`, undefined, "DELETE"); }
    catch (error) { this.schedulePoll(3, this.flowVersion); throw error; }
    flow.status = "cancelled"; this.authStatus.textContent = t("authorization.cancelled"); this.authLink.hidden = this.cancelAuthorization.hidden = true; this.updateDisabled();
  }
  translate() {
    for (const entry of this.labels) entry.node.textContent = t(entry.key);
    this.key.placeholder = t("留空保留已保存的密钥"); this.renderProfiles(); this.updateDisabled();
    if (this.flow) this.authStatus.textContent = t(this.flow.status === "pending" ? "authorizationPending" : "authorization." + this.flow.status);
    this.presetPanel?.translate();
  }
  destroy() { clearTimeout(this.pollTimer); ++this.flowVersion; this.key.value = ""; this.presetPanel?.destroy(); this.root.remove(); }
}
