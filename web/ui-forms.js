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

export function renderSchemaForm(container, schema, getValue, onChange, t, onResize = () => {}) {
  container.replaceChildren();
  const properties = schema?.properties || {};
  const required = new Set(schema.required || []);
  const keys = Object.keys(properties).filter((key) => !OMIT.has(key) && !key.startsWith("__"));
  keys.sort((a, b) => Number(required.has(b)) - Number(required.has(a)) ||
    (COMMON.includes(a) ? COMMON.indexOf(a) : 100) - (COMMON.includes(b) ? COMMON.indexOf(b) : 100));
  const primary = make("div", { className: "ea-fields" });
  const more = make("details", { className: "ea-more" }, make("summary", {}, t("moreParameters")));
  const secondary = make("div", { className: "ea-fields" });
  const commit = (path, next, remove = false) => onChange(writeAt(getValue(), path, next, remove));

  function build(key, definition, path, isRequired = false, depth = 0) {
    const current = atPath(getValue(), path);
    const label = definition.title || t("param." + key, key);
    const hint = definition.description || "";
    const type = primitiveType(definition);
    const options = definition.enum || definition.options;
    const wrapper = make("div", { className: "ea-form-field" });
    const heading = make("div", { className: "ea-field-heading" }, make("span", {}, label + (isRequired ? " *" : "")));
    wrapper.append(heading);
    if (hint) wrapper.title = hint;
    const reset = make("button", { type: "button", className: "ea-reset", textContent: t("useDefault"), title: t("useDefault") });
    reset.addEventListener("click", () => {
      if (isRequired && definition.default !== undefined) commit(path, clone(definition.default));
      else commit(path, undefined, true);
      render();
    });
    heading.append(reset);

    if (type === "object" && definition.properties && !definition.oneOf && !definition.anyOf && depth < 4) {
      const body = make("div", { className: "ea-object-fields" });
      for (const [child, spec] of Object.entries(definition.properties)) {
        body.append(build(child, spec, [...path, child], (definition.required || []).includes(child), depth + 1));
      }
      wrapper.append(body);
    } else if (type === "array" && definition.items && !Array.isArray(definition.items) && !definition.items.oneOf && !definition.items.anyOf && depth < 3) {
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
      add.addEventListener("click", () => { commit(path, [...values, initialItem(definition.items)]); render(); });
      wrapper.append(list, add);
      if (definition.minItems !== undefined || definition.maxItems !== undefined) wrapper.append(make("small", { className: "ea-caption" }, t("itemLimits", { min: definition.minItems ?? 0, max: definition.maxItems ?? "∞" })));
    } else {
      let input;
      if (Array.isArray(options) || type === "boolean") {
        const choices = Array.isArray(options) ? options : [true, false];
        input = make("select", {}, make("option", { value: "" }, t("defaultHint", { value: definition.default !== undefined ? optionText(definition.default) : "—" })),
          ...choices.map((choice) => make("option", { value: JSON.stringify(choice) }, typeof choice === "boolean" ? t(choice ? "enabled" : "disabled") : optionText(choice))));
        input.value = current === undefined ? "" : JSON.stringify(current);
      } else if (["integer", "number"].includes(type)) {
        input = make("input", { type: "number", value: current ?? "", placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue"), step: type === "integer" ? "1" : "any" });
        if (definition.minimum !== undefined) input.min = definition.minimum;
        if (definition.maximum !== undefined) input.max = definition.maximum;
      } else if (["object", "array"].includes(type) || definition.oneOf || definition.anyOf || definition.additionalProperties) {
        input = make("textarea", { value: current === undefined ? "" : JSON.stringify(current, null, 2), placeholder: type === "array" ? "[]" : "{}", rows: 3 });
      } else {
        input = make((definition.maxLength || 0) > 150 || /prompt|instructions|description/.test(key) ? "textarea" : "input",
          { value: current ?? "", placeholder: definition.default !== undefined ? String(definition.default) : t("optionalValue") });
      }
      input.setAttribute("aria-label", label);
      input.dataset.path = JSON.stringify(path);
      input.addEventListener("change", () => {
        try {
          if (input.value === "") { commit(path, undefined, true); input.setCustomValidity?.(""); return; }
          let next = input.value;
          if (Array.isArray(options) || type === "boolean" || ["object", "array"].includes(type) || definition.oneOf || definition.anyOf) next = JSON.parse(next);
          else if (["integer", "number"].includes(type)) {
            next = Number(next);
            if (!Number.isFinite(next) || (type === "integer" && !Number.isInteger(next))) throw new Error(t("invalidNumber"));
          }
          commit(path, next); input.setCustomValidity?.("");
        } catch (error) { input.setCustomValidity?.(error.message); input.reportValidity?.(); }
      });
      wrapper.append(input);
    }
    if (hint) wrapper.append(make("small", { className: "ea-field-help" }, hint));
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
