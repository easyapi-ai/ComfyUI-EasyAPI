import { WanTunerPanel, contextFingerprint } from "./wan-tuner-panel.js";
import { readTuningDraft, writeTuningDraft, tuningFingerprint } from "./video-tuning-state.js";

function make(tag, properties = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, properties); node.append(...children); return node;
}

function draftPrompt(text) {
  const match = text.match(/"prompt"\s*:\s*"((?:\\.|[^"\\])*)/s);
  if (!match) return "";
  try { return JSON.parse('"' + match[1].replace(/\\$/, "") + '"'); } catch { return ""; }
}

export class VideoTunerPanel extends WanTunerPanel {
  constructor(options) {
    const scopeId = options.scopeId || options.getContext().scopeId;
    const saved = readTuningDraft(scopeId);
    const form = saved?.form ? { ...saved.form,
      profileId: saved.form.profileId ?? saved.form.profile_id,
      model: saved.form.model ?? saved.form.text_model,
      analysisModel: saved.form.analysisModel ?? saved.form.analysis_model,
      start: saved.form.start ?? (saved.form.issue_start >= 0 ? saved.form.issue_start : ""),
      end: saved.form.end ?? (saved.form.issue_end >= 0 ? saved.form.issue_end : "") } : null;
    super({ ...options, preferences: { ...options.preferences, ...form } });
    Object.assign(this, { tune: options.tune, resumeTuning: options.resumeTuning,
      uploadMedia: options.uploadMedia, externalRun: options.externalRun, scopeId, readOnlyApply: options.readOnlyApply === true });
    this.videoReady = true;
    this.persistent = true;
    this.nonce = saved?.form?.nonce || 0;
    const general = make("option", { value: "general", textContent: this.t("videoDomainGeneral") });
    this.domain.insertBefore(general, this.domain.firstChild);
    this.domain.value = saved?.form?.domain || options.preferences?.domain || "general";
    this.persistenceStatus = make("p", { className: "ea-caption", role: "status" });
    this.partialPrompt = make("textarea", { rows: 3, readOnly: true });
    this.partialPrompt.setAttribute("aria-label", this.t("videoPartialDraft"));
    const copyPartial = make("button", { type: "button", textContent: this.t("videoCopyPartial") });
    copyPartial.addEventListener("click", () => this.copy(this.partialPrompt.value));
    this.partialSection = make("section", { className: "ea-wan-section", hidden: true },
      make("h4", { textContent: this.t("videoPartialDraft") }), this.partialPrompt, copyPartial);
    this.rerun = make("button", { type: "button", textContent: this.t("videoRetune") });
    this.rerun.addEventListener("click", () => this.execute(true));
    this.run.parentElement.insertBefore(this.rerun, this.cancel);
    this.content.append(this.partialSection, this.persistenceStatus);
    if (this.externalRun) {
      const input = this.content.querySelector('input[type="file"]');
      if (input) input.closest("label").hidden = true;
      this.videoUrl.closest("label").hidden = true;
      this.mediaList.parentElement.append(make("p", { className: "ea-caption", textContent: this.t("videoNodeMediaNote") }));
    }
    this.root.dataset.captureWheel = "true";
    if (form) this.restoreForm(form);
    if (saved?.completed) this.setResult(saved.completed, { original: saved.resultOriginal,
      digest: saved.resultDigest, stale: saved.stale || saved.attachmentCount > 0, save: false });
    if (saved?.partial) {
      this.partialPrompt.value = saved.partial; this.partialSection.hidden = false;
    }
    this.runId = saved?.runId || null;
    this.activeDigest = saved?.activeDigest; this.activeOriginal = saved?.activeOriginal;
    if (options.initialResult) this.setResult(options.initialResult, { stale: true });
    this.updateResultControls();
    if (this.runId && this.resumeTuning && !this.externalRun) this.resumeRun(this.runId);
  }

  restoreForm(form) {
    for (const [key, field] of [["domain", this.domain], ["mode", this.mode], ["output", this.output],
      ["issue", this.issue], ["start", this.start], ["end", this.end], ["videoUrl", this.videoUrl]]) {
      if (form[key] != null) field.value = String(form[key]);
    }
    if (typeof form.analyze === "boolean") this.analyze.checked = form.analyze;
    this.updateIssue();
  }

  formState() {
    return { profileId: this.profile.value, model: this.model.value, analysisModel: this.analysisModel.value,
      domain: this.domain.value, mode: this.mode.value, output: this.output.value, analyze: this.analyze.checked,
      issue: this.issue.value, start: this.start.value, end: this.end.value, videoUrl: this.videoUrl.value,
      nonce: this.nonce || 0, shape: this.shapeKey };
  }

