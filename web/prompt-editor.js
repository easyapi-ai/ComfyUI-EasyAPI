const TOOLS = [
  ["history", "promptHistory"],
  ["undo", "promptUndo"],
  ["redo", "promptRedo"],
  ["tags", "promptTags"],
  ["translate", "promptTranslate"],
  ["optimize", "promptOptimize"],
  ["wan", "promptWanTune"],
];
const LANGUAGES = [["en", "promptLanguageEn"], ["zh", "promptLanguageZh"], ["zh-TW", "promptLanguageZhTW"]];
const TAGS = {
  promptTagGroupQuality: [
    ["fine detail", "丰富细节", "豐富細節"], ["natural texture", "自然质感", "自然質感"],
    ["sharp focus", "清晰对焦", "清晰對焦"], ["subtle film grain", "轻微颗粒", "輕微顆粒"],
  ],
  promptTagGroupLight: [
    ["soft daylight", "柔和日光", "柔和日光"], ["warm backlight", "暖色逆光", "暖色逆光"],
    ["window light", "窗边光", "窗邊光"], ["volumetric lighting", "体积光", "體積光"],
  ],
  promptTagGroupComposition: [
    ["wide shot", "远景", "遠景"], ["close-up", "特写", "特寫"],
    ["overhead view", "俯拍", "俯拍"], ["shallow depth of field", "浅景深", "淺景深"],
  ],
  promptTagGroupStyle: [
    ["documentary style", "纪实风格", "紀實風格"], ["watercolor", "水彩", "水彩"],
    ["editorial photography", "编辑摄影", "編輯攝影"], ["muted colors", "低饱和配色", "低飽和配色"],
  ],
  promptTagGroupMotion: [
    ["slow dolly-in", "缓慢推进", "緩慢推進"], ["steady tracking shot", "稳定跟拍", "穩定跟拍"],
    ["smooth pan", "平滑摇镜", "平滑搖鏡"], ["locked-off camera", "固定机位", "固定機位"],
  ],
};
const HISTORY_LIMIT = 20;
const HISTORY_TEXT_LIMIT = 16000;
const UNDO_LIMIT = 40;
const TYPING_DELAY = 700;
const TOOL_COLLAPSE_DELAY = 240;
let nextId = 0;

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function button(text, onClick, className = "") {
  const node = element("button", { type: "button", className, textContent: text });
  node.addEventListener("click", onClick);
  return node;
}

function option(value, text, disabled = false) {
  return element("option", { value, textContent: text, disabled });
}

function field(label, control) {
  control.setAttribute("aria-label", label);
  return element("label", {}, element("span", { textContent: label }), control);
}

function snapshot(text, start = text.length, end = start) {
  return { text, start, end };
}

