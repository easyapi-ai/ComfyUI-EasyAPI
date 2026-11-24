// Pure state helpers shared by both Comfy renderers. Never persist credentials.
export const clone = (value) => JSON.parse(JSON.stringify(value));
export const draftKey = (profile, model, operation) => JSON.stringify([profile || "", model || "", operation || ""]);
const suppliedImage = (value) => typeof value === "string" ? Boolean(value.trim()) :
  Array.isArray(value) ? value.some(suppliedImage) : Boolean(value && typeof value === "object" &&
    [value.url, value.path, value.image_url].some(suppliedImage));
export function inferImageOperation(inputs = [], params = {}) {
  const linked = inputs.some((input) => input.link != null && ["image", "references"].includes(input.name));
  const references = params.input_references;
  const referenceImage = (entry) => typeof entry === "string" ? suppliedImage(entry) : entry && typeof entry === "object" &&
    !["audio", "video", "mask"].includes(entry.kind || entry.type) && suppliedImage(entry);
  return linked || suppliedImage(params.image) || suppliedImage(params.images) ||
    (Array.isArray(references) ? references.some(referenceImage) : referenceImage(references))
    ? "image-edit" : "image-generation";
}

export const IMAGE_LAYOUT_VERSION = 2;
const IMAGE_DEFAULT_OUTPUT_LABELS = new Set(["STRING", "Text", "text", "文本", "文字", "JSON", "EASYAPI_JSON", "IMAGE", "Images", "Image", "image", "图片", "圖像", "圖片", "url", "URL", "response", "Response", "响应", "回應"]);
function imageOutputSlot(output, name, type, index) {
  const slot = { ...output, name, type, slot_index: index };
  delete slot.localized_name;
  if (IMAGE_DEFAULT_OUTPUT_LABELS.has(slot.label)) delete slot.label;
  return slot;
}
export function legacyImageLayout(node) {
  return node?.type === "EasyAPIImages" && node.outputs?.length >= 8 &&
    node.outputs[0]?.type === "STRING" && node.outputs[1]?.type === "EASYAPI_JSON" && node.outputs[2]?.type === "IMAGE";
}
// Migrate the serialized graph before LiteGraph restores slots and links. Slot 2 used
// to be IMAGE; leaving it untouched would silently wire it into the new JSON output.
export function migrateImageWorkflow(graph) {
  const report = { nodes: 0, disconnected: 0 };
  const seen = new Set();
  const visit = (scope) => {
    if (!scope || typeof scope !== "object" || seen.has(scope)) return;
    seen.add(scope);
    const nodes = Array.isArray(scope.nodes) ? scope.nodes : [];
    const migrated = new Map();
    for (const node of nodes) {
      if (node?.type === "EasyAPIImages" && node.outputs?.length === 3 &&
        node.outputs[0]?.type === "IMAGE" && node.outputs[1]?.type === "STRING" && node.outputs[2]?.type === "EASYAPI_JSON") {
        node.outputs = [imageOutputSlot(node.outputs[0], "image", "IMAGE", 0),
          imageOutputSlot(node.outputs[1], "url", "STRING", 1), imageOutputSlot(node.outputs[2], "响应", "EASYAPI_JSON", 2)];
      }
      if (!legacyImageLayout(node)) continue;
      const oldInputs = node.inputs || [];
      const inputs = oldInputs.filter((input) => !["operation", "wait_for_result", "timeout", "audio", "video"].includes(input.name));
      migrated.set(String(node.id), { node, oldInputs, inputs });
      node.outputs = [
        { ...imageOutputSlot(node.outputs[2], "image", "IMAGE", 0), links: [] },
        { name: "url", type: "STRING", slot_index: 1, links: [] },
        { ...imageOutputSlot(node.outputs[1], "响应", "EASYAPI_JSON", 2), links: [] },
      ];
      node.inputs = inputs;
      if (Array.isArray(node.widgets_values)) {
        const values = node.widgets_values;
        // Some frontend versions serialized the custom DOM control as a leading
        // empty value. Named values are authoritative; otherwise identify the
        // old operation instead of assuming the controls never occupied a slot.
        const operations = new Set(["image-generation", "image-edit", "image-task", "image-task-edit"]);
        const offset = operations.has(values[2]) ? 0 : operations.has(values[3]) ? 1 : 0;
        const named = node.widgets_values_named || {};
        const positions = { profile_id: 0, model: 1, prompt: 3, params_json: 4 };
        node.widgets_values = Object.entries(positions).map(([name, index]) => Object.hasOwn(named, name) ? named[name] : values[index + offset]);
        node.widgets_values_named = Object.fromEntries(Object.keys(positions).map((name, index) => [name, node.widgets_values[index]]));
      }
      for (const name of ["widgets_values_named", "widgets_values"]) {
        const values = node[name];
        if (values && !Array.isArray(values) && typeof values === "object") {
          for (const field of ["operation", "wait_for_result", "timeout", "easyapi_controls"]) delete values[field];
        }
      }
      node.properties ??= {};
      node.properties.easyapi_image_layout = IMAGE_LAYOUT_VERSION;
      ++report.nodes;
    }
    if (migrated.size && Array.isArray(scope.links)) {
      const removed = new Set();
      const kept = [];
      for (const link of scope.links) {
        const array = Array.isArray(link);
        const id = array ? link[0] : link.id;
        const origin = String(array ? link[1] : link.origin_id);
        const target = String(array ? link[3] : link.target_id);
        const source = migrated.get(origin), destination = migrated.get(target);
        let drop = false;
        if (source) {
          const oldSlot = array ? link[2] : link.origin_slot;
          const newSlot = ({ 2: 0, 1: 2 })[oldSlot];
          if (newSlot === undefined) drop = true;
          else {
            if (array) { link[2] = newSlot; link[5] = source.node.outputs[newSlot].type; }
            else { link.origin_slot = newSlot; link.type = source.node.outputs[newSlot].type; }
          }
        }
        if (destination) {
          const oldSlot = array ? link[4] : link.target_slot;
          const oldInput = destination.oldInputs[oldSlot];
          const newSlot = destination.inputs.findIndex((input) => input === oldInput);
          if (newSlot < 0) drop = true;
          else if (array) link[4] = newSlot;
          else link.target_slot = newSlot;
        }
        if (drop) {
          removed.add(String(id)); ++report.disconnected;
          const owner = source || destination;
          owner.node.properties.easyapi_image_disconnected = (owner.node.properties.easyapi_image_disconnected || 0) + 1;
        }
        else {
          kept.push(link);
          if (source) source.node.outputs[array ? link[2] : link.origin_slot].links.push(id);
        }
      }
      scope.links = kept;
      for (const node of nodes) {
        for (const input of node.inputs || []) if (removed.has(String(input.link))) input.link = null;
        for (const output of node.outputs || []) if (Array.isArray(output.links)) output.links = output.links.filter((id) => !removed.has(String(id)));
      }
      for (const reroute of scope.reroutes || []) if (Array.isArray(reroute.linkIds)) reroute.linkIds = reroute.linkIds.filter((id) => !removed.has(String(id)));
    }
    for (const child of scope.definitions?.subgraphs || []) visit(child);
    for (const child of scope.subgraphs || []) visit(child);
  };
  visit(graph);
  return report;
}
export function parseParams(raw) {
  const result = typeof raw === "string" ? JSON.parse(raw || "{}") : clone(raw || {});
  if (!result || Array.isArray(result) || typeof result !== "object") throw new Error("objectRequired");
  const inspect = (value) => {
    if (Array.isArray(value)) return value.forEach(inspect);
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (/^(api[_-]?key|authorization|access[_-]?token|auth[_-]?token|password|secret)$/i.test(key)) throw new Error("credentialsInParams");
      if (["__proto__", "constructor", "prototype"].includes(key)) throw new Error("invalidParameterKey");
      inspect(child);
    }
  };
  inspect(result);
  return result;
}
export function selectProfile(current, profiles, defaultId) {
  if (current) return current; // A saved workflow may refer to a temporarily unavailable profile.
  if (profiles.length === 1) return profiles[0].id || profiles[0].profile_id;
  return profiles.some((profile) => (profile.id || profile.profile_id) === defaultId) ? defaultId : "";
}
export function writeAt(object, path, value, remove = false) {
  const result = clone(object || {});
  let target = result;
  for (let i = 0; i < path.length - 1; i++) {
    const key = path[i];
    target[key] ??= typeof path[i + 1] === "number" ? [] : {};
    target = target[key];
  }
  const last = path.at(-1);
  if (remove) {
    if (Array.isArray(target)) target.splice(last, 1);
    else delete target[last];
  } else target[last] = value;
  return result;
}
export function atPath(object, path) { return path.reduce((value, key) => value?.[key], object); }
export function requiredDefaults(schema, object) {
  const result = clone(object || {});
  const required = new Set(schema.required || []);
  for (const [key, definition] of Object.entries(schema.properties || {})) {
    if (!definition) continue;
    if (required.has(key) && result[key] === undefined && definition.default !== undefined) result[key] = clone(definition.default);
    if (definition.type === "object" && result[key] && typeof result[key] === "object") result[key] = requiredDefaults(definition, result[key]);
    if (definition.type === "array" && Array.isArray(result[key]) && definition.items?.type === "object") result[key] = result[key].map((item) => requiredDefaults(definition.items, item));
  }
  return result;
}
export function normalizedSchema(raw) {
  return raw?.form_schema || raw?.request_schema || raw?.schema || raw?.data?.request_schema || raw?.data?.schema || raw?.data || raw || {};
}
export function persistDraft(node, key, raw, mode = "auto") {
  if (!key) return;
  const safe = parseParams(raw);
  node.properties ??= {};
  const book = node.properties.easyapi_drafts;
  if (!book || book.version !== 1 || !book.values || typeof book.values !== "object") node.properties.easyapi_drafts = { version: 1, values: {} };
  node.properties.easyapi_drafts.values[key] = { params: safe, mode };
}
export function readDraft(node, key) {
  const record = node.properties?.easyapi_drafts?.values?.[key];
  if (!record || !record.params) return null;
  try { return { params: parseParams(record.params), mode: record.mode || "auto" }; } catch { return null; }
}
export const VIDEO_PORTS = {
  first_frame: "first_frame", last_frame: "last_frame",
  reference_image_1: "reference_image", reference_image_2: "reference_image",
  reference_video_1: "reference_video", reference_video_2: "reference_video",
  reference_audio_1: "reference_audio", reference_audio_2: "reference_audio",
};
export function inferVideoScenario(inputs) {
  const connected = inputs.filter((input) => input.link != null).map((input) => input.name);
  const frame = connected.some((name) => ["first_frame", "last_frame"].includes(name));
  const reference = connected.some((name) => /^reference_(image|video|audio)_/.test(name));
  if (connected.includes("references") || (frame && reference)) return "";
  return frame ? "image-to-video" : reference ? "reference-to-video" : "text-to-video";
}
export function videoPortState(inputs, video, mode = "auto") {
  const inferred = inferVideoScenario(inputs);
  const selected = mode !== "auto" ? mode : inferred;
  const variant = video?.variants?.[selected];
  const roles = variant?.roles || video?.roles;
  const known = Array.isArray(roles) && (roles.length > 0 || (video?.scenarios?.length || 0) > 0);
  const invalid = known ? inputs.filter((input) => input.link != null && VIDEO_PORTS[input.name] && !roles.includes(VIDEO_PORTS[input.name])).map((input) => input.name) : [];
  return { selected, inferred, roles: roles || [], known, invalid, ambiguous: !inferred && (video?.scenarios?.length || 0) > 1 };
}