  fingerprint() {
    return tuningFingerprint([contextFingerprint(this.getContext()), this.formState(),
      this.addedMedia.map(item => [item.kind, item.filename, item.url || item.previewUrl])]);
  }

  saveDraft() {
    if (!this.videoReady || !this.scopeId) return;
    const ok = writeTuningDraft(this.scopeId, { form: this.formState(), completed: this.completed,
      resultOriginal: this.resultOriginal, resultDigest: this.resultDigest, stale: this.resultStale,
      partial: this.partialPrompt?.value || "", attachmentCount: this.addedMedia.length, runId: this.runId,
      activeDigest: this.activeDigest, activeOriginal: this.activeOriginal });
    if (this.persistenceStatus) this.persistenceStatus.textContent = ok ? "" : this.t("videoDraftSaveFailed");
  }

  changed() { super.changed(); if (this.videoReady) this.saveDraft(); }
  renderMedia() { super.renderMedia(); if (this.videoReady) this.saveDraft(); }
  async refreshModels(...args) {
    await super.refreshModels(...args);
    if (this.videoReady) { this.updateResultControls(); this.saveDraft(); }
  }
  async addFiles(files) {
    if (!this.uploadMedia) return super.addFiles(files);
    if (this.uploading) return;
    this.uploading = true; this.run.disabled = true; this.rerun.disabled = true;
    this.status.textContent = this.t("videoUploadingMedia");
    this.uploadRequest = new AbortController();
    this.invalidate(false);
    try {
      for (const file of files || []) {
        const media = await this.uploadMedia(file, this.uploadRequest.signal);
        this.addedMedia.push({ ...media, id: "attachment-" + (this.addedMedia.length + 1), analysisOnly: true, ready: true });
        if (media.kind === "video") { this.output.value = "diagnose"; this.updateIssue(); }
      }
      this.status.textContent = "";
    } catch (error) { if (!this.uploadRequest.signal.aborted) this.status.textContent = error.message; }
    finally {
      this.uploading = false; this.run.disabled = this.model.disabled || !this.model.value || Boolean(this.request);
      this.renderMedia(); this.updateResultControls();
    }
  }

  validResult() {
    return Boolean(this.completed && !this.resultStale && this.resultOriginal === this.getContext().prompt &&
      this.resultDigest === this.fingerprint());
  }

  updateResultControls() {
    if (!this.videoReady) return;
    this.result.hidden = !this.completed;
    this.apply.hidden = !this.completed || this.readOnlyApply;
    this.apply.disabled = !this.validResult();
    this.copyPrompt.disabled = this.copyAnalysis.disabled = !this.completed;
    this.rerun.disabled = Boolean(this.request) || this.uploading;
  }

  invalidate(message = true) {
    if (!this.videoReady) { super.invalidate(message); return; }
    this.resultStale = true;
    this.updateResultControls();
    if (message && this.completed) this.status.textContent = this.t("videoResultStale");
    this.saveDraft();
  }

  setResult(result, { original, digest, stale = false, save = true } = {}) {
    this.completed = result;
    this.sourceAnalysis = result.sourceAnalysis || null;
    this.resultOriginal = original ?? this.getContext().prompt;
    this.resultKey = contextFingerprint(this.getContext());
    this.resultDigest = digest || this.fingerprint();
    this.resultStale = stale;
    this.resultPrompt.value = result.prompt;
    this.renderResult(this.resultOriginal);
    this.updateResultControls();
    this.status.textContent = this.t(stale ? "videoResultStale" : result.cache_hit ? "videoCacheHit" : "wanReady");
    if (save) this.saveDraft();
  }

  requestValue() {
    const context = this.buildContext();
    const live = this.getContext();
    const media = [...context.media, ...this.addedMedia.filter(item => item.kind === "image")];
    const resultMedia = this.output.value === "diagnose" ?
      [...(live.resultMedia || []), ...this.addedMedia.filter(item => item.kind === "video")] : [];
    if (this.output.value === "diagnose" && this.videoUrl.value.trim() &&
      !resultMedia.some(item => (item.url || item.previewUrl) === this.videoUrl.value.trim())) {
      resultMedia.push({ kind: "video", role: "result_video", analysisOnly: true, ready: true, url: this.videoUrl.value.trim() });
    }
    return { scope_id: this.scopeId, nonce: this.nonce || 0, profile_id: this.profile.value,
      model: this.model.value, analysis_model: this.analysisModel.value, target_profile_id: live.profileId,
      target_model: context.targetModel, prompt: context.prompt, params: context.params,
      domain: context.domain, mode: context.mode, output: context.output, analyze: this.analyze.checked,
      issue: context.issue, media, resultMedia, locale: context.locale };
  }

