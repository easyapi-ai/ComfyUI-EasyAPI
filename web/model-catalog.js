const STORAGE_KEY = "easyapi.modelCatalog.v2";
const MAX_AGE_MS = 120_000;
const MAX_STALE_MS = 86_400_000;
const MAX_ENTRIES = 24;
const MAX_STORAGE_LENGTH = 400_000;

function cancelled() {
  const error = new Error("The model connection changed");
  error.name = "AbortError";
  return error;
}

function forConsumer(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

function storageResult(value) {
  if (!Array.isArray(value?.models)) return null;
  const models = value.models.flatMap((model) => {
    if (typeof model === "string") return [model];
    const id = model?.id || model?.name;
    if (typeof id !== "string" || !id) return [];
    const entry = { id };
    for (const field of ["name", "display_name", "capability_status"]) {
      if (typeof model[field] === "string") entry[field] = model[field];
    }
    for (const field of ["supports_image", "supports_video"]) {
      if (typeof model[field] === "boolean") entry[field] = model[field];
    }
    if (Array.isArray(model.input_modalities)) {
      entry.input_modalities = model.input_modalities.filter((kind) => ["text", "image", "video", "audio"].includes(kind));
    }
    return [entry];
  });
  return { models, empty: !models.length, authority: "api_key", operation: value.operation, locale: value.locale };
}

export function createModelCatalog(request, { now = Date.now, storage = () => globalThis.localStorage } = {}) {
  const memory = new Map();
  const pending = new Map();
  const listeners = new Map();
  const errors = new Map();
  const revisions = new Map();
  const superseded = new Map();
  const epochs = new Map();
  const retained = (record) => Number.isFinite(record?.savedAt) && Number.isFinite(record?.expiresAt) &&
    record.savedAt <= now() && record.staleUntil > now() && record.staleUntil <= record.savedAt + MAX_STALE_MS &&
    record.expiresAt <= record.savedAt + MAX_AGE_MS;
  const fresh = (record) => retained(record) && record.expiresAt > now();

  function readStorage() {
    try {
      const raw = storage()?.getItem(STORAGE_KEY);
      if (!raw || raw.length > MAX_STORAGE_LENGTH) return [];
      const records = JSON.parse(raw);
      return Array.isArray(records) ? records.filter((record) => retained(record) &&
        typeof record.key === "string" && typeof record.profileId === "string" && Array.isArray(record.value?.models)) : [];
    } catch { return []; }
  }

  function writeStorage(records) {
    try {
      const bounded = records.filter(retained).slice(0, MAX_ENTRIES);
      let raw = JSON.stringify(bounded);
      while (raw.length > MAX_STORAGE_LENGTH) {
        bounded.pop();
        raw = JSON.stringify(bounded);
      }
      if (bounded.length) storage()?.setItem(STORAGE_KEY, raw);
      else storage()?.removeItem(STORAGE_KEY);
    } catch { /* Storage can be unavailable or reach browser quota. */ }
  }

  function scopeOf(input) {
    const scope = { profileId: String(input.profileId || ""), revision: String(input.revision || ""),
      family: String(input.family || ""), operation: String(input.operation || ""), locale: String(input.locale || ""),
      purpose: input.purpose === "prompt" ? "prompt" : "models" };
    scope.key = JSON.stringify([scope.profileId, scope.revision, scope.family, scope.operation, scope.locale, scope.purpose]);
    return scope;
  }

  function recordFor(scope) {
    let record = memory.get(scope.key);
    if (!retained(record)) {
      memory.delete(scope.key);
      record = scope.revision && readStorage().find((entry) => entry.key === scope.key);
      if (record) {
        memory.set(scope.key, record);
        if (memory.size > MAX_ENTRIES) memory.delete(memory.keys().next().value);
      }
    }
    return record || null;
  }

  function stateFor(scope, extra = {}) {
    const record = recordFor(scope);
    return { value: record?.value, stale: !!record && !fresh(record), updating: pending.has(scope.key),
      error: errors.get(scope.key)?.error || null, invalidated: false, ...extra };
  }

  function emit(scope, extra) {
    const state = stateFor(scope, extra);
    for (const listener of listeners.get(scope.key)?.callbacks || []) {
      try { listener(state); } catch { /* One viewer must not interrupt other viewers. */ }
    }
  }

  function invalidate(profileId, error = null) {
    if (!profileId) return;
    epochs.set(profileId, (epochs.get(profileId) || 0) + 1);
    for (const cache of [memory, pending]) {
      for (const [key, record] of cache) if (record.profileId === profileId) cache.delete(key);
    }
    for (const [key, record] of errors) if (record.profileId === profileId) errors.delete(key);
    writeStorage(readStorage().filter((record) => record.profileId !== profileId));
    for (const { scope } of listeners.values()) {
      if (scope.profileId !== profileId) continue;
      errors.delete(scope.key);
      emit(scope, { value: undefined, stale: false, updating: false, error, invalidated: true });
    }
  }

  function prepare(input) {
    const scope = scopeOf(input);
    const previous = revisions.get(scope.profileId);
    if (superseded.get(scope.profileId)?.has(scope.revision)) return null;
    if (previous !== undefined && previous !== scope.revision) {
      const old = superseded.get(scope.profileId) || new Set();
      old.add(previous); superseded.set(scope.profileId, old);
      revisions.set(scope.profileId, scope.revision);
      invalidate(scope.profileId);
    }
    revisions.set(scope.profileId, scope.revision);
    return scope;
  }

  function remember(scope, value, startedAt) {
    const savedAt = now();
    const ttl = Number.isFinite(value?.cache_ttl) ? Math.max(0, value.cache_ttl * 1000) : MAX_AGE_MS;
    const record = { key: scope.key, profileId: scope.profileId, value, savedAt,
      expiresAt: Math.min(startedAt + MAX_AGE_MS, savedAt + ttl), staleUntil: savedAt + MAX_STALE_MS };
    memory.set(scope.key, record);
    if (memory.size > MAX_ENTRIES) memory.delete(memory.keys().next().value);
    if (!scope.revision) return;
    const small = storageResult(value);
    if (small) writeStorage([{ ...record, value: small }, ...readStorage().filter((entry) => entry.key !== scope.key)]);
  }

  function start(scope, refresh) {
    const inflight = pending.get(scope.key);
    if (inflight && (!refresh || inflight.refresh)) return inflight.promise;
    const epoch = epochs.get(scope.profileId) || 0;
    const startedAt = now();
    const record = { profileId: scope.profileId, refresh };
    const current = () => pending.get(scope.key) === record && (epochs.get(scope.profileId) || 0) === epoch &&
      revisions.get(scope.profileId) === scope.revision;
    const query = new URLSearchParams({ profile_id: scope.profileId, family: scope.family,
      operation: scope.operation, locale: scope.locale, refresh: refresh ? "1" : "0" });
    const path = scope.purpose === "prompt" ? "/easyapi/prompt/models" : "/easyapi/models";
    errors.delete(scope.key);
    record.promise = Promise.resolve().then(() => request(`${path}?${query}`)).then((value) => {
      if (!current()) throw cancelled();
      remember(scope, value, startedAt);
      return value;
    }, (error) => {
      if (current()) {
        if (error.status === 401 || error.status === 403) invalidate(scope.profileId, error);
        else errors.set(scope.key, { profileId: scope.profileId, error });
      }
      throw error;
    }).finally(() => {
      if (pending.get(scope.key) === record) {
        pending.delete(scope.key);
        emit(scope);
      }
    });
    pending.set(scope.key, record);
    emit(scope);
    return record.promise;
  }

  function peek(input) {
    const scope = prepare(input);
    const record = scope && recordFor(scope);
    return record ? { value: record.value, stale: !fresh(record) } : null;
  }

  function subscribe(input, listener) {
    const scope = prepare(input);
    if (!scope) {
      listener({ value: undefined, stale: false, updating: false, error: null, invalidated: true });
      return () => {};
    }
    let entry = listeners.get(scope.key);
    if (!entry) { entry = { scope, callbacks: new Set() }; listeners.set(scope.key, entry); }
    entry.callbacks.add(listener);
    listener(stateFor(scope));
    return () => {
      entry.callbacks.delete(listener);
      if (!entry.callbacks.size) listeners.delete(scope.key);
    };
  }

  async function get(input) {
    if (input.signal?.aborted) throw input.signal.reason;
    const scope = prepare(input);
    if (!scope) throw cancelled();
    const cached = recordFor(scope);
    if (!input.refresh && cached) {
      if (!fresh(cached)) start(scope, false).catch(() => {});
      return cached.value;
    }
    return forConsumer(start(scope, input.refresh === true), input.signal);
  }

  return { get, peek, subscribe, invalidate };
}
