import { buildAnalysisMessages, buildTuningMessages, inferTuningMode, loadKnowledge,
  normalizeTuningMedia, parseAnalysisResult, parseTuningResult } from "./wan-tuner-core.js";

const MODES = ["text-to-video", "first-frame", "first-last-frame", "reference", "v2v"];
const OUTPUTS = ["quick", "full", "diagnose"];

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function button(text, action, className = "") {
  const node = element("button", { type: "button", textContent: text, className });
  node.addEventListener("click", action);
  return node;
}

function select(values, label) {
  return element("select", {}, ...values.map(value => element("option", { value, textContent: label(value) })));
}

function field(label, control) {
  control.setAttribute("aria-label", label);
  return element("label", {}, element("span", { textContent: label }), control);
}

export function contextFingerprint(context) {
  return JSON.stringify({ profileId: context.profileId, targetModel: context.targetModel || context.modelId,
    prompt: context.prompt, mode: context.mode, params: context.params, capabilities: context.capabilities,
    media: context.media || [], resultMedia: context.resultMedia || [] });
}

function mediaShape(context) {
  return JSON.stringify([context.profileId, context.targetModel || context.modelId, context.mode,
    (context.media || []).map(item => [item.kind, item.role])]);
}

function visualModel(model) {
  return model.supports_image === true || model.input_modalities?.includes("image");
}

function lines(value) {
  if (value == null) return [];
  return (Array.isArray(value) ? value : [value]).map(item => typeof item === "string" ? item : JSON.stringify(item));
}

function addSection(parent, label, value) {
  const items = lines(value).filter(Boolean);
  if (!items.length) return;
  parent.append(element("section", { className: "ea-wan-section" }, element("h4", { textContent: label }),
    element("ul", {}, ...items.map(text => element("li", { textContent: text })))));
}

function analysisText(result, source) {
  return [result.analysis, ...(result.changes || []), ...(result.risks || []), ...(result.assumptions || []),
    ...Object.values(result.diagnosis || {}).flatMap(lines), ...lines(source?.facts), ...lines(source?.uncertainties)]
    .filter(Boolean).join("\n\n");
}

function partialPrompt(text) {
  const match = text.match(/"prompt"\s*:\s*"((?:\\.|[^"\\])*)/s);
  if (!match) return "";
  try { return JSON.parse('"' + match[1].replace(/\\$/, "") + '"'); }
  catch { return ""; }
}

function diffText(before, after) {
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) ++start;
  let suffix = 0;
  while (suffix < before.length - start && suffix < after.length - start &&
         before[before.length - suffix - 1] === after[after.length - suffix - 1]) ++suffix;
  const root = element("div", { className: "ea-wan-diff" });
  root.append(element("span", { textContent: before.slice(0, start) }));
  if (before.length - suffix > start) root.append(element("del", { textContent: before.slice(start, before.length - suffix) }));
  if (after.length - suffix > start) root.append(element("ins", { textContent: after.slice(start, after.length - suffix) }));
  if (suffix) root.append(element("span", { textContent: after.slice(-suffix) }));
  return root;
}

async function fileData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(file.name));
    reader.readAsDataURL(file);
  });
}

