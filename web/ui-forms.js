import { atPath, clone, writeAt } from "./ui-state.js";

const OMIT = new Set(["model", "prompt", "messages", "contents", "content", "input", "api_key", "authorization", "token", "input_reference", "input_references", "image", "images", "image_url", "audio", "audio_url", "video", "video_url", "mask", "file", "files", "base64Array"]);
const COMMON = ["stream", "size", "resolution", "aspect_ratio", "width", "height", "duration", "seconds", "fps", "seed", "n",
  "generate_audio", "return_last_frame", "watermark", "quality", "style", "negative_prompt", "voice", "instructions",
  "speed", "speech_rate", "language", "response_format", "output_format", "output_compression", "compression", "background",
  "temperature", "max_tokens", "max_output_tokens", "steps", "guidance_scale"];
const make = (tag, props = {}, ...children) => {
  const element = document.createElement(tag);
  Object.assign(element, props);
  for (const child of children.flat()) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return element;
};
const primitiveType = (definition) => Array.isArray(definition.type) ? definition.type.find((type) => type !== "null") : definition.type;
const optionText = (value) => typeof value === "string" ? value : JSON.stringify(value);
const initialItem = (definition) => definition.default !== undefined ? clone(definition.default) : ({ object: {}, array: [], boolean: false, integer: 0, number: 0 }[primitiveType(definition)] ?? "");
let nextFieldId = 0;

function imageSizeParts(value) {
  const match = typeof value === "string" && value.match(/^\s*(\d+)\s*[xX*×]\s*(\d+)\s*$/);
  if (!match) return null;
  const width = Number(match[1]), height = Number(match[2]);
  return Number.isSafeInteger(width) && Number.isSafeInteger(height) ? { width, height } : null;
}

export function validateImageSize(definition, value) {
  const invalid = (reason) => ({ ok: false, value, reason });
  const valid = (next) => ({ ok: true, value: next, reason: "" });
  if (typeof value !== "string") return invalid("invalid_size");
  const raw = value.trim(), dimensions = imageSizeParts(raw);
  const normalized = dimensions ? `${dimensions.width}x${dimensions.height}` : raw;
  const spec = definition["x-size-spec"] || {};
  for (const option of definition.enum || []) {
    if (typeof option !== "string") continue;
    const parts = imageSizeParts(option);
    const comparable = parts ? `${parts.width}x${parts.height}` : option.trim();
    if (normalized.toLowerCase() === comparable.toLowerCase()) return valid(comparable.toLowerCase() === "auto" ? "auto" : parts ? comparable : option);
  }
  if (raw.toLowerCase() === "auto") {
    return spec.allow_auto === true || String(definition.default).toLowerCase() === "auto" ? valid("auto") : invalid("auto_not_allowed");
  }
  if (spec.allow_custom !== true) return invalid("custom_not_allowed");
  if (!dimensions || dimensions.width <= 0 || dimensions.height <= 0 || !Number.isSafeInteger(dimensions.width * dimensions.height)) return invalid("invalid_size");
  const { width, height } = dimensions;
  for (const [name, actual, minimum] of [["min_width", width, true], ["max_width", width, false], ["min_height", height, true], ["max_height", height, false]]) {
    if (Number.isFinite(spec[name]) && (minimum ? actual < spec[name] : actual > spec[name])) return invalid(name);
  }
  if (Number.isSafeInteger(spec.multiple_of) && spec.multiple_of > 0 && (width % spec.multiple_of || height % spec.multiple_of)) return invalid("multiple_of");
  const pixels = width * height, ratio = width / height;
  for (const [name, actual, minimum] of [["min_pixels", pixels, true], ["max_pixels", pixels, false], ["min_ratio", ratio, true], ["max_ratio", ratio, false]]) {
    if (Number.isFinite(spec[name]) && (minimum ? actual < spec[name] : actual > spec[name])) return invalid(name);
  }
  return valid(normalized);
}

