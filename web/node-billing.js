const PARAMS = ["duration", "resolution", "size", "n", "generate_audio", "aspect_ratio"];
const RATE_LABEL = {
  prompt: "billingInputTokens", completion: "billingOutputTokens", input_cache_read: "billingCacheRead",
  input_cache_write: "billingCacheWrite", request: "billingPerRequest", second: "billingPerSecond",
  image: "billingImageInput", audio: "billingAudioInput", audio_completion: "billingAudioOutput",
  image_input: "billingInputImage", image_output: "billingOutputImage", image_output_2k: "billingOutputImage2K",
};
const TOKEN_RATES = new Set(["prompt", "completion", "input_cache_read", "input_cache_write", "image", "audio", "audio_completion"]);
const TEXT_OPERATIONS = new Set(["chat", "responses", "responses-compact", "messages", "completions", "gemini-generate", "gemini-stream"]);
const REASONS = { missing_usage: "billingMissingUsage", unsupported_operation: "billingUnknownUsage", model_unavailable: "billingModelUnavailable" };
let nextPopoverId = 0;

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

function number(value) { return Number.isFinite(value) && value >= 0; }

function money(amount, currency, locale) {
  const options = { maximumFractionDigits: 6 };
  if (/^[A-Z]{3}$/.test(currency)) {
    return new Intl.NumberFormat(locale, { ...options, style: "currency", currency }).format(amount);
  }
  return `${new Intl.NumberFormat(locale, options).format(amount)} ${currency}`;
}

function mediaFacts(inputs, params, operation) {
  const linked = new Set(inputs.filter(({ link }) => link != null).map(({ name }) => name));
  const kinds = { image: /^image$|^(first|last)_frame$|^reference_image_/, video: /^video$|^reference_video_/, audio: /^audio$|^reference_audio_/ };
  const linkedKinds = Object.fromEntries(Object.entries(kinds).map(([kind, pattern]) => [kind, [...linked].some((name) => pattern.test(name))]));
  // Unified video execution replaces inline references when media comes from graph links.
  const inline = operation === "video-generation" && (linked.has("references") || Object.values(linkedKinds).some(Boolean)) ? {} : params;
  const references = inline.input_references;
  const imageOnly = ["image-generation", "image-edit"].includes(operation);
  const referenceList = references == null ? [] : Array.isArray(references) ? references : imageOnly ? [references] : [];
  const referenceTypes = referenceList.map((reference) => reference?.kind ?? reference?.type ??
    (typeof reference?.role === "string" ? reference.role.split("_").at(-1) :
      imageOnly && (typeof reference === "string" ? reference.trim() : reference?.url || reference?.path || reference?.image_url) ? "image" : undefined));
  const grouped = linked.has("references") || (references != null && !Array.isArray(references) && !imageOnly) ||
    referenceTypes.some((type) => !["image", "video", "audio"].includes(type));
  const result = {};
  for (const [kind, connected] of Object.entries(linkedKinds)) {
    const legacy = kind === "image" ? inline.input_reference : undefined;
    const parameter = referenceTypes.length ? undefined : inline[kind] ?? inline[`${kind}_url`] ?? inline[`${kind}s`] ?? legacy;
    const supplied = Array.isArray(parameter) ? parameter.length > 0 : Boolean(parameter);
    const present = connected || supplied || referenceTypes.includes(kind);
    if (present || !grouped) result[`has_${kind}_input`] = present;
    // A connected image port can carry a batch and a generic group can contain several media types.
    if (kind === "image" && !connected && !grouped) {
      result.input_image_count = referenceTypes.filter((type) => type === "image").length + (Array.isArray(parameter) ? parameter.length : Number(supplied));
    }
  }
  return result;
}