export async function videoFrames(media, issue, signal, maxFrames = 7) {
  const video = document.createElement("video");
  video.crossOrigin = "anonymous";
  video.muted = true;
  video.preload = "auto";
  const wait = event => new Promise((resolve, reject) => {
    const timer = setTimeout(() => done(new Error("Video preview timed out")), 20000);
    const done = error => {
      clearTimeout(timer);
      video.removeEventListener(event, ready);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
      error ? reject(error) : resolve();
    };
    const ready = () => done();
    const failed = () => done(new Error("Video preview is unavailable"));
    const aborted = () => done(new DOMException("Aborted", "AbortError"));
    video.addEventListener(event, ready, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
  });
  try {
    const metadata = wait("loadedmetadata");
    video.src = media.previewUrl || media.url;
    await metadata;
    signal.throwIfAborted();
    const duration = video.duration;
    if (!Number.isFinite(duration) || duration <= 0) throw new Error("Video duration is unavailable");
    const start = Math.max(0, Math.min(issue?.start ?? 0, duration));
    const end = Math.max(start, Math.min(issue?.end ?? duration, duration));
    let times = [...new Set([Math.max(0, start - 0.5), start, start + (end - start) / 4,
      start + (end - start) / 2, start + (end - start) * 3 / 4, end, end + 0.5]
      .map(time => Math.max(0, Math.min(time, duration - 0.04))))];
    if (times.length > maxFrames) times = Array.from({ length: maxFrames }, (_, index) =>
      times[Math.round(index * (times.length - 1) / (maxFrames - 1))]);
    const canvas = document.createElement("canvas");
    const ratio = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * ratio);
    canvas.height = Math.round(video.videoHeight * ratio);
    const context = canvas.getContext("2d");
    const frames = [];
    for (const time of times) {
      signal.throwIfAborted();
      if (Math.abs(video.currentTime - time) > 0.0005 || video.readyState < 2) {
        const seeking = wait("seeked");
        video.currentTime = time || 0.001;
        await seeking;
      }
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      frames.push({ id: media.id + "-" + time.toFixed(2), kind: "image", role: "video_frame", ready: true,
        analysisOnly: true, sourceMediaId: media.id, sourceLabel: media.label || media.name || media.id, timestamp: time,
        name: `${media.name || media.id} @ ${time.toFixed(2)}s`, url: canvas.toDataURL("image/jpeg", 0.85) });
    }
    return frames;
  } finally {
    video.removeAttribute("src");
    video.load?.();
  }
}

