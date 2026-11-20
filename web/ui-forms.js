import { atPath, clone, writeAt } from "./ui-state.js";

const OMIT = new Set(["model", "prompt", "messages", "contents", "content", "input", "api_key", "authorization", "token", "input_reference", "input_references", "image", "images", "image_url", "audio", "audio_url", "video", "video_url", "mask", "file", "files", "base64Array"]);
const COMMON = ["stream", "size", "resolution", "aspect_ratio", "width", "height", "duration", "seconds", "fps", "seed", "n",
  "generate_audio", "return_last_frame", "watermark", "quality", "style", "negative_prompt", "voice", "instructions",
  "speed", "speech_rate", "language", "response_format", "temperature", "max_tokens", "max_output_tokens", "steps", "guidance_scale"];
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

    if (type === "object" && definition.properties && !definition.oneOf && !definition.anyOf && depth < 4) {
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
      const hasChoices = Array.isArray(options) || type === "boolean";
      const isJSON = ["object", "array"].includes(type) || definition.oneOf || definition.anyOf || definition.additionalProperties;
      const displayOption = (choice) => typeof choice === "boolean" ? t(choice ? "enabled" : "disabled") : optionText(choice);
      const inputValue = (value) => value === undefined ? "" : hasChoices ? JSON.stringify(value) : isJSON ? JSON.stringify(value, null, 2) : value;
      if (hasChoices) {
        const choices = Array.isArray(options) ? options : [true, false];
        const choicesUI = choices.map((choice) => make("option", { value: JSON.stringify(choice) }, displayOption(choice)));
        if (current !== undefined && !choicesUI.some((choice) => choice.value === inputValue(current))) {
          choicesUI.push(make("option", { value: inputValue(current), disabled: true }, displayOption(current)));
        }
        input = make("select", {}, make("option", { value: "" }, t("defaultHint", { value: definition.default !== undefined ? displayOption(definition.default) : "—" })),
          ...choicesUI);
      } else if (["integer", "number"].includes(type)) {
        input = make("input", { type: "number", placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue"), step: type === "integer" ? "1" : "any" });
        if (definition.minimum !== undefined) input.min = definition.minimum;
        if (definition.maximum !== undefined) input.max = definition.maximum;
      } else if (isJSON) {
        input = make("textarea", { placeholder: type === "array" ? "[]" : "{}", rows: 3 });
      } else {
        input = make((definition.maxLength || 0) > 150 || /prompt|instructions|description/.test(key) ? "textarea" : "input",
          { placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue") });
      }
      input.id = "easyapi-field-" + ++nextFieldId;
      input.value = inputValue(current);
      const controlLabel = make("label", { htmlFor: input.id }, labelText);
      name.replaceChildren(controlLabel, ...(help ? [help.element] : []));
      if (help) input.setAttribute("aria-describedby", help.id);
      input.dataset.path = JSON.stringify(path);
      wrapper.className += input.tagName === "TEXTAREA" ? " ea-field-wide" : " ea-field-inline";
      reset.addEventListener("click", () => {
        resetValue();
        input.value = inputValue(atPath(getValue(), path));
        input.setCustomValidity?.("");
        reset.hidden = true;
        input.focus?.();
      });
      input.addEventListener("change", () => {
        try {
          input.setCustomValidity?.("");
          if (input.validity?.badInput) throw new Error(t("invalidNumber"));
          let next = input.value;
          if (next === "") {
            if (isArrayItem) {
              resetValue();
              input.value = inputValue(atPath(getValue(), path));
            } else commit(path, undefined, true);
          }
          else if (hasChoices || isJSON) commit(path, JSON.parse(next));
          else if (["integer", "number"].includes(type)) {
            next = Number(next);
            if (!Number.isFinite(next) || (type === "integer" && !Number.isInteger(next)) ||
              next < definition.minimum || next > definition.maximum) throw new Error(input.validationMessage || t("invalidNumber"));
            commit(path, next);
          } else commit(path, next);
          reset.hidden = !canReset(atPath(getValue(), path));
        } catch (error) { reset.hidden = false; input.setCustomValidity?.(error.message); input.reportValidity?.(); }
      });
      wrapper.append(input);
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