// Only send metering facts to the pricing endpoint. Text and media are never part of a quote.
export function estimateFacts({ model, operation, params, schema, inputs, prompt, paramsLinked }) {
  const facts = { model, operation };
  if (paramsLinked) return facts;
  const get = (key) => Object.hasOwn(params, key) ? params[key] : schema?.properties?.[key]?.default;
  for (const key of PARAMS) {
    const value = get(key);
    if (number(value) || key === "duration" && value === -1 || typeof value === "boolean" || typeof value === "string") facts[key] = value;
  }
  if (!facts.size && number(get("width")) && number(get("height"))) facts.size = `${get("width")}x${get("height")}`;
  Object.assign(facts, mediaFacts(inputs, params, operation));
  if (operation === "audio-speech" && typeof prompt === "string") facts.text_characters = Array.from(prompt).length;
  if (TEXT_OPERATIONS.has(operation) && typeof prompt === "string" && !params.messages && !params.contents && !params.input &&
      facts.has_image_input === false && facts.has_video_input === false && facts.has_audio_input === false) {
    let ascii = 0, other = 0;
    for (const char of prompt) {
      if (char.codePointAt(0) < 128) ascii++;
      else other++;
    }
    facts.input_tokens = Math.ceil(ascii / 4) + other;
    const maxOutput = get("max_output_tokens") ?? get("max_tokens") ?? get("maxOutputTokens");
    if (number(maxOutput)) facts.output_tokens = maxOutput;
  }
  return facts;
}

