const PREFIX = "easyapi.videoTuningDraft.v1.";
const PRIVATE_KEY = /^(?:api[_-]?key|authorization|access[_-]?token|auth[_-]?token|password|secret)$/i;

// This is a local draft identity, not an authorization or security boundary.
export function tuningFingerprint(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  let a = 2166136261, b = 2246822519;
  for (let index = 0; index < text.length; ++index) {
    a = Math.imul(a ^ text.charCodeAt(index), 16777619);
    b = Math.imul(b ^ text.charCodeAt(index), 3266489917);
  }
  return text.length.toString(16) + ":" + (a >>> 0).toString(16) + (b >>> 0).toString(16);
}

function safeValue(value, depth = 0) {
  if (depth > 15) return undefined;
  if (typeof value === "string") {
    if (/^(?:data:(?:image|video|audio)\/|blob:)/i.test(value)) return undefined;
    return value.length <= 200000 ? value : undefined;
  }
  if (value == null || typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.slice(0, 100).map(item => safeValue(item, depth + 1));
  if (typeof value !== "object") return undefined;
  return Object.fromEntries(Object.entries(value).filter(([key]) =>
    !PRIVATE_KEY.test(key) && !["__proto__", "prototype", "constructor", "media", "resultMedia", "path"].includes(key))
    .map(([key, entry]) => [key, safeValue(entry, depth + 1)]).filter(([, entry]) => entry !== undefined));
}

export function readTuningDraft(scopeId) {
  if (!scopeId) return null;
  try {
    const raw = localStorage.getItem(PREFIX + scopeId);
    if (!raw || raw.length > 800000) return null;
    const value = JSON.parse(raw);
    return value?.version === 1 && value.scopeId === scopeId ? value : null;
  } catch { return null; }
}

export function writeTuningDraft(scopeId, record) {
  if (!scopeId) return false;
  try {
    const text = JSON.stringify({ ...safeValue(record), version: 1, scopeId, savedAt: Date.now() });
    if (text.length > 800000) return false;
    localStorage.setItem(PREFIX + scopeId, text);
    return true;
  } catch { return false; }
}

export function clearTuningDraft(scopeId) {
  if (!scopeId) return;
  try { localStorage.removeItem(PREFIX + scopeId); } catch { /* Optional local storage. */ }
}