export class WanTunerPanel {
  constructor({ t, getContext, loadModels, transform, resolveMedia, onApply, preferences = {}, onPreferences,
                prepareVideo = videoFrames, knowledgeLoader = loadKnowledge }) {
    Object.assign(this, { t, getContext, loadModels, transform, resolveMedia, onApply, onPreferences,
      prepareVideo, knowledgeLoader });
    this.root = element("div", { className: "ea-wan-panel" });
    this.addedMedia = [];
    this.objectUrls = [];
    this.modelPreferences = new Map();
    this.contextKey = contextFingerprint(getContext());
    this.shapeKey = mediaShape(getContext());
    this.domain = select(["drama", "ecommerce"], value => t("wanDomain_" + value));
    this.domain.value = preferences.domain || "drama";
    this.mode = select(MODES, value => t("wanMode_" + value));
    this.mode.value = preferences.shape === this.shapeKey ? preferences.mode : inferTuningMode(getContext());
    this.output = select(OUTPUTS, value => t("wanOutput_" + value));
    this.output.value = preferences.output || "quick";
    const profiles = getContext().profiles || [];
    this.profile = element("select", {}, element("option", { value: "", textContent: t("chooseConnection") }),
      ...profiles.map(entry => element("option", { value: entry.id, textContent: entry.name || entry.id })));
    this.profile.value = getContext().profileId || (profiles.length === 1 ? profiles[0].id : "");
    this.model = element("select", { disabled: true });
    this.analysisModel = element("select", { disabled: true });
    this.analyze = element("input", { type: "checkbox", checked: true });
    this.issue = element("textarea", { rows: 2, placeholder: t("wanIssuePlaceholder") });
    this.start = element("input", { type: "number", min: 0, step: "0.1", placeholder: t("wanStart") });
    this.end = element("input", { type: "number", min: 0, step: "0.1", placeholder: t("wanEnd") });
    this.videoUrl = element("input", { type: "url", placeholder: t("wanVideoUrl") });
    this.mediaList = element("ul", { className: "ea-wan-media" });
    this.status = element("p", { className: "ea-prompt-status", role: "status" });
    this.catalogStatus = element("span", { className: "ea-caption", role: "status" });
    this.retryModels = button(t("retryModels"), () => this.refreshModels({}, true));
    this.retryModels.hidden = true;
    this.resultPrompt = element("textarea", { rows: 6, readOnly: true });
    this.details = element("div", { className: "ea-wan-details" });
    this.result = element("div", { className: "ea-prompt-result ea-wan-result", hidden: true });
    this.apply = button(t("promptUseResult"), () => {
      if (!this.validResult()) { this.invalidate(); return; }
      this.onApply(this.completed.prompt);
    }, "ea-primary");
    this.copyPrompt = button(t("wanCopyPrompt"), () => this.copy(this.completed?.prompt));
    this.copyAnalysis = button(t("wanCopyAnalysis"), () => this.copy(analysisText(this.completed || {}, this.sourceAnalysis)));
    this.run = button(t("wanRun"), () => this.execute(), "ea-primary");
    this.cancel = button(t("wanCancel"), () => this.cancelRequest());
    this.cancel.hidden = true;
    this.result.append(field(t("wanPromptResult"), this.resultPrompt),
      element("div", { className: "ea-prompt-actions" }, this.copyPrompt, this.copyAnalysis), this.details);
    const attachment = element("input", { type: "file", accept: "image/*,video/*", multiple: true });
    attachment.addEventListener("change", () => this.addFiles(attachment.files));
    this.contextParams = element("pre", { className: "ea-wan-context", textContent: this.contextSummary() });
    this.issueFields = element("div", { className: "ea-wan-issue" }, field(t("wanIssue"), this.issue),
      element("div", { className: "ea-wan-time-range" }, field(t("wanStart"), this.start), field(t("wanEnd"), this.end)),
      field(t("wanResultVideo"), this.videoUrl));
    const form = element("div", { className: "ea-prompt-form ea-wan-form" },
      field(t("wanDomain"), this.domain), field(t("wanMode"), this.mode), field(t("wanOutput"), this.output),
      field(t("connection"), this.profile), field(t("promptTransformModel"), this.model),
      field(t("wanAnalysisModel"), this.analysisModel));
    this.content = element("div", { className: "ea-wan-content" }, form, this.contextParams,
      element("p", { className: "ea-caption", textContent: t("promptTransformCost") }),
      element("div", { className: "ea-model-updates" }, this.catalogStatus, this.retryModels),
      element("details", { open: true, className: "ea-wan-materials" }, element("summary", { textContent: t("wanMaterials") }),
        this.mediaList, field(t("wanAddMedia"), attachment), field(t("wanAnalyzeMedia"), this.analyze)),
      this.issueFields, this.status, this.result);
    this.root.append(this.content, element("div", { className: "ea-prompt-actions ea-wan-footer" }, this.run, this.cancel, this.apply));
    for (const control of [this.domain, this.mode, this.output, this.model, this.analysisModel, this.analyze,
      this.issue, this.start, this.end, this.videoUrl]) control.addEventListener("input", () => this.changed());
    this.output.addEventListener("change", () => this.updateIssue());
    this.profile.addEventListener("change", () => { this.invalidate(); this.refreshModels(); });
    this.updateIssue();
    this.renderMedia();
    this.invalidate(false);
    this.refreshModels(preferences);
    this.timer = setInterval(() => this.checkContext(), 500);
  }

  contextSummary() {
    const context = this.getContext();
    return [context.targetModel || context.modelId, JSON.stringify(context.params || {}, null, 2)].filter(Boolean).join("\n");
  }

  checkContext() {
    if (this.catalogKey !== this.modelScopeKey()) { this.invalidate(); this.refreshModels(); }
    const key = contextFingerprint(this.getContext());
    if (key === this.contextKey) return;
    this.contextKey = key;
    const shape = mediaShape(this.getContext());
    if (shape !== this.shapeKey) {
      this.shapeKey = shape;
      this.mode.value = inferTuningMode(this.getContext());
    }
    this.contextParams.textContent = this.contextSummary();
    this.renderMedia();
    this.invalidate();
  }