  receiveEvent(event) {
    if (event.type === "phase" || event.phase !== this.streamPhase) {
      this.streamPhase = event.phase; this.streamText = "";
    }
    if (event.type === "delta") {
      this.streamText = (this.streamText || "") + (event.text || event.data?.text || "");
      if (event.phase === "tuning") {
        const partial = draftPrompt(this.streamText);
        if (partial) { this.partialPrompt.value = partial; this.partialSection.hidden = false; }
      }
    }
    this.status.textContent = this.t(event.phase === "analysis" ? "wanAnalyzing" : event.phase === "cache" ? "videoCacheHit" : "wanTuning");
    this.saveDraft();
  }

  async resumeRun(id) {
    const request = new AbortController(); this.request = request;
    this.run.disabled = true; this.cancel.hidden = false; this.updateResultControls();
    const original = this.activeOriginal ?? this.getContext().prompt, digest = this.activeDigest || this.fingerprint();
    try {
      const result = await this.resumeTuning(id, request.signal, event => this.receiveEvent(event));
      this.runId = null;
      this.setResult(result, { original, digest, stale: digest !== this.fingerprint() });
    } catch (error) {
      if (!request.signal.aborted) { this.runId = null; this.status.textContent = error.message; }
    } finally { this.finishRequest(request); }
  }

  async execute(force = false) {
    if (this.request || this.uploading || !this.model.value || !this.profile.value) return;
    if (force && !this.externalRun) this.nonce = (this.nonce || 0) + 1;
    if (this.externalRun) {
      try {
        this.saveDraft();
        await this.externalRun(this.formState(), force);
        this.status.textContent = this.t("videoQueued");
      } catch (error) { this.status.textContent = error.message; }
      return;
    }
    const request = new AbortController(); this.request = request;
    const original = this.getContext().prompt, digest = this.fingerprint();
    this.activeOriginal = original; this.activeDigest = digest;
    this.run.disabled = true; this.cancel.hidden = false; this.resultStale = true;
    this.partialPrompt.value = ""; this.partialSection.hidden = true; this.streamText = "";
    this.updateResultControls();
    try {
      const result = await this.tune(this.requestValue(), request.signal, event => this.receiveEvent(event), id => {
        this.runId = id; this.saveDraft();
      });
      request.signal.throwIfAborted(); this.runId = null;
      this.setResult(result, { original, digest, stale: digest !== this.fingerprint() });
    } catch (error) {
      if (!request.signal.aborted) { this.runId = null; this.status.textContent = error.message || this.t("promptTransformFailed"); }
    } finally { this.finishRequest(request); }
  }

  finishRequest(request) {
    if (this.request === request) this.request = null;
    this.run.disabled = this.model.disabled || !this.model.value || Boolean(this.request);
    this.cancel.hidden = !this.request;
    this.updateResultControls(); this.saveDraft();
  }

  cancelRequest(message = true) {
    if (this.videoReady && this.detaching) {
      const reason = new Error("The tuning view was detached; the saved run can be resumed");
      reason.name = "TuningViewDetached";
      this.request?.abort(reason); this.request = null; this.cancel.hidden = true;
      this.updateResultControls(); this.saveDraft(); return;
    }
    super.cancelRequest(message);
    if (this.videoReady) { this.runId = null; this.updateResultControls(); this.saveDraft(); }
  }

  async copy(text) {
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
    } catch { /* HTTP and denied clipboard access use selection copying. */ }
    const temporary = make("textarea", { value: text, readOnly: true, tabIndex: -1 });
    Object.assign(temporary.style, { position: "fixed", opacity: "0", width: "1px", height: "1px" });
    const focused = document.activeElement;
    document.body.append(temporary); temporary.select();
    try { if (!document.execCommand("copy")) throw new Error("Copy unavailable"); }
    catch { this.status.textContent = this.t("wanCopyFailed"); }
    finally { temporary.remove(); focused?.focus?.({ preventScroll: true }); }
  }

  dispose() { this.detaching = true; this.uploadRequest?.abort(); this.saveDraft(); super.dispose(); }
}

// Keep the existing prompt-editor host interface while using the generic service.
export { VideoTunerPanel as WanTunerPanel };