function stepButton(text, label, className = "ea-step") {
  const button = make("button", { type: "button", className, textContent: text, title: label });
  button.setAttribute("aria-label", label);
  return button;
}

function stepper(kind, input, label, t, minimum, maximum, getValue, middle = input) {
  const minus = stepButton("−", t("decreaseParameter", { name: label }));
  const plus = stepButton("+", t("increaseParameter", { name: label }));
  const element = make("div", { className: "ea-stepper ea-" + kind }, minus, middle, plus);
  const choose = (value) => {
    input.value = String(Math.min(maximum, Math.max(minimum, value)));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  minus.addEventListener("click", () => choose((getValue() ?? minimum) - 1));
  plus.addEventListener("click", () => {
    const value = getValue();
    choose(value === undefined ? minimum : value + 1);
  });
  const sync = () => {
    const value = getValue();
    minus.disabled = value === undefined || value <= minimum;
    plus.disabled = value !== undefined && value >= maximum;
  };
  return { element, choose, sync };
}

function durationBounds(definition) {
  const { minimum, maximum, anyOf } = definition;
  const choices = definition.enum || definition.options;
  if (Array.isArray(choices)) {
    if (anyOf || !choices.every((value) => Number.isSafeInteger(value) &&
      (minimum === undefined || value >= minimum) && (maximum === undefined || value <= maximum))) return null;
    const values = [...new Set(choices)].sort((a, b) => a - b);
    if (values.length < 2) return null;
    return { minimum: values[0], maximum: values.at(-1), smart: values.includes(-1), values };
  }
  if (!anyOf) return { minimum, maximum, smart: false };
  if (minimum !== undefined || maximum !== undefined || !Array.isArray(anyOf) || anyOf.length !== 2) return null;
  const smart = anyOf.find((branch) => branch?.const === -1 && Object.keys(branch).length === 1);
  const range = anyOf.find((branch) => branch?.type === "integer" && Object.keys(branch).every((key) =>
    ["type", "minimum", "maximum", "title", "description", "default"].includes(key)));
  if (!smart || !range || !(range.minimum >= 1)) return null;
  return { minimum: range.minimum, maximum: range.maximum, smart: true };
}

export function makeFieldHelp(hint, label) {
  const tip = make("span", { className: "ea-field-help", id: "easyapi-help-" + ++nextFieldId, textContent: hint });
  tip.setAttribute("role", "tooltip");
  const trigger = make("button", { type: "button", className: "ea-help", textContent: "i" });
  trigger.setAttribute("aria-label", label);
  trigger.setAttribute("aria-describedby", tip.id);
  if (tip.showPopover) {
    tip.setAttribute("popover", "manual");
    const show = () => {
      if (!tip.matches(":popover-open")) tip.showPopover();
      const bounds = trigger.getBoundingClientRect();
      const left = Math.max(8, Math.min(bounds.left, document.documentElement.clientWidth - tip.offsetWidth - 8));
      const top = bounds.top - tip.offsetHeight - 5;
      tip.style.left = left + "px";
      tip.style.top = (top >= 8 ? top : bounds.bottom + 5) + "px";
    };
    const hide = () => tip.hidePopover();
    trigger.addEventListener("pointerenter", show);
    trigger.addEventListener("pointerleave", () => { if (!trigger.matches(":focus-visible")) hide(); });
    trigger.addEventListener("focus", show);
    trigger.addEventListener("blur", hide);
    trigger.addEventListener("click", show);
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && tip.matches(":popover-open")) { event.stopPropagation(); hide(); }
    });
  }
  return { element: make("span", { className: "ea-help-wrap" }, trigger, tip), id: tip.id };
}