  changed() {
    this.invalidate();
    const preferences = { domain: this.domain.value, mode: this.mode.value, shape: this.shapeKey, output: this.output.value,
      profileId: this.profile.value, model: this.model.value, analysisModel: this.analysisModel.value };
    this.modelPreferences.set(this.profile.value, preferences);
    this.onPreferences?.(preferences);
  }

  updateIssue() {
    this.issueFields.hidden = this.output.value !== "diagnose";
  }

  renderMedia() {
    const context = this.getContext();
    const media = [...(context.media || []), ...this.addedMedia];
    this.mediaList.replaceChildren(...media.map(item => {
      const row = element("li", { textContent:
        `${item.name || item.id || item.role} · ${this.t(item.analysisOnly ? "wanAnalysisAttachment" : "port." + item.role)} · ` +
        this.t(item.ready ? "wanMediaReady" : "wanMediaPending") });
      if (this.addedMedia.includes(item)) {
        row.append(button(this.t("removeItem"), () => {
          this.addedMedia = this.addedMedia.filter(entry => entry !== item);
          if (item.url?.startsWith("blob:")) URL.revokeObjectURL(item.url);
          this.invalidate();
          this.renderMedia();
        }));
      }
      return row;
    }));
    if (!media.length) this.mediaList.append(element("li", { textContent: this.t("wanNoMedia") }));
    const video = context.resultMedia?.find(item => item.kind === "video" && item.ready);
    if (video && !this.videoUrl.value) this.videoUrl.value = video.previewUrl || video.url || "";
  }

  async addFiles(files) {
    this.invalidate();
    try {
      for (const file of files || []) {
        const kind = file.type.startsWith("video/") ? "video" : "image";
        const id = "attachment-" + (this.addedMedia.length + 1);
        if (kind === "video") {
          const url = URL.createObjectURL(file);
          this.objectUrls.push(url);
          this.addedMedia.push({ id, name: file.name, kind, role: "result_video", analysisOnly: true, ready: true, previewUrl: url, url });
          this.output.value = "diagnose";
          this.updateIssue();
        } else {
          if (file.size > 10 * 1024 * 1024) throw new Error(this.t("wanImageTooLarge"));
          this.addedMedia.push({ id, name: file.name, kind, role: "analysis_image", analysisOnly: true,
            sourceLabel: file.name, ready: true, url: await fileData(file) });
        }
      }
    } catch (error) { this.status.textContent = error.message; }
    finally { this.renderMedia(); }
  }

  modelScopeKey() {
    const profile = this.getContext().profiles?.find((row) => row.id === this.profile.value);
    return JSON.stringify([this.profile.value, profile?.model_catalog_revision]);
  }

