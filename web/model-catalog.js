const STORAGE_KEY = "easyapi.modelCatalog.v1";
const MAX_AGE_MS = 120_000;
const MAX_ENTRIES = 24;
const MAX_STORAGE_LENGTH = 400_000;

function forConsumer(promise, signal) {
  if (!signal) return promise;
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
    return [{
      id,
      name: typeof model.name === "string" ? model.name : undefined,
      display_name: typeof model.display_name === "string" ? model.display_name : undefined,
      capability_status: typeof model.capability_status === "string" ? model.capability_status : undefined,
    }];
  });
  return { models, empty: !models.length, authority: "api_key", operation: value.operation, locale: value.locale };
}

export function createModelCatalog(request, { now = Date.now, storage = () => globalThis.localStorage } = {}) {
  const memory = new Map();
  const pending = new Map();
  const fresh = (record) => {
    const remaining = record?.expiresAt - now();
    return remaining > 0 && remaining <= MAX_AGE_MS;
  };

  function readStorage() {
    try {
      const raw = storage()?.getItem(STORAGE_KEY);
      if (!raw || raw.length > MAX_STORAGE_LENGTH) return [];
      const records = JSON.parse(raw);
      return Array.isArray(records) ? records.filter((record) => fresh(record) &&
        typeof record.key === "string" && typeof record.profileId === "string" && Array.isArray(record.value?.models)) : [];
    } catch { return []; }
  }

  function writeStorage(records) {
    try {
      const bounded = records.filter(fresh).slice(0, MAX_ENTRIES);
      let raw = JSON.stringify(bounded);
      while (raw.length > MAX_STORAGE_LENGTH) {
        bounded.pop();
        raw = JSON.stringify(bounded);
      }
      if (bounded.length) storage()?.setItem(STORAGE_KEY, raw);
      else storage()?.removeItem(STORAGE_KEY);
    } catch { /* Storage can be unavailable or reach browser quota. */ }
  }

  function invalidate(profileId) {
    if (!profileId) return;
    for (const cache of [memory, pending]) {
      for (const [key, record] of cache) if (record.profileId === profileId) cache.delete(key);
    }
    writeStorage(readStorage().filter((record) => record.profileId !== profileId));
  }

  function remember(key, profileId, revision, value, startedAt) {
    const ttl = Number.isFinite(value?.cache_ttl) ? value.cache_ttl * 1000 : MAX_AGE_MS;
    const expiresAt = Math.min(startedAt + MAX_AGE_MS, now() + ttl);
    const record = { key, profileId, value, expiresAt };
    if (!fresh(record)) return;
    memory.set(key, record);
    if (memory.size > MAX_ENTRIES) memory.delete(memory.keys().next().value);
    if (!revision) return;
    const small = storageResult(value);
    if (small) writeStorage([{ ...record, value: small }, ...readStorage().filter((entry) => entry.key !== key)]);
  }

  async function get({ profileId, family = "", operation = "", locale = "", revision = "", refresh = false, signal }) {
    if (signal?.aborted) throw signal.reason;
    const key = JSON.stringify([profileId, revision, family, operation, locale]);
    const inflight = pending.get(key);
    if (inflight && (!refresh || inflight.refresh)) return forConsumer(inflight.promise, signal);
    if (refresh) invalidate(profileId);
    else {
      const cached = memory.get(key);
      if (fresh(cached)) return cached.value;
      memory.delete(key);
      const stored = revision && readStorage().find((record) => record.key === key);
      if (stored) return stored.value;
    }
    const startedAt = now();
    const record = { profileId, refresh };
    const query = new URLSearchParams({ profile_id: profileId, family, operation, locale, refresh: refresh ? "1" : "0" });
    record.promise = Promise.resolve().then(() => request(`/easyapi/models?${query}`)).then((value) => {
      if (pending.get(key) === record) remember(key, profileId, revision, value, startedAt);
      return value;
    }, (error) => {
      if (pending.get(key) === record && (error.status === 401 || error.status === 403)) invalidate(profileId);
      throw error;
    }).finally(() => {
      if (pending.get(key) === record) pending.delete(key);
    });
    pending.set(key, record);
    return forConsumer(record.promise, signal);
  }

  return { get, invalidate };
}