export class NodeBilling {
  constructor({ request, t, locale }) {
    Object.assign(this, { request, t, locale });
    this.id = "";
    this.total = element("span", { className: "ea-billing-value", role: "status" });
    const icon = element("span", { className: "ea-billing-icon", textContent: "ⓘ" });
    icon.setAttribute("aria-hidden", "true");
    this.trigger = element("button", { type: "button", className: "ea-billing-trigger" }, icon, this.total);
    this.details = element("section", { id: `easyapi-price-${++nextPopoverId}`, className: "easyapi-panel ea-billing-details", hidden: true });
    this.root = element("div", { className: "ea-node-billing", hidden: true }, this.trigger, this.details);
    this.trigger.setAttribute("aria-controls", this.details.id);
    this.trigger.setAttribute("aria-expanded", "false");
    this.trigger.addEventListener("click", () => this.open ? this.close() : this.show());
    this.nativePopover = typeof this.details.showPopover === "function";
    if (this.nativePopover) {
      this.details.setAttribute("popover", "auto");
      this.details.addEventListener("toggle", (event) => {
        if (event.newState === "closed" && !this.details.matches(":popover-open")) this.close();
      });
    }
    this.closeFromOutside = (event) => {
      if (!this.trigger.contains(event.target) && !this.details.contains(event.target)) this.close();
    };
    this.closeFromKeyboard = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      this.close();
      this.trigger.focus();
    };
    this.reposition = () => this.position();
    this.render();
  }

  show() {
    if (this.query && !this.loading && Date.now() - this.loadedAt >= 30_000) this.update(this.query, this.revision, true);
    this.open = true;
    this.trigger.setAttribute("aria-expanded", "true");
    this.details.hidden = false;
    if (this.nativePopover) this.details.showPopover();
    else document.body.append(this.details);
    window.addEventListener("resize", this.reposition);
    window.addEventListener("scroll", this.reposition, true);
    document.addEventListener("pointerdown", this.closeFromOutside, true);
    document.addEventListener("keydown", this.closeFromKeyboard, true);
    this.position();
  }

  position() {
    const anchor = this.trigger.getBoundingClientRect();
    const gap = 8;
    this.details.style.left = `${Math.max(gap, Math.min(anchor.left, window.innerWidth - this.details.offsetWidth - gap))}px`;
    const below = window.innerHeight - anchor.bottom - 2 * gap;
    const above = anchor.top - 2 * gap;
    const aboveAnchor = below < this.details.offsetHeight && above > below;
    this.details.style.top = aboveAnchor ? "auto" : `${anchor.bottom + gap}px`;
    this.details.style.bottom = aboveAnchor ? `${window.innerHeight - anchor.top + gap}px` : "auto";
    this.details.style.maxHeight = `${Math.max(gap, aboveAnchor ? above : below)}px`;
  }

  close() {
    if (!this.open) return;
    this.open = false;
    window.removeEventListener("resize", this.reposition);
    window.removeEventListener("scroll", this.reposition, true);
    document.removeEventListener("pointerdown", this.closeFromOutside, true);
    document.removeEventListener("keydown", this.closeFromKeyboard, true);
    this.trigger.setAttribute("aria-expanded", "false");
    if (this.nativePopover && this.details.matches(":popover-open")) this.details.hidePopover();
    if (!this.nativePopover) this.root.append(this.details);
    this.details.hidden = true;
  }

  update(query, revision = "", force = false) {
    const id = query ? JSON.stringify([query, revision]) : "";
    if (id === this.id && !force) return;
    this.id = id;
    this.query = query;
    this.revision = revision;
    clearTimeout(this.timer);
    this.pending?.abort();
    this.root.hidden = !id;
    this.result = null;
    this.loading = Boolean(id);
    this.render();
    if (query) this.timer = setTimeout(() => this.load(query, id), 280);
    else this.close();
  }

  async load(query, id) {
    const controller = new AbortController();
    this.pending = controller;
    let result;
    try {
      result = await this.request(query, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) return;
      result = { reason: "quote_unavailable" };
    }
    if (controller.signal.aborted || this.id !== id) return;
    this.result = result;
    this.loadedAt = Date.now();
    this.loading = false;
    this.render();
  }

  render() {
    const locale = this.locale();
    const { estimate, pricing, reason } = this.result || {};
    const format = (amount, currency) => money(amount, currency, locale);
    this.total.textContent = estimate ? `≈ ${format(estimate.amount, estimate.currency)}` : "";
    this.trigger.title = this.t("billingDetails");
    this.trigger.setAttribute("aria-label", this.total.textContent ? `${this.t("billingEstimate")} ${this.total.textContent}` : this.t("billingDetails"));
    const rows = [];
    const add = (label, value) => rows.push(element("dt", { textContent: this.t(label) }), element("dd", { textContent: value }));
    if (estimate && number(estimate.original_amount) && estimate.original_amount > estimate.amount) {
      add("billingOriginal", format(estimate.original_amount, estimate.currency));
    }
    if (pricing) {
      if (pricing.mode === "dynamic") add("billingMode", this.t(pricing.currency === "CNY" ? "billingDynamicCNY" : "billingDynamicUSD"));
      for (const [key, value] of Object.entries(pricing.rates || {})) {
        if (Object.hasOwn(RATE_LABEL, key) && number(value)) add(RATE_LABEL[key], format(value * (TOKEN_RATES.has(key) ? 1_000_000 : 1), pricing.currency));
      }
      if (pricing.offer?.name) add("billingOffer", pricing.offer.name);
      if (number(pricing.offer?.ends_at)) add("billingOfferEnd", new Date(pricing.offer.ends_at * 1000).toLocaleString(locale));
    }
    const discount = estimate?.discount ?? pricing?.discount;
    if (number(discount)) {
      const saving = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 2 }).format(1 - discount);
      const source = pricing?.basis === "public_catalog" && (estimate?.discount === undefined || estimate.basis === "public_catalog")
        ? "billingPublicDiscount" : "billingDiscount";
      add(source, discount < 1 ? this.t("billingDiscountValue", { discount: saving }) : this.t("billingNoDiscount"));
    }
    const note = estimate?.basis === "public_catalog" ? "billingPublicEstimateNote" :
      estimate?.basis === "authenticated_catalog" ? "billingCatalogEstimateNote" : "billingEstimateNote";
    const description = this.loading ? this.t("billingChecking") : estimate ? this.t(note) : this.t(REASONS[reason] || "billingQuoteUnavailable");
    const heading = `${this.t("billingEstimate")}${this.total.textContent ? " " + this.total.textContent : ""}`;
    this.details.replaceChildren(element("strong", { textContent: heading }),
      element("p", { className: "ea-caption", textContent: description }));
    if (pricing && Object.keys(pricing.rates || {}).length) {
      this.details.append(element("p", { className: "ea-caption", textContent: this.t(pricing.basis === "public_catalog" ? "billingPublicRates" : "billingCatalogRates") }));
    }
    if (rows.length) this.details.append(element("dl", {}, ...rows));
    if (this.open) this.position();
  }

  dispose() { clearTimeout(this.timer); this.pending?.abort(); this.close(); }
}