  async refreshModels(preferences = {}, refresh = false) {
    this.modelRequest?.abort();
    const request = new AbortController();
    this.modelRequest = request;
    this.catalogKey = this.modelScopeKey();
    preferences = this.modelPreferences.get(this.profile.value) ||
      (!preferences.profileId || preferences.profileId === this.profile.value ? preferences : {});
    this.run.disabled = this.model.disabled = this.analysisModel.disabled = true;
    this.model.replaceChildren(); this.analysisModel.replaceChildren();
    this.model.value = this.analysisModel.value = "";
    this.retryModels.hidden = true;
    if (!this.profile.value) { this.catalogStatus.textContent = this.t("profileNeeded"); return; }
    this.catalogStatus.textContent = this.t("loadingModels");
    let applied;
    const applyModels = (models) => {
      if (request.signal.aborted || applied === models) return;
      const previous = this.model.value, previousAnalysis = this.analysisModel.value;
      const replacing = applied !== undefined;
      applied = models;
      this.models = models;
      this.model.replaceChildren(element("option", { value: "", textContent: this.t("chooseModel") }),
        ...models.map(item => element("option", { value: item.id, textContent: item.display_name || item.id })));
      const wanted = replacing ? previous : preferences.model || this.getContext().modelId;
      this.model.value = models.some(item => item.id === wanted) ? wanted : !replacing && models.length === 1 ? models[0].id : "";
      const visual = models.filter(visualModel);
      this.analysisModel.replaceChildren(element("option", { value: "", textContent: this.t("wanAnalysisOptional") }),
        ...visual.map(item => element("option", { value: item.id, textContent: item.display_name || item.id })));
      const wantedAnalysis = replacing ? previousAnalysis : preferences.analysisModel;
      this.analysisModel.value = visual.some(item => item.id === wantedAnalysis) ? wantedAnalysis : replacing ? "" :
        visual.some(item => item.id === this.model.value) ? this.model.value : visual.length === 1 ? visual[0].id : "";
      this.model.disabled = !models.length;
      this.analysisModel.disabled = !visual.length;
      this.run.disabled = !this.model.value || Boolean(this.request);
      if (replacing && (previous !== this.model.value || previousAnalysis !== this.analysisModel.value)) this.invalidate();
      this.catalogStatus.textContent = models.length ? (visual.length ? "" : this.t("wanNoVisionModel")) : this.t("noModels");
    };
    const update = (state) => {
      if (request.signal.aborted) return;
      if (state.invalidated) {
        request.abort();
        this.invalidate(); applied = undefined; this.models = [];
        this.model.replaceChildren(); this.analysisModel.replaceChildren();
        this.model.value = this.analysisModel.value = "";
        this.run.disabled = this.model.disabled = this.analysisModel.disabled = true;
      } else if (state.models) applyModels(state.models);
      this.retryModels.hidden = !state.error && !state.invalidated;
      if (state.error) this.catalogStatus.textContent = state.invalidated ? state.error.message : this.t("modelUpdateFailed");
      else if (state.updating) this.catalogStatus.textContent = this.t(state.value ? "updatingModels" : "loadingModels");
    };
    try {
      const models = await this.loadModels(this.profile.value, request.signal, update,
        { profiles: this.getContext().profiles, refresh });
      applyModels(models);
    } catch (error) {
      if (!request.signal.aborted) { this.catalogStatus.textContent = error.message; this.retryModels.hidden = false; }
    }
  }

  buildContext() {
    const live = this.getContext();
    const start = this.start.value === "" ? undefined : Number(this.start.value);
    const end = this.end.value === "" ? undefined : Number(this.end.value);
    if (this.output.value === "diagnose" && ((start !== undefined && (!Number.isFinite(start) || start < 0)) ||
        (end !== undefined && (!Number.isFinite(end) || end <= (start ?? 0))) ||
        (start === undefined) !== (end === undefined))) throw new Error(this.t("wanInvalidRange"));
    if (this.output.value === "diagnose" && !this.issue.value.trim()) throw new Error(this.t("wanIssueRequired"));
    const media = [...(live.media || [])];
    return { domain: this.domain.value, mode: this.mode.value, output: this.output.value, prompt: live.prompt,
      targetModel: live.targetModel || live.modelId, params: live.params || {}, capabilities: live.capabilities, media,
      issue: this.output.value === "diagnose" ? { description: this.issue.value.trim(), start, end } : undefined,
      locale: live.locale || "en" };
  }

