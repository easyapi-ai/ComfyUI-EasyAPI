const PARAMS = ["duration", "resolution", "size", "n", "generate_audio", "aspect_ratio"];
const RATE_LABEL = {
  prompt: "billingInputTokens", completion: "billingOutputTokens", input_cache_read: "billingCacheRead",
  input_cache_write: "billingCacheWrite", request: "billingPerRequest", second: "billingPerSecond",
  image: "billingImageInput", audio: "billingAudioInput", audio_completion: "billingAudioOutput",
};
const TOKEN_RATES = new Set(["prompt", "completion", "input_cache_read", "input_cache_write", "image", "audio", "audio_completion"]);
const TEXT_OPERATIONS = new Set(["chat", "responses", "responses-compact", "messages", "completions", "gemini-generate", "gemini-stream"]);
const REASONS = { missing_usage: "billingMissingUsage", unsupported_operation: "billingUnknownUsage", model_unavailable: "billingModelUnavailable" };

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
  const referenceTypes = Array.isArray(references) ? references.map((reference) => reference?.type ??
    (typeof reference?.role === "string" ? reference.role.split("_").at(-1) : undefined)) : [];
  const grouped = linked.has("references") || (references != null && !Array.isArray(references)) ||
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
  constructor({ request, t, locale, resize }) {
    Object.assign(this, { request, t, locale, resize });
    this.id = "";
    this.label = element("span");
    this.total = element("span", { className: "ea-billing-value", role: "status" });
    this.details = element("div", { className: "ea-billing-details" });
    this.root = element("details", { className: "ea-node-billing", hidden: true },
      element("summary", {}, this.label, this.total), this.details);
    this.root.addEventListener("toggle", () => {
      if (this.root.open && this.query && !this.loading && Date.now() - this.loadedAt >= 30_000) {
        this.update(this.query, this.revision, true);
      }
      resize();
    });
    this.root.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.root.open) {
        this.root.open = false;
        this.root.firstElementChild.focus();
        event.stopPropagation();
      }
    });
    this.render();
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
    else this.root.open = false;
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
    this.label.textContent = this.t("billingEstimate");
    this.total.textContent = this.loading ? this.t("billingChecking") : estimate ? `≈ ${format(estimate.amount, estimate.currency)}` : "—";
    this.root.firstElementChild?.setAttribute("title", this.t("billingDetails"));
    const rows = [];
    const add = (label, value) => rows.push(element("dt", { textContent: this.t(label) }), element("dd", { textContent: value }));
    if (estimate && number(estimate.original_amount) && estimate.original_amount > estimate.amount) {
      add("billingOriginal", format(estimate.original_amount, estimate.currency));
    }
    if (pricing) {
      for (const [key, value] of Object.entries(pricing.rates || {})) {
        if (Object.hasOwn(RATE_LABEL, key) && number(value)) add(RATE_LABEL[key], format(value * (TOKEN_RATES.has(key) ? 1_000_000 : 1), pricing.currency));
      }
      if (pricing.offer?.name) add("billingOffer", pricing.offer.name);
      if (number(pricing.offer?.ends_at)) add("billingOfferEnd", new Date(pricing.offer.ends_at * 1000).toLocaleString(locale));
    }
    const discount = estimate?.discount ?? pricing?.discount;
    if (number(discount)) {
      const saving = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 2 }).format(1 - discount);
      add("billingDiscount", discount < 1 ? this.t("billingDiscountValue", { discount: saving }) : this.t("billingNoDiscount"));
    }
    this.details.replaceChildren(element("p", { className: "ea-caption", textContent: this.loading ? this.t("billingChecking") :
      estimate ? this.t("billingEstimateNote") : this.t(REASONS[reason] || "billingQuoteUnavailable") }));
    if (pricing && Object.keys(pricing.rates || {}).length) {
      this.details.append(element("p", { className: "ea-caption", textContent: this.t("billingCatalogRates") }));
    }
    if (rows.length) this.details.append(element("dl", {}, ...rows));
    if (this.root.open) this.resize();
  }

  dispose() { clearTimeout(this.timer); this.pending?.abort(); }
}
