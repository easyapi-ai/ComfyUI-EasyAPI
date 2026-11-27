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
    model_catalog_revision: String(value.model_catalog_revision || ""),
  };
}
function formatBalanceAmount(entry) {
  if (!Number.isFinite(entry?.amount) || typeof entry.currency !== "string" || !entry.currency.trim()) return null;
  const currency = entry.currency.trim();
  const options = { maximumFractionDigits: 6 };
  if (/^[A-Z]{3}$/.test(currency)) {
    return new Intl.NumberFormat(hostLocale(), { ...options, style: "currency", currency, currencyDisplay: "code" }).format(entry.amount);
  }
  return `${new Intl.NumberFormat(hostLocale(), options).format(entry.amount)} ${currency}`;
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
  constructor({ request, bootstrap, onChanged = () => {}, createPresets = null, createPromptSettings = null,
                openExternal = (url) => window.open(url, "_blank", "noopener,noreferrer") }) {
    this.request = request; this.bootstrap = bootstrap; this.onChanged = onChanged; this.openExternal = openExternal;
    this.profiles = []; this.defaultId = null; this.selectedId = null; this.labels = []; this.flowVersion = 0; this.profileRefreshSequence = 0;
    this.balances = new Map(); this.balanceViews = new Map(); this.balanceChanges = new Set();
    this.root = element("section", { className: "easyapi-manager easyapi-panel" });
    this.heading = this.label("h2", "managerTitle");
    this.profileList = element("div", { className: "ea-connections", role: "list" });
    this.notice = element("div", { className: "ea-manager-notice", role: "status" });
    this.id = element("input", { pattern: "[A-Za-z0-9_-]+", autocomplete: "off" });
    this.name = element("input", { autocomplete: "off" });
    this.base = element("input", { type: "url", value: OFFICIAL_GATEWAY, autocomplete: "url" });
    this.key = element("input", { type: "password", value: "", autocomplete: "new-password" });
    this.makeDefault = element("input", { type: "checkbox" });
    this.summary = element("div", { className: "ea-credential-summary" });
    this.saveButton = this.action("保存连接", () => this.save(), true);
    this.deleteButton = this.action("删除此连接", () => this.remove());
    this.defaultButton = this.action("setDefaultConnection", () => this.setDefault());
    this.testButton = this.action("testConnection", () => this.test());
    this.testHint = element("small", { className: "ea-caption" });
    this.authorizeButton = this.action("authorizeOfficial", () => this.startAuthorization());
    this.authNotice = this.label("p", "authorizationNotice", { className: "ea-caption" });
    this.authStatus = element("div", { className: "ea-status", role: "status" });
    this.authLink = this.label("a", "openAuthorization", { target: "_blank", rel: "noopener noreferrer", hidden: true });
    this.cancelAuthorization = this.action("cancelAuthorization", () => this.cancelAuth());
    this.cancelAuthorization.hidden = true;
    const authorization = element("details", { className: "ea-advanced" }, this.label("summary", "authMethod.official"),
      element("div", { className: "ea-manager-view ea-authorization" }, this.authNotice,
        element("div", { className: "ea-row" }, this.authorizeButton, this.authLink, this.cancelAuthorization), this.authStatus));
    this.form = element("form", { className: "ea-profile-editor" });
    this.form.addEventListener("submit", (event) => { event.preventDefault(); this.save(); });
    this.form.append(this.summary, this.field("显示名称", this.name), this.field("连接别名", this.id), this.field("服务地址", this.base),
      this.field("API 密钥", this.key), element("label", { className: "ea-checkbox" }, this.makeDefault, this.label("span", "makeDefault")),
      element("div", { className: "ea-row" }, this.saveButton, this.defaultButton, this.deleteButton),
      element("div", { className: "ea-connection-test" }, this.testButton, this.testHint), authorization);
    this.connectionView = element("div", { className: "ea-manager-view" }, this.label("p", "managerDescription", { className: "ea-caption" }), this.profileList,
      this.action("newConnection", () => this.newProfile()), this.notice, this.form);
    this.presetPanel = createPresets?.();
    this.promptSettingsPanel = createPromptSettings?.();
    this.root.append(element("div", { className: "ea-title" }, this.heading,
      this.action("refresh", () => this.refresh(undefined, { refreshBalances: true, refreshPromptModels: true }))), this.connectionView);
    if (this.presetPanel || this.promptSettingsPanel) {
      this.connectionTab = this.action("connectionsTab", () => this.showSection("connections"));
      this.tabs = element("div", { className: "ea-manager-tabs", role: "tablist" }, this.connectionTab);
      this.root.insertBefore(this.tabs, this.connectionView);
      if (this.presetPanel) {
        this.presetsTab = this.action("presetsTab", () => this.showSection("presets"));
        this.tabs.append(this.presetsTab); this.root.append(this.presetPanel.root);
      }
      if (this.promptSettingsPanel) {
        this.promptSettingsTab = this.action("promptSettingsTab", () => this.showSection("promptSettings"));
        this.tabs.append(this.promptSettingsTab); this.root.append(this.promptSettingsPanel.root);
      }
      this.showSection("connections");
    }
    for (const input of [this.id, this.name, this.base, this.key]) input.addEventListener("input", () => {
      this.dirty = true; this.editorTouched = true; this.updateDisabled();
    });
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
  async refresh(selectId, { refreshBalances = false, refreshPromptModels = false } = {}) {
    const sequence = ++this.profileRefreshSequence;
    try {
      const data = await this.request("/easyapi/profiles");
      if (this.disposed || sequence !== this.profileRefreshSequence) return this.profiles;
      this.profiles = (Array.isArray(data.profiles) ? data.profiles : []).map(publicProfile);
      for (const [id, query] of this.balances) {
        if (!this.profiles.some((profile) => profile.id === id && profile.has_key && profile.model_catalog_revision === query.revision)) {
          this.clearBalance(id);
        }
      }
      this.defaultId = data.default_profile_id || null;
      const requested = selectId || this.requestedId || (!this.loaded && !this.editorTouched ? this.defaultId || this.profiles[0]?.id : null); this.requestedId = null;
      this.renderProfiles();
      const profile = this.profiles.find((value) => value.id === requested || (!requested && value.id === this.selectedId));
      if (profile && (!this.dirty || requested)) this.select(profile.id);
      else if (!this.dirty && this.selectedId && !profile) this.newProfile();
      this.loaded = true;
      this.presetPanel?.setProfiles(this.profiles, this.defaultId);
      this.promptSettingsPanel?.setProfiles(this.profiles, this.defaultId, undefined, { refresh: refreshPromptModels });
      if (refreshBalances) void this.refreshBalances();
      else if (!this.connectionView.hidden) {
        for (const profile of this.profiles) if (!this.balances.has(profile.id)) void this.checkBalance(profile.id);
      }
      return this.profiles;
    } catch (error) {
      if (!this.disposed && sequence === this.profileRefreshSequence) {
        this.showError(error);
        if (this.promptSettingsPanel && !this.promptSettingsPanel.root.hidden) this.promptSettingsPanel.showProfileError(error);
      }
      return [];
    }
  }
  showSection(section, profileId) {
    const wasHidden = this.connectionView.hidden;
    this.connectionView.hidden = section !== "connections";
    this.connectionTab?.setAttribute("aria-selected", String(section === "connections"));
    if (this.presetPanel) {
      this.presetPanel.root.hidden = section !== "presets";
      this.presetsTab?.setAttribute("aria-selected", String(section === "presets"));
      if (section === "presets") { this.presetPanel.setProfiles(this.profiles, this.defaultId, profileId); this.presetPanel.refresh(); }
    }
    if (this.promptSettingsPanel) {
      const visible = section === "promptSettings";
      this.promptSettingsTab.setAttribute("aria-selected", String(visible));
      if (visible) this.promptSettingsPanel.setProfiles(this.profiles, this.defaultId, profileId);
      this.promptSettingsPanel.setVisible(visible);
    }
    if (wasHidden && !this.connectionView.hidden) void this.refreshBalances();
  }
  renderProfiles() {
    this.balanceViews.clear();
    this.profileList.replaceChildren(...this.profiles.map((profile) => {
      const card = element("button", { type: "button", className: "ea-connection-card" });
      card.setAttribute("aria-pressed", String(profile.id === this.selectedId));
      card.append(element("span", { className: "ea-connection-name" }, profile.name),
        element("span", { className: "ea-connection-url" }, profile.base_url),
        element("span", { className: "ea-connection-meta" }, credentialHint(profile), " · ", t("authMethod." + profile.auth_method)),
        element("span", { className: "ea-connection-status", dataset: { status: profile.connection_status } }, t("connectionStatus." + profile.connection_status)));
      if (profile.id === this.defaultId) card.append(element("span", { className: "ea-default-badge" }, t("defaultConnection")));
      card.addEventListener("click", () => this.select(profile.id));
      const row = element("div", { className: "ea-connection-card-row", role: "listitem" }, card);
      if (profile.has_key) {
        const status = element("span", { className: "ea-caption ea-balance-status", role: "status" });
        const label = t("refreshBalance", { name: profile.name });
        const refresh = element("button", { type: "button", className: "ea-balance-refresh", title: label }, "↻");
        refresh.setAttribute("aria-label", label);
        refresh.addEventListener("click", () => this.checkBalance(profile.id));
        card.append(status);
        row.append(refresh);
        this.balanceViews.set(profile.id, { status, refresh, profile });
        this.renderBalance(profile.id);
      }
      return row;
    }));
    if (!this.profiles.length) this.profileList.append(this.label("p", "noConnections", { className: "ea-caption" }));
  }
  newProfile() {
    if (this.flow?.status === "pending") { this.notice.textContent = t("finishAuthorization"); return; }
    this.selectedId = null; this.id.readOnly = false;
    let alias = "easyapi", count = 2; while (this.profiles.some((profile) => profile.id === alias)) alias = "easyapi-" + count++;
    this.id.value = alias; this.name.value = "EasyAPI"; this.base.value = OFFICIAL_GATEWAY; this.key.value = "";
    this.makeDefault.checked = false; this.dirty = true; this.editorTouched = false;
    this.summary.textContent = t("newConnection"); this.notice.textContent = ""; this.renderProfiles(); this.updateDisabled();
  }
  select(profileId) {
    if (this.flow?.status === "pending" && profileId !== this.flow.profileId) { this.notice.textContent = t("finishAuthorization"); return; }
    const profile = this.profiles.find((value) => value.id === profileId); if (!profile) { this.requestedId = profileId; return; }
    this.selectedId = profile.id; this.id.value = profile.id; this.id.readOnly = true;
    this.name.value = profile.name; this.base.value = profile.base_url; this.key.value = "";
    this.makeDefault.checked = profile.id === this.defaultId; this.dirty = false;
    const expiry = profile.credential_expires_at ? new Date(profile.credential_expires_at * 1000).toLocaleDateString(hostLocale()) : "";
    this.summary.textContent = [credentialHint(profile), t("authMethod." + profile.auth_method), expiry ? t("credentialExpires", { date: expiry }) : ""].filter(Boolean).join(" · ");
    this.renderProfiles(); this.updateDisabled();
  }
  updateDisabled() {
    const profile = this.selected(), readOnly = Boolean(profile?.read_only), pending = this.authorizing || this.flow?.status === "pending";
    this.name.readOnly = this.base.readOnly = this.key.readOnly = readOnly || pending;
    this.key.placeholder = profile?.has_key ? t("留空保留已保存的密钥") : "";
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
    if (!this.selected() && !this.key.value.trim()) {
      this.key.focus();
      throw new Error(t("connectionKeyRequired"));
    }
    const id = this.id.value.trim();
    this.saveButton.disabled = true;
    this.balanceChanges.add(id);
    this.clearBalance(id);
    try {
      await this.mutation(`/easyapi/profiles/${encodeURIComponent(id)}`, { name: this.name.value.trim(), base_url: this.base.value.trim(), api_key: this.key.value, make_default: this.makeDefault.checked });
      this.key.value = "";
      if (!this.makeDefault.checked && this.defaultId === id) await this.mutation("/easyapi/default-profile", { profile_id: null });
      this.dirty = false; await this.refresh(id); this.notice.className = "ea-manager-notice"; this.notice.textContent = t("连接已保存。密钥仅保存在 ComfyUI 后端。");
      await this.onChanged(id);
    } finally {
      this.balanceChanges.delete(id); this.key.value = ""; this.updateDisabled();
      this.renderBalance(id);
      if (!this.connectionView.hidden) void this.checkBalance(id);
    }
  }
  async remove() {
    const profile = this.selected(); if (!profile || profile.read_only || this.deleteButton.disabled) return;
    this.balanceChanges.add(profile.id);
    this.clearBalance(profile.id);
    try {
      await this.mutation(`/easyapi/profiles/${encodeURIComponent(profile.id)}`, undefined, "DELETE");
      this.dirty = false; this.selectedId = null; await this.refresh(); this.newProfile(); await this.onChanged(profile.id);
    } finally {
      this.balanceChanges.delete(profile.id); this.updateDisabled(); this.renderBalance(profile.id);
      if (!this.connectionView.hidden) void this.checkBalance(profile.id);
    }
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
  canCheckBalance(profile) {
    return !this.disposed && profile?.has_key && !this.balanceChanges.has(profile.id) &&
      !(this.flow?.status === "pending" && this.flow.profileId === profile.id);
  }
  clearBalance(id) {
    this.balances.get(id)?.controller.abort();
    this.balances.delete(id);
    this.renderBalance(id);
  }
  renderBalance(id) {
    const view = this.balanceViews.get(id);
    if (!view) return;
    const { status, refresh, profile } = view;
    const query = this.balances.get(id);
    refresh.disabled = !this.canCheckBalance(profile) || Boolean(query?.pending);
    refresh.dataset.loading = String(Boolean(query?.pending));
    if (!query || query.pending) {
      status.textContent = query ? t("checkingBalance") : "";
      return;
    }
    const { wallet, token, budget } = query.result || {};
    const lines = [];
    const walletAmount = formatBalanceAmount(wallet);
    const keyAmount = formatBalanceAmount(token);
    const budgetAmount = formatBalanceAmount(budget);
    if (walletAmount) lines.push(t("walletBalance", { amount: walletAmount }));
    if (token?.unlimited === true) lines.push(t("unlimitedKeyBalance"));
    else if (keyAmount) lines.push(t("keyBalance", { amount: keyAmount }));
    const total = token?.unlimited === true ? null : formatBalanceAmount({ amount: token?.total_granted, currency: token?.currency });
    const used = formatBalanceAmount({ amount: token?.total_used, currency: token?.currency });
    const quota = [total && t("totalKeyQuota", { amount: total }), used && t("usedKeyQuota", { amount: used })].filter(Boolean);
    if (quota.length) lines.push(quota.join(" · "));
    if (budgetAmount) {
      const period = String(budget.period);
      const label = ["daily", "1"].includes(period) ? "dailyBudgetBalance" : ["monthly", "2"].includes(period) ? "monthlyBudgetBalance" : "budgetBalance";
      lines.push(t(label, { amount: budgetAmount }));
    }
    status.textContent = lines.length ? lines.join("\n") : t("balanceUnavailable");
  }
  refreshBalances() {
    if (this.connectionView.hidden || this.disposed) return Promise.resolve();
    return Promise.all(this.profiles.map((profile) => this.checkBalance(profile.id)));
  }
  checkBalance(id = this.selectedId) {
    const profile = this.profiles.find((item) => item.id === id);
    if (!this.canCheckBalance(profile)) return;
    const pending = this.balances.get(id)?.pending;
    if (pending) return pending;
    const query = { revision: profile.model_catalog_revision, controller: new AbortController(), result: null };
    this.balances.set(id, query);
    query.pending = Promise.resolve().then(() => this.readBalance(id, query));
    this.renderBalance(id);
    return query.pending;
  }
  async readBalance(id, query) {
    const isCurrent = () => {
      const profile = this.profiles.find((item) => item.id === id);
      return this.balances.get(id) === query && this.canCheckBalance(profile) && profile.model_catalog_revision === query.revision;
    };
    try {
      if (!isCurrent()) return;
      const nonce = await this.bootstrap();
      if (!isCurrent()) return;
      query.result = await this.request(`/easyapi/profiles/${encodeURIComponent(id)}/balance`, {
        method: "GET", headers: { "X-EasyAPI-Management": nonce }, cache: "no-store", signal: query.controller.signal,
      });
    } catch { query.result = null; }
    finally {
      if (isCurrent()) { query.pending = null; this.renderBalance(id); }
    }
  }
  async startAuthorization() {
    if (this.authorizeButton.disabled) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(this.id.value.trim())) throw new Error(t("invalidConnectionFields"));
    const version = ++this.flowVersion;
    const payload = { profile_id: this.id.value.trim(), name: this.name.value.trim(), make_default: this.makeDefault.checked };
    this.authorizing = true; this.balanceChanges.add(payload.profile_id); this.clearBalance(payload.profile_id);
    this.key.value = ""; this.authStatus.textContent = t("authorizationStarting"); this.updateDisabled();
    let result;
    try { result = await this.mutation("/easyapi/auth/start", payload); }
    finally { this.authorizing = false; this.balanceChanges.delete(payload.profile_id); this.renderBalance(payload.profile_id); this.updateDisabled(); }
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
    this.renderBalance(payload.profile_id);
    this.authLink.href = address; this.authLink.hidden = false; this.cancelAuthorization.hidden = false;
    this.authStatus.textContent = t("authorizationPending"); this.updateDisabled();
    this.openExternal(address); this.schedulePoll(result.retry_after, version);
  }
  schedulePoll(retry, version) {
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => this.pollAuthorization(version).catch((error) => {
      this.authStatus.textContent = error.message;
      if (version === this.flowVersion && this.flow?.status === "pending" && Date.now() < this.flow.expiresAt) this.schedulePoll(3, version);
      else if (this.flow) {
        this.flow.status = "expired"; this.authStatus.textContent = t("authorization.expired");
        this.renderBalance(this.flow.profileId); this.updateDisabled();
      }
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
    this.renderBalance(this.flow.profileId);
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
    this.renderBalance(flow.profileId);
  }
  translate() {
    for (const entry of this.labels) entry.node.textContent = t(entry.key);
    this.renderProfiles(); this.updateDisabled();
    if (this.flow) this.authStatus.textContent = t(this.flow.status === "pending" ? "authorizationPending" : "authorization." + this.flow.status);
    this.presetPanel?.translate();
    this.promptSettingsPanel?.translate();
  }
  destroy() {
    this.disposed = true; clearTimeout(this.pollTimer); ++this.flowVersion;
    for (const query of this.balances.values()) query.controller.abort();
    this.balances.clear(); this.balanceViews.clear();
    this.key.value = ""; this.presetPanel?.destroy(); this.promptSettingsPanel?.destroy(); this.root.remove();
  }
}