  async analysisMedia(context, signal) {
    const labeled = normalizeTuningMedia(context.media);
    let media = [...context.media.map((item, index) => ({ ...item, label: labeled[index].label })), ...this.addedMedia];
    const videos = [];
    if (this.output.value === "diagnose" && this.videoUrl.value.trim() && !media.some(item => item.url === this.videoUrl.value.trim()))
      videos.push({ id: "result-video", name: this.t("wanResultVideo"), kind: "video", role: "result_video",
        analysisOnly: true, ready: true, url: this.videoUrl.value.trim() });
    media.push(...videos);
    media.sort((a, b) => Number(b.role === "result_video") - Number(a.role === "result_video"));
    const imageCount = media.filter(item => item.kind === "image" && item.ready).length;
    const videoCount = media.filter(item => item.kind === "video" && item.ready).length;
    const frameLimit = Math.max(2, Math.min(7, Math.floor((24 - imageCount) / Math.max(1, videoCount))));
    const result = [];
    const uncertain = [];
    for (const item of media) {
      signal.throwIfAborted();
      if (!item.ready) { uncertain.push(`${item.name || item.id}: ${this.t("wanMediaPending")}`); continue; }
      try {
        if (item.kind === "video") result.push(...await this.prepareVideo(item, context.issue, signal, frameLimit));
        else if (item.kind === "image") {
          if (item.filename || /^(?:\/view\?|https?:\/\/[^/]+\/view\?)/.test(item.url || "")) {
            const resolved = await this.resolveMedia({ profile_id: this.profile.value, media: [item] }, signal);
            result.push(...(resolved.media || []).filter(entry => entry.ready));
            for (const missing of (resolved.media || []).filter(entry => !entry.ready))
              uncertain.push(`${missing.name || item.name || item.id}: ${missing.error || this.t("wanMediaPending")}`);
          } else if (item.url) result.push(item);
        }
      } catch (error) {
        if (signal.aborted) throw error;
        uncertain.push(`${item.name || item.id}: ${error.message}`);
      }
    }
    if (result.length > 24) uncertain.push(this.t("wanAnalysisLimit"));
    return { media: normalizeTuningMedia(result.slice(0, 24)), uncertain };
  }

  async execute() {
    if (!this.model.value || !this.profile.value || this.request) return;
    this.invalidate(false);
    const request = new AbortController();
    this.request = request;
    const sourceKey = contextFingerprint(this.getContext());
    const original = this.getContext().prompt;
    this.run.disabled = true;
    this.cancel.hidden = false;
    this.status.textContent = this.t("wanPreparing");
    this.sourceAnalysis = null;
    this.streamText = "";
    this.resultPrompt.value = "";
    this.details.replaceChildren();
    try {
      const context = this.buildContext();
      const knowledge = await this.knowledgeLoader();
      request.signal.throwIfAborted();
      let source = { facts: [], invariants: [], uncertainties: [this.t("wanNotAnalyzed")] };
      if (this.analyze.checked && this.analysisModel.value) {
        this.status.textContent = this.t("wanAnalyzing");
        const ready = await this.analysisMedia(context, request.signal);
        if (ready.media.length) {
          const analysisContext = { ...context, media: ready.media };
          const response = await this.transform({ action: "wan_analyze", profile_id: this.profile.value,
            model: this.analysisModel.value, messages: buildAnalysisMessages(analysisContext), stream: true },
          request.signal, event => { if (!request.signal.aborted) this.streamEvent(event, "analysis"); });
          source = parseAnalysisResult(response.text);
          source.uncertainties.push(...ready.uncertain);
        } else source.uncertainties.push(...ready.uncertain);
      }
      request.signal.throwIfAborted();
      if (sourceKey !== contextFingerprint(this.getContext()) || original !== this.getContext().prompt) {
        this.invalidate(); return;
      }
      this.sourceAnalysis = source;
      this.status.textContent = this.t("wanTuning");
      this.streamText = "";
      const response = await this.transform({ action: "wan_tune", profile_id: this.profile.value,
        model: this.model.value, messages: buildTuningMessages(context, source, knowledge), stream: true },
      request.signal, event => { if (!request.signal.aborted) this.streamEvent(event, "tuning"); });
      request.signal.throwIfAborted();
      if (sourceKey !== contextFingerprint(this.getContext()) || original !== this.getContext().prompt) {
        this.invalidate(); return;
      }
      const completed = parseTuningResult(response.text, { requireDiagnosis: context.output === "diagnose", context });
      this.completed = completed;
      this.resultKey = sourceKey;
      this.resultOriginal = original;
      this.resultPrompt.value = this.completed.prompt;
      this.renderResult(original);
      this.result.hidden = false;
      this.apply.disabled = this.copyPrompt.disabled = this.copyAnalysis.disabled = false;
      this.apply.hidden = false;
      this.status.textContent = this.t("wanReady");
    } catch (error) {
      if (!request.signal.aborted) {
        this.completed = null;
        this.status.textContent = error.message || this.t("promptTransformFailed");
      }
    } finally {
      if (this.request === request) this.request = null;
      this.run.disabled = this.model.disabled || !this.model.value || Boolean(this.request);
      this.cancel.hidden = !this.request;
    }
  }