export function renderSchemaForm(container, schema, getValue, onChange, t, onResize = () => {}) {
  const wasExpanded = container.querySelector?.(".ea-more")?.open || false;
  container.replaceChildren();
  const properties = schema?.properties || {};
  const required = new Set(schema.required || []);
  const keys = Object.keys(properties).filter((key) => !OMIT.has(key) && !key.startsWith("__"));
  keys.sort((a, b) => Number(required.has(b)) - Number(required.has(a)) ||
    (COMMON.includes(a) ? COMMON.indexOf(a) : 100) - (COMMON.includes(b) ? COMMON.indexOf(b) : 100));
  const primary = make("div", { className: "ea-fields" });
  const more = make("details", { className: "ea-more", open: wasExpanded }, make("summary", {}, t("moreParameters")));
  const secondary = make("div", { className: "ea-fields" });
  const commit = (path, next, remove = false) => onChange(writeAt(getValue(), path, next, remove));

  function build(key, definition, path, isRequired = false, depth = 0) {
    const current = atPath(getValue(), path);
    const label = definition.title || t("param." + key, key);
    const hint = definition.description || "";
    const type = primitiveType(definition);
    const options = definition.enum || definition.options;
    const wrapper = make("div", { className: "ea-form-field" });
    const labelText = label + (isRequired ? " *" : "");
    const name = make("span", { className: "ea-field-name" }, labelText);
    const heading = make("div", { className: "ea-field-heading" }, name);
    const help = hint ? makeFieldHelp(hint, t("parameterHelp", { name: label })) : null;
    wrapper.append(heading);
    if (help) name.append(help.element);
    const isArrayItem = typeof path.at(-1) === "number";
    const resetValue = () => {
      if (isArrayItem) commit(path, initialItem(definition));
      else if (isRequired && definition.default !== undefined) commit(path, clone(definition.default));
      else commit(path, undefined, true);
    };
    const canReset = (value) => value !== undefined && (isArrayItem
      ? JSON.stringify(value) !== JSON.stringify(initialItem(definition))
      : !isRequired || definition.default === undefined || JSON.stringify(value) !== JSON.stringify(definition.default));
    const reset = make("button", { type: "button", className: "ea-reset", textContent: "↶", title: t("useDefault"), hidden: !canReset(current) });
    reset.setAttribute("aria-label", t("useDefault") + " · " + label);
    heading.append(reset);

    if (path.length === 1 && key === "size" && type === "string" && definition["x-size-spec"]?.allow_custom === true) {
      const select = make("select", { className: "ea-size-preset", id: "easyapi-field-" + ++nextFieldId });
      select.dataset.path = JSON.stringify(path);
      const presets = [];
      for (const option of [...(Array.isArray(options) ? options : []), definition.default]) {
        const result = validateImageSize(definition, option);
        if (result.ok && !presets.includes(result.value)) presets.push(result.value);
      }
      if (definition["x-size-spec"].allow_auto === true && !presets.includes("auto")) presets.push("auto");
      select.append(make("option", { value: "" }, definition.default === undefined ? t("optionalValue") : t("sizeModelDefault", { value: definition.default })),
        ...presets.map((option) => make("option", { value: option }, option)), make("option", { value: "__custom" }, t("sizeCustom")));
      const width = make("input", { type: "number", className: "ea-size-width", min: "1", step: "1", placeholder: t("sizeWidth") });
      const height = make("input", { type: "number", className: "ea-size-height", min: "1", step: "1", placeholder: t("sizeHeight") });
      width.setAttribute("aria-label", t("sizeWidth")); height.setAttribute("aria-label", t("sizeHeight"));
      const spec = definition["x-size-spec"];
      for (const [field, axis] of [[width, "width"], [height, "height"]]) {
        if (spec["min_" + axis] !== undefined) field.min = spec["min_" + axis];
        if (spec["max_" + axis] !== undefined) field.max = spec["max_" + axis];
        if (spec.multiple_of !== undefined) field.step = spec.multiple_of;
      }
      const dimensions = make("div", { className: "ea-size-dimensions" }, width, make("span", { textContent: "×", ariaHidden: "true" }), height);
      const raw = make("input", { className: "ea-size-raw", type: "text" });
      raw.setAttribute("aria-label", label);
      const error = make("small", { className: "ea-size-error ea-error", id: "easyapi-size-error-" + ++nextFieldId, hidden: true });
      error.setAttribute("role", "status");
      for (const field of [width, height, raw]) field.setAttribute("aria-describedby", error.id);
      const showError = (reason = "") => {
        const message = reason ? t("sizeError." + reason, spec) : "";
        error.textContent = message; error.hidden = !message;
        for (const field of [width, height, raw]) { field.setCustomValidity?.(message); field.setAttribute("aria-invalid", String(Boolean(message))); }
      };
      const display = () => {
        const saved = atPath(getValue(), path), effective = saved === undefined ? definition.default : saved;
        const checked = validateImageSize(definition, effective), parts = imageSizeParts(effective);
        select.value = saved === undefined ? "" : checked.ok && presets.includes(checked.value) ? checked.value : "__custom";
        dimensions.hidden = select.value !== "__custom";
        raw.hidden = saved === undefined || Boolean(parts) || checked.ok;
        if (!raw.hidden) { raw.value = String(saved); dimensions.hidden = true; }
        width.value = saved !== undefined && parts ? String(parts.width) : "";
        height.value = saved !== undefined && parts ? String(parts.height) : "";
        const defaults = imageSizeParts(definition.default);
        width.placeholder = defaults ? String(defaults.width) : t("sizeWidth");
        height.placeholder = defaults ? String(defaults.height) : t("sizeHeight");
        showError(saved !== undefined && !checked.ok ? checked.reason : "");
        reset.hidden = !canReset(saved);
      };
      const accept = (next) => {
        const result = validateImageSize(definition, next);
        if (!result.ok) { showError(result.reason); return; }
        commit(path, result.value); display();
      };
      select.addEventListener("change", () => {
        showError(); raw.hidden = true;
        if (select.value === "__custom") { dimensions.hidden = false; onResize(); return; }
        if (select.value === "") { resetValue(); display(); }
        else accept(select.value);
        onResize();
      });
      const customChange = () => {
        if (!width.value || !height.value) { showError("invalid_size"); return; }
        accept(`${width.value}x${height.value}`);
      };
      width.addEventListener("change", customChange); height.addEventListener("change", customChange);
      raw.addEventListener("change", () => accept(raw.value));
      reset.addEventListener("click", () => { resetValue(); display(); onResize(); });
      const controlLabel = make("label", { htmlFor: select.id }, labelText);
      name.replaceChildren(controlLabel, ...(help ? [help.element] : []));
      if (help) select.setAttribute("aria-describedby", help.id);
      wrapper.className += " ea-field-inline";
      wrapper.append(make("div", { className: "ea-size-control" }, select, dimensions, raw, error));
      display();
    } else if (type === "object" && definition.properties && !definition.oneOf && !definition.anyOf && depth < 4) {
      reset.addEventListener("click", () => { resetValue(); render(); });
      const body = make("div", { className: "ea-object-fields" });
      body.addEventListener("change", () => { reset.hidden = !canReset(atPath(getValue(), path)); });
      for (const [child, spec] of Object.entries(definition.properties)) {
        body.append(build(child, spec, [...path, child], (definition.required || []).includes(child), depth + 1));
      }
      wrapper.append(body);
    } else if (type === "array" && definition.items && !Array.isArray(definition.items) && !definition.items.oneOf && !definition.items.anyOf && depth < 3) {
      reset.addEventListener("click", () => { resetValue(); render(); });
      const list = make("div", { className: "ea-array" });
      const values = Array.isArray(current) ? current : [];
      values.forEach((item, index) => {
        const row = make("div", { className: "ea-array-item" });
        const remove = make("button", { type: "button", className: "ea-reset", textContent: t("removeItem"), title: t("removeItem") });
        remove.addEventListener("click", () => { commit([...path, index], undefined, true); render(); });
        row.append(build(String(index + 1), definition.items, [...path, index], false, depth + 1), remove);
        list.append(row);
      });
      const add = make("button", { type: "button", textContent: t("addItem"), disabled: definition.maxItems !== undefined && values.length >= definition.maxItems });
      add.addEventListener("click", () => {
        const latest = atPath(getValue(), path);
        commit(path, [...(Array.isArray(latest) ? latest : []), initialItem(definition.items)]);
        render();
      });
      wrapper.append(list, add);
      if (definition.minItems !== undefined || definition.maxItems !== undefined) wrapper.append(make("small", { className: "ea-caption" }, t("itemLimits", { min: definition.minItems ?? 0, max: definition.maxItems ?? "∞" })));
    } else {
      let input;
      let control;
      let syncControl = () => {};
      const choices = Array.isArray(options) ? options : type === "boolean" ? [true, false] : null;
      const hasChoices = choices !== null;
      const isJSON = ["object", "array"].includes(type) || definition.oneOf || definition.anyOf || definition.additionalProperties;
      const displayOption = (choice) => typeof choice === "boolean" ? t(choice ? "enabled" : "disabled") : optionText(choice);
      const declaredDefault = JSON.stringify(definition.default);
      const defaultChoice = choices?.some((choice) => JSON.stringify(choice) === declaredDefault) ? declaredDefault : "";
      const supportsSwitch = type === "boolean" && !isJSON && definition.const === undefined &&
        (!Array.isArray(definition.type) || !definition.type.includes("null")) &&
        (!Array.isArray(options) || (options.length === 2 && options.includes(true) && options.includes(false)));
      const canSwitch = (value) => supportsSwitch && (value === undefined || typeof value === "boolean");
      const isSwitch = canSwitch(current);
      const simpleInteger = type === "integer" && !definition.oneOf && !definition.additionalProperties && definition.const === undefined &&
        definition.exclusiveMinimum === undefined && definition.exclusiveMaximum === undefined &&
        (definition.multipleOf === undefined || definition.multipleOf === 1);
      const minimum = definition.minimum;
      const maximum = definition.maximum;
      const bounds = simpleInteger && !definition.allOf && !definition.not && !definition.if && ["duration", "seconds"].includes(key)
        ? durationBounds(definition) : null;
      const durationMin = bounds?.minimum;
      const durationMax = bounds?.maximum;
      const durationValues = bounds?.values;
      const hasSmartDuration = bounds?.smart === true;
      const withinBounds = (value) => value === undefined || (durationValues ? durationValues.includes(value) :
        (hasSmartDuration && value === -1) || (Number.isSafeInteger(value) && value >= durationMin && value <= durationMax));
      const supportsDuration = Number.isSafeInteger(durationMin) && Number.isSafeInteger(durationMax) &&
        durationMin < durationMax && withinBounds(definition.default);
      const canSlide = (value) => supportsDuration && withinBounds(value);
      const isDuration = canSlide(current);
      // Enum sliders use indexes so sparse choices never invent unsupported seconds.
      // For bounded ranges, reserve the stop before the fixed range for smart duration.
      const firstDurationStop = durationValues ? 0 : hasSmartDuration ? durationMin - 1 : durationMin;
      const lastDurationStop = durationValues ? durationValues.length - 1 : durationMax;
      const durationAtStop = (stop) => durationValues ? durationValues[stop] : hasSmartDuration && stop === firstDurationStop ? -1 : stop;
      const durationStop = (value) => value === undefined ? undefined : durationValues ? durationValues.indexOf(value) : hasSmartDuration && value === -1 ? firstDurationStop : value;
      const inputValue = (value) => value === undefined ? defaultChoice : hasChoices ? JSON.stringify(value) : isJSON ? JSON.stringify(value, null, 2) : value;
      const effectiveNumber = () => {
        const value = atPath(getValue(), path);
        const effective = value === undefined ? definition.default : value;
        return Number.isSafeInteger(effective) ? effective : undefined;
      };
      if (isSwitch) {
        input = make("input", { type: "checkbox", className: "ea-switch" });
        input.setAttribute("role", "switch");
      } else if (hasChoices && !isDuration) {
        const choicesUI = choices.map((choice) => make("option", { value: JSON.stringify(choice) }, displayOption(choice)));
        if (current !== undefined && !choicesUI.some((choice) => choice.value === inputValue(current))) {
          choicesUI.push(make("option", { value: inputValue(current), disabled: true, hidden: true }, displayOption(current)));
        }
        input = make("select", {}, ...choicesUI);
      } else if (isDuration) {
        input = make("input", { type: "range", className: "ea-duration-input", min: String(firstDurationStop), max: String(lastDurationStop), step: "1" });
        const output = make("output", { className: "ea-duration-value" });
        const track = make("div", { className: "ea-duration-track" }, input, output);
        const steps = stepper("duration", input, label, t, firstDurationStop, lastDurationStop, () => durationStop(effectiveNumber()), track);
        control = steps.element;
        const paint = (value) => {
          const fraction = value === undefined ? 0 : (durationStop(value) - firstDurationStop) / (lastDurationStop - firstDurationStop);
          control.style.setProperty("--ea-duration-fill", `${fraction * 100}%`);
          output.textContent = value === undefined ? "—" : hasSmartDuration && value === -1 ? t("smartDuration") : String(value);
          if (hasSmartDuration || durationValues) input.setAttribute("aria-valuetext", value === undefined ? t("durationUnset") : output.textContent);
        };
        input.addEventListener("input", () => paint(durationAtStop(Number(input.value))));
        if (hasSmartDuration || durationValues) {
          // The native range emits no change if the first stop is already under its thumb.
          input.addEventListener("click", () => { if (effectiveNumber() === undefined) steps.choose(Number(input.value)); });
          input.addEventListener("keydown", (event) => {
            if (event.key === "Home" && effectiveNumber() === undefined) {
              event.preventDefault();
              steps.choose(firstDurationStop);
            }
          });
        }
        syncControl = () => { paint(effectiveNumber()); steps.sync(); };
      } else if (["integer", "number"].includes(type)) {
        input = make("input", { type: "number", placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue"), step: type === "integer" ? "1" : "any" });
        if (minimum !== undefined) input.min = minimum;
        if (maximum !== undefined) input.max = maximum;
        const seedMinimum = minimum ?? 0;
        const seedMaximum = maximum ?? Math.max(seedMinimum, 2147483647);
        if (key === "seed" && simpleInteger && !isJSON && Number.isSafeInteger(seedMinimum) && Number.isSafeInteger(seedMaximum) && seedMinimum <= seedMaximum) {
          input.className = "ea-seed-value";
          const steps = stepper("seed", input, label, t, seedMinimum, maximum ?? Number.MAX_SAFE_INTEGER, effectiveNumber);
          const shuffle = stepButton("⤨", t("shuffleSeed"), "ea-shuffle");
          steps.element.insertBefore(shuffle, steps.element.lastElementChild);
          shuffle.addEventListener("click", () => {
            // Nonnegative seeds are reproducible on models that reserve a negative value for automatic seeding.
            const start = seedMaximum >= 0 ? Math.max(seedMinimum, 0) : seedMinimum;
            let next = start + Math.floor(Math.random() * (seedMaximum - start + 1));
            if (next === effectiveNumber() && start < seedMaximum) next = next === seedMaximum ? start : next + 1;
            steps.choose(next);
          });
          control = steps.element;
          syncControl = steps.sync;
        }
      } else if (isJSON) {
        input = make("textarea", { placeholder: type === "array" ? "[]" : "{}", rows: 3 });
      } else {
        input = make((definition.maxLength || 0) > 150 || /prompt|instructions|description/.test(key) ? "textarea" : "input",
          { placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue") });
      }
      input.id = "easyapi-field-" + ++nextFieldId;
      const refreshInput = () => {
        const value = atPath(getValue(), path);
        if (isSwitch) {
          const effective = value === undefined ? definition.default : value;
          input.checked = effective === true;
          input.dataset.unset = String(effective === undefined);
          input.title = effective === undefined ? t("optionalValue") : "";
        } else input.value = isDuration ? String(durationStop(effectiveNumber()) ?? firstDurationStop) : inputValue(value);
        syncControl();
      };
      refreshInput();
      const controlLabel = make("label", { htmlFor: input.id }, labelText);
      name.replaceChildren(controlLabel, ...(help ? [help.element] : []));
      if (help) input.setAttribute("aria-describedby", help.id);
      input.dataset.path = JSON.stringify(path);
      wrapper.className += input.tagName === "TEXTAREA" ? " ea-field-wide" : " ea-field-inline";
      const upgradeFallback = () => {
        const value = atPath(getValue(), path);
        if ((isSwitch || !canSwitch(value)) && (isDuration || !canSlide(value))) return false;
        render();
        [...container.querySelectorAll("[data-path]")].find((field) => field.dataset.path === input.dataset.path)?.focus();
        return true;
      };
      reset.addEventListener("click", () => {
        resetValue();
        if (upgradeFallback()) return;
        refreshInput();
        input.setCustomValidity?.("");
        reset.hidden = true;
        input.focus?.();
      });
      input.addEventListener("change", () => {
        try {
          input.setCustomValidity?.("");
          if (input.validity?.badInput) throw new Error(t("invalidNumber"));
          let next = input.value;
          if (isSwitch) commit(path, input.checked);
          else if (next === "") {
            if (isArrayItem) {
              resetValue();
              input.value = inputValue(atPath(getValue(), path));
            } else commit(path, undefined, true);
          }
          else if (isDuration) {
            const duration = durationAtStop(Number(next));
            if (duration === undefined || !withinBounds(duration)) throw new Error(t("invalidNumber"));
            commit(path, duration);
          }
          else if (hasChoices || (isJSON && !supportsDuration)) commit(path, JSON.parse(next));
          else if (["integer", "number"].includes(type)) {
            next = Number(next);
            if (!Number.isFinite(next) || (type === "integer" && !Number.isInteger(next)) ||
              (supportsDuration ? !withinBounds(next) : next < minimum || next > maximum)) throw new Error(input.validationMessage || t("invalidNumber"));
            commit(path, next);
          } else commit(path, next);
          if (upgradeFallback()) return;
          reset.hidden = !canReset(atPath(getValue(), path));
          if (isSwitch) refreshInput();
          else syncControl();
        } catch (error) { reset.hidden = false; input.setCustomValidity?.(error.message); input.reportValidity?.(); }
      });
      wrapper.append(control || input);
    }
    return wrapper;
  }
  function render() {
    const wasOpen = more.open;
    primary.replaceChildren(); secondary.replaceChildren();
    for (const key of keys) {
      const field = build(key, properties[key], [key], required.has(key));
      (required.has(key) || COMMON.includes(key) || properties[key]["x-ui-group"] === "common" ? primary : secondary).append(field);
    }
    more.open = wasOpen;
    container.replaceChildren();
    if (primary.children.length) container.append(primary);
    if (secondary.children.length) { more.replaceChildren(make("summary", {}, t("moreParameters")), secondary); container.append(more); }
    if (!keys.length) container.append(make("p", { className: "ea-caption" }, t("noDeclaredFields")));
    onResize();
  }
  more.addEventListener("toggle", onResize);
  render();
  return { rerender: render };
}
