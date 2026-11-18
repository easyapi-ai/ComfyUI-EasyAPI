// Pure state helpers shared by both Comfy renderers. Never persist credentials.
export const clone = (value) => JSON.parse(JSON.stringify(value));
export const draftKey = (profile, model, operation) => JSON.stringify([profile || "", model || "", operation || ""]);
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