  streamEvent(event, phase) {
    if (event.type !== "delta") return;
    this.streamText = (this.streamText || "") + (event.data?.text || "");
    this.status.textContent = `${this.t(phase === "analysis" ? "wanAnalyzing" : "wanTuning")} ${this.streamText.length}`;
    if (phase === "tuning") {
      const partial = partialPrompt(this.streamText);
      if (partial) { this.resultPrompt.value = partial; this.result.hidden = false; }
    }
  }

  renderResult(original) {
    this.details.replaceChildren();
    this.details.append(element("details", {}, element("summary", { textContent: this.t("wanDiff") }),
      diffText(original, this.completed.prompt)));
    addSection(this.details, this.t("wanAnalysis"), this.completed.analysis);
    addSection(this.details, this.t("wanChanges"), this.completed.changes);
    addSection(this.details, this.t("wanFacts"), this.sourceAnalysis?.facts);
    addSection(this.details, this.t("wanInvariants"), this.sourceAnalysis?.invariants);
    addSection(this.details, this.t("wanUncertain"), this.sourceAnalysis?.uncertainties);
    for (const [key, value] of Object.entries(this.completed.diagnosis || {})) addSection(this.details, this.t("wanDiagnosis_" + key), value);
    addSection(this.details, this.t("wanRisks"), this.completed.risks);
    addSection(this.details, this.t("wanAssumptions"), this.completed.assumptions);
    if (Object.keys(this.completed.suggestedParams || {}).length) this.details.append(element("section", { className: "ea-wan-section" },
      element("h4", { textContent: this.t("wanSuggestedParams") }),
      element("pre", { textContent: JSON.stringify(this.completed.suggestedParams, null, 2) })));
  }

  validResult() {
    return Boolean(this.completed && this.resultOriginal === this.getContext().prompt &&
      this.resultKey === contextFingerprint(this.getContext()));
  }

  invalidate(message = true) {
    this.cancelRequest(false);
    this.completed = null;
    this.result.hidden = true;
    this.apply.disabled = this.copyPrompt.disabled = this.copyAnalysis.disabled = true;
    this.apply.hidden = true;
    if (message) this.status.textContent = this.t("wanContextChanged");
  }

  cancelRequest(message = true) {
    this.request?.abort();
    this.request = null;
    this.run.disabled = this.model.disabled || !this.model.value;
    this.cancel.hidden = true;
    if (message) this.status.textContent = this.t("wanCanceled");
  }

  async copy(text) {
    if (!text || !this.validResult()) return;
    try {
      if (globalThis.navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
      }
    } catch { /* LAN HTTP and browser permissions can require the selection-based copy path. */ }
    const focused = document.activeElement;
    const selection = focused && Number.isInteger(focused.selectionStart)
      ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
    const temporary = element("textarea", { value: text, readOnly: true, tabIndex: -1 });
    temporary.setAttribute("aria-hidden", "true");
    Object.assign(temporary.style, { position: "fixed", inset: "0 auto auto 0", width: "1px", height: "1px",
      opacity: "0", pointerEvents: "none" });
    try {
      document.body.append(temporary);
      temporary.focus({ preventScroll: true });
      temporary.select();
      temporary.setSelectionRange(0, text.length);
      if (!document.execCommand?.("copy")) throw new Error("Copy was unavailable");
    } catch { this.status.textContent = this.t("wanCopyFailed"); }
    finally {
      temporary.remove();
      if (focused?.isConnected !== false) {
        focused?.focus?.({ preventScroll: true });
        if (selection) focused.setSelectionRange?.(...selection);
      }
    }
  }

  dispose() {
    clearInterval(this.timer);
    this.modelRequest?.abort();
    this.cancelRequest(false);
    for (const url of this.objectUrls) URL.revokeObjectURL(url);
  }
}
