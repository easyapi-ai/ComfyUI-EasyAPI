import { WanTunerPanel, contextFingerprint } from "./wan-tuner-panel.js";
import { readTuningDraft, writeTuningDraft, tuningFingerprint } from "./video-tuning-state.js";

function make(tag, properties = {}, ...children) {
  const node = document.createElement(tag); Object.assign(node, properties); node.append(...children); return node;
}

const PHASES = new Set(["preparing", "analysis", "tuning", "validation", "cache"]);
const PHASE_LABELS = { preparing: "Preparing", analysis: "Analyzing media", tuning: "Tuning prompt", validation: "Validating result", cache: "Reusing result" };

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
    Object.assign(this, { tune: options.tune, resumeTuning: options.resumeTuning, cancelTuning: options.cancelTuning,
      uploadMedia: options.uploadMedia, externalRun: options.externalRun, scopeId, readOnlyApply: options.readOnlyApply === true });
    this.now = options.now || Date.now;
    this.phaseRecords = [];
    this.progressState = "idle";
    this.phaseTimer = null;
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
    this.phaseStatus = make("strong", { className: "ea-tuning-phase-status", role: "status", ariaLive: "polite" });
    this.phaseClock = make("span", { className: "ea-tuning-phase-clock" });
    this.phaseList = make("ol", { className: "ea-tuning-phases" });
    this.phaseList.setAttribute("aria-label", this.progressText("videoProgressStages", "Tuning stages"));
    this.phaseError = make("p", { className: "ea-tuning-phase-error", hidden: true });
    this.analysisGroups = make("div", { className: "ea-tuning-analysis-groups" });
    this.analysisPreview = make("details", { className: "ea-tuning-analysis", hidden: true },
      make("summary", { textContent: this.progressText("videoProgressAnalysisPreview", "Media analysis for this run") }), this.analysisGroups);
    this.progress = make("section", { className: "ea-tuning-progress", hidden: true },
      make("div", { className: "ea-tuning-progress-heading" }, this.phaseStatus, this.phaseClock), this.phaseList, this.phaseError, this.analysisPreview);
    this.root.insertBefore(this.progress, this.content);
    if (this.externalRun) {
      const input = this.content.querySelector('input[type="file"]');
      if (input) input.closest("label").hidden = true;
      this.videoUrl.closest("label").hidden = true;
      this.mediaList.parentElement.append(make("p", { className: "ea-caption", textContent: this.t("videoNodeMediaNote") }));
    }
    this.root.dataset.captureWheel = "true";
    if (form) this.restoreForm(form);
    if (saved?.completed) this.setResult(saved.completed, { original: saved.resultOriginal,
      digest: saved.resultDigest, stale: saved.stale || saved.attachmentCount > 0, save: false, progress: false });
    if (saved?.partial) {
      this.partialPrompt.value = saved.partial; this.partialSection.hidden = false;
    }
    this.runId = saved?.runId || null;
    this.activeDigest = saved?.activeDigest; this.activeOriginal = saved?.activeOriginal;
    if (saved?.progress) this.restoreProgress(saved.progress);
    if (saved?.analysisPreview) this.showAnalysis(saved.analysisPreview);
    if (options.initialResult) this.setResult(options.initialResult, { stale: true, progress: false });
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
      activeDigest: this.activeDigest, activeOriginal: this.activeOriginal,
      progress: { state: this.progressState, phase: this.currentPhase, records: this.phaseRecords,
        waitingSince: this.waitingSince, message: this.progressMessage, error: this.progressError }, analysisPreview: this.latestAnalysis });
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
    this.rerun.disabled = Boolean(this.request || this.runId) || this.uploading;
    this.run.textContent = this.runId && !this.request ? this.progressText("videoProgressResume", "Resume viewing") : this.t("wanRun");
    this.run.disabled = Boolean(this.request || this.uploading) || (!this.runId && (this.model.disabled || !this.model.value));
    this.cancel.hidden = !this.request && !this.runId;
  }

  invalidate(message = true) {
    if (!this.videoReady) { super.invalidate(message); return; }
    this.resultStale = true;
    this.updateResultControls();
    if (message && this.completed) this.status.textContent = this.t("videoResultStale");
    this.saveDraft();
  }

  setResult(result, { original, digest, stale = false, save = true, progress = true } = {}) {
    if (!result || typeof result.prompt !== "string" || !result.prompt.trim()) {
      const error = new Error(this.progressText("videoProgressInvalidResult", "The response did not contain a complete validated prompt."));
      error.phase = "validation"; throw error;
    }
    this.completed = result;
    this.sourceAnalysis = result.sourceAnalysis || null;
    this.resultOriginal = original ?? this.getContext().prompt;
    this.resultKey = contextFingerprint(this.getContext());
    this.resultDigest = digest || this.fingerprint();
    this.resultStale = stale;
    this.resultPrompt.value = result.prompt;
    this.renderResult(this.resultOriginal);
    this.updateResultControls();
    if (progress) this.completeProgress(result.cache_hit === true);
    this.status.textContent = stale ? this.t("videoResultStale") : progress ? "" : this.t(result.cache_hit ? "videoCacheHit" : "wanReady");
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

  progressText(key, fallback, values = {}) {
    let text = this.t(key, values);
    if (!text || text === key) text = fallback;
    for (const [name, value] of Object.entries(values)) text = text.replaceAll("{" + name + "}", String(value));
    return text;
  }

  phaseLabel(phase) { return this.progressText("videoProgressPhase_" + phase, PHASE_LABELS[phase] || phase); }

  restoreProgress(saved) {
    this.phaseRecords = (saved.records || []).filter(record => PHASES.has(record.phase) && record.state !== "skipped").map(record => ({ ...record }));
    this.currentPhase = PHASES.has(saved.phase) ? saved.phase : null;
    this.waitingSince = Number.isFinite(saved.waitingSince) ? saved.waitingSince : this.now();
    this.progressState = saved.state || "idle"; this.progressError = saved.error || "";
    this.progressMessage = saved.message || "";
    if (this.progressState !== "idle") this.renderProgress();
  }

  beginProgress(resume = false) {
    this.stopPhaseTimer();
    if (!resume) {
      this.phaseRecords = []; this.latestAnalysis = null; this.analysisPreview.hidden = true; this.analysisGroups.replaceChildren();
    } else for (const record of this.phaseRecords) if (record.state === "running") record.state = "paused";
    this.progressState = "running"; this.progressError = "";
    this.currentPhase = resume ? null : "preparing";
    this.waitingSince = this.now();
    this.progressMessage = resume ? this.progressText("videoProgressResuming", "Restoring job status…") : this.phaseLabel("preparing");
    if (!resume) this.phaseRecords.push({ phase: "preparing", state: "running", startedAt: this.waitingSince });
    this.status.textContent = ""; this.renderProgress(); this.startPhaseTimer(); this.saveDraft();
  }

  startPhaseTimer() {
    this.stopPhaseTimer();
    if (this.detaching || !["running", "pending", "queued"].includes(this.progressState)) return;
    this.phaseTimer = setInterval(() => this.paintPhaseClock(), 1000);
  }

  stopPhaseTimer() { if (this.phaseTimer != null) clearInterval(this.phaseTimer); this.phaseTimer = null; }

  paintPhaseClock() {
    const waiting = ["running", "pending", "queued"].includes(this.progressState);
    this.phaseClock.hidden = !waiting;
    if (waiting) {
      const seconds = Math.max(0, Math.floor((this.now() - (this.waitingSince ?? this.now())) / 1000));
      this.phaseClock.textContent = this.progressText("videoProgressWaiting", "Waiting {seconds}s", { seconds });
    }
  }

  renderProgress() {
    this.progress.hidden = this.progressState === "idle";
    this.progress.dataset.state = this.progressState;
    this.phaseStatus.textContent = this.progressMessage || "";
    this.phaseError.textContent = this.progressError || ""; this.phaseError.hidden = !this.progressError;
    const states = { running: ["videoProgressRunning", "In progress"], completed: ["videoProgressStepDone", "Completed"], failed: ["videoProgressStepFailed", "Failed"], cancelled: ["videoProgressCancelled", "Cancelled"], paused: ["videoProgressPaused", "Waiting for connection"] };
    this.phaseList.replaceChildren(...this.phaseRecords.filter(record => record.state !== "skipped").map(record => {
      const [key, fallback] = states[record.state] || states.paused;
      const item = make("li", { className: "ea-tuning-phase", textContent: this.phaseLabel(record.phase), title: this.progressText(key, fallback) });
      item.dataset.phase = record.phase; item.dataset.state = record.state;
      if (record.state === "running") item.setAttribute("aria-current", "step");
      return item;
    }));
    this.paintPhaseClock();
  }

  receiveEvent(event) {
    // Structured JSON and analysis are delivered only as complete server products.
    if (event.type !== "phase" || !PHASES.has(event.phase)) return;
    const phase = event.phase, state = event.state || "running";
    if (!["running", "completed", "skipped"].includes(state)) return;
    let record = this.phaseRecords.find(item => item.phase === phase);
    if (state === "skipped") {
      if (record) record.state = "skipped";
      if (this.currentPhase === phase) this.currentPhase = null;
    } else {
      if (!record) { record = { phase, state, startedAt: this.now() }; this.phaseRecords.push(record); }
      record.state = state;
      if (state === "running") {
        const previous = this.phaseRecords.find(item => item.phase === this.currentPhase);
        if (previous && previous !== record && previous.state === "running") previous.state = "completed";
        this.currentPhase = phase; this.waitingSince = record.startedAt;
        this.progressMessage = this.phaseLabel(phase);
      } else if (this.currentPhase === phase) this.currentPhase = null;
    }
    if (phase === "analysis" && state === "completed" && event.sourceAnalysis) this.showAnalysis(event.sourceAnalysis);
    this.progressState = "running"; this.progressError = "";
    this.renderProgress(); this.startPhaseTimer(); this.saveDraft();
  }

  showAnalysis(source) {
    this.latestAnalysis = source;
    this.analysisGroups.replaceChildren();
    for (const [field, key, fallback] of [["facts", "wanFacts", "Observed facts"], ["invariants", "wanInvariants", "Keep unchanged"], ["uncertainties", "wanUncertain", "Uncertainties"]]) {
      const items = (Array.isArray(source[field]) ? source[field] : []).filter(item => typeof item === "string" && item.trim());
      if (items.length) this.analysisGroups.append(make("section", {}, make("h5", { textContent: this.progressText(key, fallback) }),
        make("ul", {}, ...items.map(text => make("li", { textContent: text })))));
    }
    this.analysisPreview.hidden = this.analysisGroups.children.length === 0;
  }

  completeProgress(cacheHit = false) {
    if (cacheHit && !this.phaseRecords.some(record => record.phase === "cache")) this.phaseRecords.push({ phase: "cache", state: "completed", startedAt: this.now() });
    for (const record of this.phaseRecords) if (["running", "paused"].includes(record.state)) record.state = "completed";
    this.currentPhase = null; this.progressState = "completed"; this.progressError = "";
    this.progressMessage = this.progressText(cacheHit ? "videoCacheHit" : "videoProgressComplete", cacheHit ? "Reused the successful result." : "Complete · result validated");
    this.stopPhaseTimer(); this.renderProgress();
  }

  failProgress(error) {
    if (error.pending) {
      this.runId = error.runId || this.runId;
      const record = this.phaseRecords.find(item => item.phase === this.currentPhase);
      if (record?.state === "running") record.state = "paused";
      this.progressState = "pending";
      this.progressMessage = this.progressText("videoProgressConnectionLost", "Connection interrupted. The job may still be running; resume viewing its status.");
      this.progressError = error.message || ""; this.renderProgress(); this.startPhaseTimer(); return;
    }
    const phase = PHASES.has(error.phase) ? error.phase : this.currentPhase;
    let record = this.phaseRecords.find(item => item.phase === phase);
    if (phase && !record) { record = { phase, startedAt: this.now() }; this.phaseRecords.push(record); }
    if (record) record.state = "failed";
    for (const previous of this.phaseRecords) if (previous !== record && previous.state === "running") previous.state = "completed";
    this.currentPhase = phase; this.progressState = "failed";
    const seconds = error.timeoutSeconds ?? error.timeout_seconds;
    const label = phase ? this.phaseLabel(phase) : this.progressText("videoProgressUnknownStage", "Job");
    this.progressMessage = Number.isFinite(seconds) && seconds > 0 ?
      this.progressText("videoProgressTimeout", "{stage} timed out after {seconds}s", { stage: label, seconds }) :
      this.progressText("videoProgressFailed", "{stage} failed", { stage: label });
    this.progressError = error.message || this.t("promptTransformFailed");
    this.stopPhaseTimer(); this.renderProgress();
  }

  async resumeRun(id) {
    const request = new AbortController(); this.request = request;
    this.beginProgress(true);
    this.run.disabled = true; this.cancel.hidden = false; this.updateResultControls();
    const original = this.activeOriginal ?? this.getContext().prompt, digest = this.activeDigest || this.fingerprint();
    try {
      const result = await this.resumeTuning(id, request.signal, event => { if (this.request === request && !request.signal.aborted) this.receiveEvent(event); });
      request.signal.throwIfAborted();
      this.runId = null;
      this.setResult(result, { original, digest, stale: digest !== this.fingerprint() });
    } catch (error) {
      if (!request.signal.aborted) { if (!error.pending) this.runId = null; this.failProgress(error); }
    } finally { this.finishRequest(request); }
  }

  async execute(force = false) {
    if (this.request || this.uploading) return;
    if (this.runId && this.resumeTuning && !this.externalRun) {
      if (!force) return this.resumeRun(this.runId);
      return;
    }
    if (!this.model.value || !this.profile.value) return;
    if (force && !this.externalRun) this.nonce = (this.nonce || 0) + 1;
    if (this.externalRun) {
      try {
        this.beginProgress();
        this.saveDraft();
        await this.externalRun(this.formState(), force);
        if (!["completed", "failed", "cancelled"].includes(this.progressState)) {
          for (const record of this.phaseRecords) if (record.state === "running") record.state = "completed";
          this.progressState = "queued"; this.currentPhase = null;
          this.progressMessage = this.progressText("videoProgressNodeQueued", "Node queued · waiting for its result");
          this.renderProgress(); this.startPhaseTimer(); this.saveDraft();
        }
      } catch (error) { this.failProgress(error); this.saveDraft(); }
      return;
    }
    const request = new AbortController(); this.request = request;
    const original = this.getContext().prompt, digest = this.fingerprint();
    this.activeOriginal = original; this.activeDigest = digest;
    this.run.disabled = true; this.cancel.hidden = false; this.resultStale = true;
    this.beginProgress();
    this.updateResultControls();
    try {
      const result = await this.tune(this.requestValue(), request.signal, event => { if (this.request === request && !request.signal.aborted) this.receiveEvent(event); }, id => {
        this.runId = id; this.saveDraft();
      });
      request.signal.throwIfAborted(); this.runId = null;
      this.setResult(result, { original, digest, stale: digest !== this.fingerprint() });
    } catch (error) {
      if (!request.signal.aborted) { if (!error.pending) this.runId = null; this.failProgress(error); }
    } finally { this.finishRequest(request); }
  }

  finishRequest(request) {
    if (this.request === request) this.request = null;
    this.run.disabled = this.model.disabled || !this.model.value || Boolean(this.request);
    this.cancel.hidden = !this.request && !this.runId;
    this.updateResultControls(); this.saveDraft();
  }

  cancelRequest(message = true) {
    if (this.videoReady && this.detaching) {
      const reason = new Error("The tuning view was detached; the saved run can be resumed");
      reason.name = "TuningViewDetached";
      this.request?.abort(reason); this.request = null; this.cancel.hidden = true;
      this.stopPhaseTimer();
      this.updateResultControls(); this.saveDraft(); return;
    }
    const acceptedId = this.runId, wasRunning = Boolean(this.request || acceptedId);
    if (this.videoReady && acceptedId && !this.request) {
      if (this.cancelTuning) Promise.resolve(this.cancelTuning(acceptedId)).catch(error => {
        if (this.detaching || this.request) return;
        if (![403, 404, 410].includes(error.status)) { error.pending = true; error.runId = acceptedId; }
        this.failProgress(error); this.updateResultControls(); this.saveDraft();
      });
      else if (this.resumeTuning) this.resumeRun(acceptedId).catch(() => {});
    }
    super.cancelRequest(message);
    if (this.videoReady) {
      this.runId = null;
      if (wasRunning) {
        for (const record of this.phaseRecords) if (["running", "paused"].includes(record.state)) record.state = "cancelled";
        this.progressState = "cancelled"; this.currentPhase = null; this.progressError = "";
        this.progressMessage = this.progressText("videoProgressCancelled", "Cancelled"); this.stopPhaseTimer(); this.renderProgress();
      }
      this.updateResultControls(); this.saveDraft();
    }
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

  dispose() { this.detaching = true; this.stopPhaseTimer(); this.uploadRequest?.abort(); this.saveDraft(); super.dispose(); }
}

// Keep the existing prompt-editor host interface while using the generic service.
export { VideoTunerPanel as WanTunerPanel };