export class PromptEditor {
  constructor({ value = "", t, onInput, getContext, loadModels, transform, resolveMedia, historyKey,
                getLocale = () => "", loadWanPanel = () => import("./wan-tuner-panel.js") }) {
    Object.assign(this, { t, onInput, getContext, loadModels, transform, resolveMedia, historyKey, getLocale, loadWanPanel });
    this.current = snapshot(String(value ?? ""));
    this.undoStack = [];
    this.redoStack = [];
    this.history = [];
    this.revision = 0;
    this.preferredModels = new Map();
    this.linked = false;
    this.toolsHovered = false;
    this.toolsPinned = false;

    const id = ++nextId;
    this.root = element("div", { className: "ea-prompt-editor" });
    this.textarea = element("textarea", { id: "easyapi-prompt-input-" + id, className: "ea-prompt-input", rows: 2, value: this.current.text });
    this.promptLabel = element("label", { className: "ea-prompt-label", htmlFor: this.textarea.id });
    this.toolbar = element("div", { className: "ea-prompt-tools", role: "group" });
    this.heading = element("h3", { id: "easyapi-prompt-title-" + id });
    this.toolActions = element("div", { className: "ea-prompt-tool-actions", id: "easyapi-prompt-actions-" + id });
    this.launcher = button("", () => this.toggleTools(), "ea-prompt-tool ea-prompt-launcher");
    this.launcher.dataset.tool = "launcher";
    this.launcher.setAttribute("aria-controls", this.toolActions.id);
    this.toolbar.append(this.launcher, element("div", { className: "ea-prompt-tool-rail" }, this.toolActions));
    this.body = element("div", { className: "ea-prompt-popover-body" });
    this.closeButton = button("×", () => this.close(true), "ea-prompt-tool ea-prompt-close");
    this.popupHeading = element("div", { className: "ea-prompt-popover-heading" }, this.heading, this.closeButton);
    this.popup = element("section", { id: "easyapi-prompt-tools-" + id, className: "easyapi-panel ea-prompt-popover", hidden: true, role: "dialog" },
      this.popupHeading, this.body);
    this.popupHeading.addEventListener("pointerdown", (event) => this.startWanDrag(event));
    this.popupHeading.addEventListener("lostpointercapture", (event) => this.stopWanDrag(event));
    this.popup.setAttribute("aria-labelledby", this.heading.id);
    this.nativePopover = typeof this.popup.showPopover === "function";
    if (this.nativePopover) {
      this.popup.setAttribute("popover", "auto");
      this.popup.addEventListener("toggle", (event) => {
        if (event.newState === "closed" && !this.popup.matches(":popover-open")) this.close();
      });
    }
    this.buttons = {};
    for (const [tool] of TOOLS) {
      const action = button("", () => {
        if (tool === "undo" || tool === "redo") this.step(tool);
        else if (this.mode === tool) this.close(true);
        else this.open(tool);
      }, "ea-prompt-tool");
      action.dataset.tool = tool;
      if (tool !== "undo" && tool !== "redo") {
        action.setAttribute("aria-controls", this.popup.id);
        action.setAttribute("aria-haspopup", "dialog");
      }
      this.buttons[tool] = action;
      this.toolActions.append(action);
    }
    this.setToolsExpanded(false);
    this.root.append(this.promptLabel, this.textarea, this.toolbar, this.popup);
    this.toolbar.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "touch" || this.linked) return;
      this.toolsHovered = true;
      this.setToolsExpanded(true);
    });
    this.toolbar.addEventListener("pointerleave", () => {
      this.toolsHovered = false;
      this.scheduleToolCollapse();
    });
    this.toolbar.addEventListener("focusin", () => { if (!this.linked) this.setToolsExpanded(true); });
    this.toolbar.addEventListener("focusout", (event) => {
      if (!this.toolbar.contains(event.relatedTarget)) this.scheduleToolCollapse();
    });
    this.toolbar.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.isComposing || this.toolbar.dataset.expanded !== "true") return;
      event.preventDefault();
      event.stopPropagation();
      if (this.mode) this.close(true);
      else {
        this.toolsPinned = false;
        this.syncOutsideListener();
        this.launcher.focus({ preventScroll: true });
        this.setToolsExpanded(false);
      }
    });
    this.textarea.addEventListener("beforeinput", (event) => {
      if (event.isComposing) return;
      const direction = { historyUndo: "undo", historyRedo: "redo" }[event.inputType];
      if (direction && event.cancelable) {
        event.preventDefault();
        this.step(direction);
      } else if (this.textarea.value === this.current.text) {
        this.current = this.captureSelection();
      }
    });
    this.textarea.addEventListener("input", (event) => this.type(event));
    this.textarea.addEventListener("compositionend", () => this.scheduleTyping());
    this.textarea.addEventListener("blur", () => this.finishTyping());
    this.textarea.addEventListener("keydown", (event) => this.onShortcut(event));
    this.popup.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.isComposing) {
        event.preventDefault();
        event.stopPropagation();
        this.close(true);
      }
    });
    this.dismissOutside = (event) => {
      if (this.toolbar.contains(event.target) || this.popup.contains(event.target)) return;
      this.toolsPinned = false;
      if (!this.root.contains(event.target)) this.close();
      this.syncOutsideListener();
      this.scheduleToolCollapse();
    };
    this.onResize = () => this.position();
    this.onScroll = (event) => {
      if (!event.target?.nodeType || !this.popup.contains(event.target)) this.position();
    };
    this.onWanDragMove = (event) => {
      const drag = this.wanDrag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      event.preventDefault(); event.stopPropagation();
      this.wanPosition = { left: drag.left + event.clientX - drag.x, top: drag.top + event.clientY - drag.y };
      this.position();
    };
    this.onWanDragEnd = (event) => this.stopWanDrag(event);
    this.translate();
  }

  translate() {
    this.promptLabel.textContent = this.t("prompt");
    this.textarea.setAttribute("aria-label", this.t("prompt"));
    this.toolbar.setAttribute("aria-label", this.t("promptTools"));
    this.launcher.title = this.t("promptTools");
    this.launcher.setAttribute("aria-label", this.launcher.title);
    this.closeButton.title = this.t("promptClose");
    this.closeButton.setAttribute("aria-label", this.closeButton.title);
    for (const [tool, label] of TOOLS) {
      this.buttons[tool].title = this.t(label);
      this.buttons[tool].setAttribute("aria-label", this.t(label));
    }
    if (this.mode) {
      const mode = this.mode;
      this.close();
      this.open(mode);
    }
    this.syncButtons();
  }

  captureSelection() {
    const { value, selectionStart, selectionEnd } = this.textarea;
    return snapshot(value, selectionStart ?? value.length, selectionEnd ?? value.length);
  }

  setValue(value, { reset = false } = {}) {
    const text = String(value ?? "");
    if (!reset && text === this.current.text) return;
    if (reset) {
      clearTimeout(this.typingTimer);
      this.typing = "";
      this.history = [];
      this.storageKey = undefined;
      this.close();
    } else this.finishTyping();
    this.undoStack = [];
    this.redoStack = [];
    this.applySnapshot(snapshot(text), false);
  }

  setLinked(linked) {
    this.linked = linked;
    this.textarea.disabled = linked;
    if (linked) {
      this.toolsHovered = this.toolsPinned = false;
      this.close();
      this.syncOutsideListener();
      this.setToolsExpanded(false);
    }
    this.syncButtons();
  }

  syncButtons() {
    this.launcher.disabled = this.linked;
    this.buttons.undo.disabled = this.linked || !this.undoStack.length;
    this.buttons.redo.disabled = this.linked || !this.redoStack.length;
    this.buttons.history.disabled = this.linked;
    this.buttons.tags.disabled = this.linked;
    this.buttons.optimize.disabled = this.linked || !this.current.text.trim();
    this.buttons.translate.disabled = this.buttons.optimize.disabled;
    const context = this.getContext();
    this.buttons.wan.hidden = !/(?:^|[\/_:-])wan3(?:[.\-_]|$)/i.test(context.targetModel || context.modelId || "");
    this.buttons.wan.disabled = this.buttons.optimize.disabled;
    for (const tool of ["history", "tags", "translate", "optimize", "wan"]) {
      this.buttons[tool].setAttribute("aria-expanded", String(this.mode === tool));
    }
  }

  setToolsExpanded(expanded) {
    clearTimeout(this.toolCollapseTimer);
    const state = String(expanded);
    if (this.toolbar.dataset.expanded === state) return;
    this.toolbar.dataset.expanded = state;
    this.launcher.setAttribute("aria-expanded", state);
    this.toolActions.inert = !expanded;
    this.toolActions.setAttribute("aria-hidden", String(!expanded));
    for (const action of Object.values(this.buttons)) action.tabIndex = expanded ? 0 : -1;
  }

  scheduleToolCollapse() {
    clearTimeout(this.toolCollapseTimer);
    this.toolCollapseTimer = setTimeout(() => {
      const target = document.activeElement;
      const focused = this.toolbar.contains(target) && (target.matches?.(":focus-visible") ?? true);
      if (!this.toolsHovered && !this.toolsPinned && !this.mode && !focused) this.setToolsExpanded(false);
    }, TOOL_COLLAPSE_DELAY);
  }

  toggleTools() {
    if (this.linked) return;
    // Pointer focus expands the rail before click. Pinning keeps that first click open.
    this.toolsPinned = !this.toolsPinned;
    if (!this.toolsPinned && this.mode) this.close();
    this.setToolsExpanded(this.toolsPinned);
    this.syncOutsideListener();
  }

  syncOutsideListener() {
    if (this.mode || this.toolsPinned) document.addEventListener("pointerdown", this.dismissOutside, true);
    else document.removeEventListener("pointerdown", this.dismissOutside, true);
  }

  rememberUndo(value) {
    this.undoStack.push(value);
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
  }

  type(event) {
    const next = this.captureSelection();
    if (next.text === this.current.text) return;
    const inputType = event.inputType;
    const typing = event.isComposing || inputType === "insertCompositionText" ? "composition" : inputType || "input";
    const continuous = ["insertText", "deleteContentBackward", "deleteContentForward", "composition"].includes(typing);
    if (!continuous || typing !== this.typing) {
      this.finishTyping();
      this.rememberUndo(this.current);
      this.redoStack = [];
      this.typing = typing;
    }
    this.current = next;
    this.notifyChange();
    if (!event.isComposing) {
      if (continuous) this.scheduleTyping();
      else this.finishTyping();
    }
  }

  scheduleTyping() {
    clearTimeout(this.typingTimer);
    if (this.typing) this.typingTimer = setTimeout(() => this.finishTyping(), TYPING_DELAY);
  }

  finishTyping() {
    clearTimeout(this.typingTimer);
    if (!this.typing) return;
    this.typing = "";
    this.record([this.current.text, this.undoStack.at(-1)?.text]);
  }

  notifyChange() {
    ++this.revision;
    this.wanPanel?.invalidate();
    if (this.result && !this.result.element.hidden) this.invalidateResult();
    this.syncButtons();
    this.onInput(this.current.text);
  }

  applySnapshot(next, notify = true) {
    this.current = next;
    this.textarea.value = next.text;
    this.textarea.setSelectionRange?.(next.start, next.end);
    if (notify) this.notifyChange();
    else {
      ++this.revision;
      this.wanPanel?.invalidate();
      if (this.result && !this.result.element.hidden) this.invalidateResult();
      this.syncButtons();
    }
  }

  replacePrompt(text, caret = text.length) {
    this.finishTyping();
    this.current = this.captureSelection();
    if (text === this.current.text) return;
    this.rememberUndo(this.current);
    this.redoStack = [];
    this.record([text, this.current.text]);
    this.applySnapshot(snapshot(text, caret));
  }

  step(direction) {
    this.finishTyping();
    const undoing = direction === "undo";
    const source = undoing ? this.undoStack : this.redoStack;
    if (!source.length) return;
    const current = this.captureSelection();
    if (undoing) this.redoStack.push(current);
    else this.rememberUndo(current);
    this.applySnapshot(source.pop());
    this.textarea.focus({ preventScroll: true });
  }

  onShortcut(event) {
    if (event.isComposing || !(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key !== "z" && key !== "y") return;
    event.preventDefault();
    event.stopPropagation();
    this.step(key === "y" || event.shiftKey ? "redo" : "undo");
  }

  loadHistory(create = false) {
    const key = this.historyKey?.(create);
    if (!key || key === this.storageKey) return;
    this.storageKey = key;
    try {
      const saved = JSON.parse(localStorage.getItem("easyapi.promptHistory.v1." + key) || "[]");
      if (Array.isArray(saved)) this.history = [...new Set([...this.history, ...saved.filter((item) =>
        typeof item === "string" && item && item.length <= HISTORY_TEXT_LIMIT)])].slice(0, HISTORY_LIMIT);
    } catch { /* History can remain available for this session when storage is disabled. */ }
  }

  record(entries) {
    const available = entries.filter((item) => item?.trim() && item.length <= HISTORY_TEXT_LIMIT);
    if (!available.length) return;
    this.loadHistory(true);
    this.history = [...new Set([...available, ...this.history])].slice(0, HISTORY_LIMIT);
    this.saveHistory();
  }

  saveHistory() {
    if (!this.storageKey) return;
    try {
      const key = "easyapi.promptHistory.v1." + this.storageKey;
      if (this.history.length) localStorage.setItem(key, JSON.stringify(this.history));
      else localStorage.removeItem(key);
    } catch { /* Quota and privacy mode can prevent browser storage. */ }
  }

  open(mode) {
    if (this.linked || this.buttons[mode].disabled) return;
    this.finishTyping();
    this.close();
    this.mode = mode;
    this.setToolsExpanded(true);
    this.heading.textContent = this.t(TOOLS.find(([tool]) => tool === mode)[1]);
    if (mode === "tags") this.renderTags();
    else if (mode === "history") this.renderHistory();
    else if (mode === "wan") this.renderWan();
    else this.renderTransform(mode);
    this.popup.classList?.toggle("ea-wan-popover", mode === "wan");
    this.popup.hidden = false;
    if (this.nativePopover) this.popup.showPopover();
    else document.body.append(this.popup);
    this.syncOutsideListener();
    window.addEventListener("resize", this.onResize);
    window.addEventListener("scroll", this.onScroll, true);
    this.position();
    if (mode === "wan" && typeof ResizeObserver !== "undefined") {
      this.popupObserver ||= new ResizeObserver(() => { if (this.mode === "wan") this.position(); });
      this.popupObserver.observe(this.popup);
    }
    this.syncButtons();
    (mode === "tags" ? this.tagSearch : this.closeButton).focus({ preventScroll: true });
  }

  position() {
    const anchor = this.root.getBoundingClientRect();
    const margin = 8;
    if (this.mode === "wan") {
      this.popup.style.height = "auto";
      this.popup.style.maxHeight = Math.max(0, Math.min(760, window.innerHeight - margin * 2)) + "px";
      const width = this.popup.offsetWidth || 0, height = this.popup.offsetHeight || 0;
      const clamp = (value, maximum) => Math.min(Math.max(margin, maximum), Math.max(margin, value));
      const left = clamp(this.wanPosition?.left ?? (window.innerWidth - width) / 2, window.innerWidth - width - margin);
      const top = clamp(this.wanPosition?.top ?? (window.innerHeight - height) / 2, window.innerHeight - height - margin);
      if (this.wanPosition) this.wanPosition = { left, top };
      this.popup.style.left = left + "px";
      this.popup.style.top = top + "px";
      this.popup.style.bottom = "auto";
      return;
    }
    this.popup.style.height = "auto";
    const top = Math.max(margin, anchor.top - 6);
    const bottom = Math.min(window.innerHeight - margin, anchor.bottom + 6);
    const below = window.innerHeight - margin - bottom;
    const above = top - margin;
    const openBelow = below >= 280 || below >= above;
    const left = Math.max(margin, Math.min(anchor.right - this.popup.offsetWidth,
      window.innerWidth - this.popup.offsetWidth - margin));
    this.popup.style.left = left + "px";
    this.popup.style.top = openBelow ? bottom + "px" : "auto";
    this.popup.style.bottom = openBelow ? "auto" : window.innerHeight - top + "px";
    this.popup.style.maxHeight = Math.min(400, openBelow ? below : above) + "px";
  }

  startWanDrag(event) {
    if (this.mode !== "wan" || event.button !== 0 || event.isPrimary === false ||
      event.target?.closest?.("button,input,select,textarea,a,[role=button]")) return;
    this.stopWanDrag();
    event.preventDefault(); event.stopPropagation();
    const rect = this.popup.getBoundingClientRect();
    this.wanDrag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    this.wanPosition = { left: rect.left, top: rect.top };
    this.popup.dataset.dragging = "true";
    window.addEventListener("pointermove", this.onWanDragMove);
    window.addEventListener("pointerup", this.onWanDragEnd);
    window.addEventListener("pointercancel", this.onWanDragEnd);
    try { this.popupHeading.setPointerCapture?.(event.pointerId); } catch { /* Window listeners also track the drag. */ }
  }

  stopWanDrag(event) {
    const drag = this.wanDrag;
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    this.wanDrag = null;
    delete this.popup.dataset.dragging;
    window.removeEventListener("pointermove", this.onWanDragMove);
    window.removeEventListener("pointerup", this.onWanDragEnd);
    window.removeEventListener("pointercancel", this.onWanDragEnd);
    try { this.popupHeading.releasePointerCapture?.(drag.pointerId); } catch { /* Capture may already have ended. */ }
  }

  close(restoreFocus = false) {
    if (!this.mode) return;
    this.stopWanDrag();
    this.wanPosition = null;
    this.popupObserver?.disconnect();
    const trigger = this.buttons[this.mode];
    this.mode = null;
    this.modelRequest?.abort();
    this.transformRequest?.abort();
    this.wanPanel?.dispose();
    this.wanPanel = null;
    this.wanOpenToken = (this.wanOpenToken || 0) + 1;
    this.modelRequest = this.transformRequest = null;
    this.result = null;
    this.checkTransformScope = null;
    window.removeEventListener("resize", this.onResize);
    window.removeEventListener("scroll", this.onScroll, true);
    if (this.nativePopover) {
      if (this.popup.matches(":popover-open")) this.popup.hidePopover();
    } else this.root.append(this.popup);
    this.popup.hidden = true;
    this.syncButtons();
    this.syncOutsideListener();
    if (restoreFocus) trigger.focus({ preventScroll: true });
    this.scheduleToolCollapse();
  }

  renderHistory() {
    this.loadHistory();
    const history = element("div", { className: "ea-prompt-history" });
    const clear = button(this.t("promptHistoryClear"), () => {
      this.history = [];
      this.saveHistory();
      this.renderHistory();
      this.closeButton.focus({ preventScroll: true });
    });
    for (const text of this.history) {
      history.append(button(text, () => {
        this.replacePrompt(text);
        this.close();
        this.textarea.focus({ preventScroll: true });
      }, "ea-prompt-history-entry"));
    }
    if (this.history.length) this.body.replaceChildren(history, element("div", { className: "ea-prompt-actions" }, clear));
    else this.body.replaceChildren(element("p", { className: "ea-prompt-status", textContent: this.t("promptHistoryEmpty") }));
  }

  renderTags() {
    const searchLabel = this.t("promptTagSearch");
    this.tagSearch = element("input", { type: "search", className: "ea-prompt-tag-search", placeholder: searchLabel });
    this.tagSearch.setAttribute("aria-label", searchLabel);
    const library = element("div", { className: "ea-prompt-tags" });
    const locale = this.getLocale() || "";
    const languageIndex = /^zh(?:-|$)/i.test(locale) ? (/^(zh-TW|zh-Hant)$/i.test(locale) ? 2 : 1) : 0;
    const filter = () => {
      const term = this.tagSearch.value.trim().toLowerCase();
      const groups = [];
      for (const [key, tags] of Object.entries(TAGS)) {
        const title = this.t(key);
        const matches = tags.filter((tag) => [title, ...tag].some((text) => text.toLowerCase().includes(term)));
        if (!matches.length) continue;
        const group = element("section", { className: "ea-prompt-tag-group" }, element("h4", { textContent: title }));
        const choices = element("div", { className: "ea-prompt-tag-options" });
        for (const tag of matches) {
          const add = button(tag[languageIndex], () => this.insertTag(tag[0]), "ea-prompt-tag");
          add.title = tag[0];
          choices.append(add);
        }
        group.append(choices); groups.push(group);
      }
      library.replaceChildren(...(groups.length ? groups : [
        element("p", { className: "ea-prompt-status", textContent: this.t("promptTagEmpty") }),
      ]));
    };
    this.tagSearch.addEventListener("input", filter);
    this.body.replaceChildren(this.tagSearch, library);
    filter();
  }

  insertTag(tag) {
    const { text, start, end } = this.captureSelection();
    const before = text.slice(0, start);
    const after = text.slice(end);
    const prefix = !before || /\s$/.test(before) ? "" : /[,，;；]$/.test(before) ? " " : ", ";
    const suffix = !after || /^[\s,，;；]/.test(after) ? "" : ", ";
    const inserted = before + prefix + tag;
    this.replacePrompt(inserted + suffix + after, inserted.length);
  }

  renderTransform(mode) {
    const { profiles = [], profileId, modelId } = this.getContext();
    const connections = profiles.filter((entry) => entry.id);
    const profile = element("select");
    const model = element("select", { disabled: true });
    const language = element("select", {}, ...LANGUAGES.map(([code, label]) => option(code, this.t(label))));
    const defaultLanguage = /\p{Script=Han}/u.test(this.current.text) ? "en" :
      /^(zh-TW|zh-Hant)$/i.test(this.getLocale()) ? "zh-TW" : "zh";
    language.value = this.language || defaultLanguage;
    const status = element("p", { className: "ea-prompt-status", role: "status" });
    const catalogStatus = element("span", { className: "ea-caption", role: "status" });
    const retry = button(this.t("retryModels"), () => refreshModels(true));
    retry.hidden = true;
    const resultText = element("textarea", { rows: 3, readOnly: true });
    const useResult = button(this.t("promptUseResult"), () => {
      if (this.result?.revision !== this.revision) { this.invalidateResult(); return; }
      const result = resultText.value;
      this.close();
      this.replacePrompt(result);
      this.textarea.focus({ preventScroll: true });
    });
    const result = element("div", { className: "ea-prompt-result", hidden: true },
      field(this.t("promptTransformResult"), resultText), element("div", { className: "ea-prompt-actions" }, useResult));
    this.result = { element: result, status, revision: null };
    const label = this.t(mode === "optimize" ? "promptOptimize" : "promptTranslate");
    const run = button(label, () => runTransform(), "ea-primary");
    run.disabled = true;
    const controls = [field(this.t("promptTransformModel"), model)];
    if (connections.length !== 1) controls.unshift(field(this.t("connection"), profile));
    if (mode === "translate") controls.push(field(this.t("promptTranslationLanguage"), language));
    this.body.replaceChildren(element("div", { className: "ea-prompt-form" }, ...controls),
      element("p", { className: "ea-caption", textContent: this.t("promptTransformCost") }),
      element("div", { className: "ea-model-updates" }, catalogStatus, retry),
      status, element("div", { className: "ea-prompt-actions" }, run), result);
    profile.replaceChildren(option("", this.t("chooseConnection"), true),
      ...connections.map((entry) => option(entry.id, entry.name || entry.id)));
    profile.value = connections.some((entry) => entry.id === profileId) ? profileId : connections.length === 1 ? connections[0].id : "";
    profile.disabled = !connections.length;

    let availableModels = [], loadedScopeKey;
    const syncControls = () => {
      const running = Boolean(this.transformRequest);
      profile.disabled = running || !connections.length;
      language.disabled = running;
      model.disabled = running || !availableModels.length;
      run.disabled = running || !model.value || !this.current.text.trim();
    };
    const scopeKey = () => JSON.stringify([profile.value,
      this.getContext().profiles?.find((row) => row.id === profile.value)?.model_catalog_revision, this.getLocale()]);
    const clearResult = () => { result.hidden = true; status.textContent = ""; };
    const refreshModels = async (refresh = false) => {
      this.modelRequest?.abort();
      this.transformRequest?.abort(); this.transformRequest = null;
      const request = new AbortController();
      this.modelRequest = request;
      clearResult();
      model.replaceChildren(option("", this.t("chooseModel"), true));
      model.value = "";
      model.disabled = run.disabled = true;
      const id = profile.value;
      loadedScopeKey = scopeKey(); availableModels = [];
      syncControls();
      if (!id) { catalogStatus.textContent = this.t("profileNeeded"); return; }
      catalogStatus.textContent = this.t("loadingModels"); retry.hidden = true;
      let applied;
      const applyModels = (models) => {
        if (request.signal.aborted || applied === models) return;
        const replacing = applied !== undefined, previous = model.value;
        const choices = models.filter((item) => item.id);
        availableModels = choices;
        applied = models;
        const preferred = replacing ? previous : this.preferredModels.get(id) || (id === profileId ? modelId : "");
        model.replaceChildren(option("", this.t("chooseModel"), true), ...choices.map((item) => option(item.id,
          item.display_name && item.display_name !== item.id ? item.display_name + " · " + item.id : item.id)));
        model.value = choices.some((item) => item.id === preferred) ? preferred : !replacing && choices.length === 1 ? choices[0].id : "";
        if (replacing && previous !== model.value) { this.transformRequest?.abort(); this.transformRequest = null; clearResult(); }
        syncControls();
        catalogStatus.textContent = choices.length ? "" : this.t("noModels");
      };
      const update = (state) => {
        if (request.signal.aborted) return;
        if (state.invalidated) {
          request.abort();
          this.transformRequest?.abort(); this.transformRequest = null; clearResult(); applied = undefined;
          availableModels = [];
          model.replaceChildren(option("", this.t("chooseModel"), true)); model.value = "";
          syncControls();
        } else if (state.models) applyModels(state.models);
        retry.hidden = !state.error && !state.invalidated;
        if (state.error) catalogStatus.textContent = state.invalidated ? state.error.message : this.t("modelUpdateFailed");
        else if (state.updating) catalogStatus.textContent = this.t(state.value ? "updatingModels" : "loadingModels");
      };
      try {
        applyModels(await this.loadModels(id, request.signal, update, { profiles: this.getContext().profiles, refresh }));
      } catch (error) {
        if (!request.signal.aborted) { catalogStatus.textContent = error.message || this.t("promptTransformFailed"); retry.hidden = false; }
      }
    };
    const runTransform = async () => {
      const text = this.current.text;
      if (run.disabled || !text.trim() || !model.value || !profile.value) return;
      this.transformRequest?.abort();
      const request = new AbortController();
      this.transformRequest = request;
      const revision = this.revision;
      const payload = { action: mode, prompt: text, profile_id: profile.value, model: model.value };
      if (mode === "translate") payload.target_language = language.value;
      this.preferredModels.set(profile.value, model.value);
      clearResult();
      run.disabled = profile.disabled = model.disabled = language.disabled = true;
      status.textContent = this.t("promptTransformRunning");
      try {
        const response = await this.transform(payload, request.signal);
        if (request.signal.aborted) return;
        if (revision !== this.revision) { this.invalidateResult(); return; }
        if (typeof response?.text !== "string" || !response.text.trim()) throw new Error(this.t("promptTransformFailed"));
        resultText.value = response.text;
        this.result.revision = revision;
        result.hidden = false;
        status.textContent = "";
      } catch (error) {
        if (!request.signal.aborted) status.textContent = error.message || this.t("promptTransformFailed");
      } finally {
        if (this.transformRequest === request) this.transformRequest = null;
        if (this.mode === mode) syncControls();
      }
    };
    profile.addEventListener("change", () => refreshModels());
    model.addEventListener("change", () => {
      this.preferredModels.set(profile.value, model.value);
      clearResult();
      run.disabled = !model.value || !this.current.text.trim();
    });
    language.addEventListener("change", () => { this.language = language.value; clearResult(); });
    this.checkTransformScope = () => { if (scopeKey() !== loadedScopeKey) refreshModels(); };
    refreshModels();
  }

  async renderWan() {
    const token = this.wanOpenToken = (this.wanOpenToken || 0) + 1;
    this.body.replaceChildren(element("p", { className: "ea-prompt-status", textContent: this.t("wanPreparing") }));
    try {
      const { WanTunerPanel } = await this.loadWanPanel();
      if (this.mode !== "wan" || this.linked || token !== this.wanOpenToken) return;
      this.wanPanel = new WanTunerPanel({
        t: this.t, loadModels: this.loadModels, transform: this.transform, resolveMedia: this.resolveMedia,
        getContext: () => ({ ...this.getContext(), prompt: this.current.text, locale: this.getLocale() }),
        preferences: this.wanPreferences,
        onPreferences: next => { this.wanPreferences = next; },
        onApply: prompt => {
          if (this.linked) return;
          this.close();
          this.replacePrompt(prompt);
          this.textarea.focus({ preventScroll: true });
        },
      });
      this.body.replaceChildren(this.wanPanel.root);
      this.position();
    } catch (error) {
      if (this.mode === "wan") this.body.replaceChildren(element("p", { className: "ea-prompt-status", textContent: error.message }));
    }
  }

  contextChanged() {
    this.syncButtons();
    this.checkTransformScope?.();
    this.wanPanel?.checkContext();
  }

  invalidateResult() {
    if (!this.result) return;
    this.result.element.hidden = true;
    this.result.status.textContent = this.t("promptChanged");
  }

  dispose() {
    this.finishTyping();
    this.toolsPinned = this.toolsHovered = false;
    this.close();
    this.syncOutsideListener();
    this.setToolsExpanded(false);
  }
}
