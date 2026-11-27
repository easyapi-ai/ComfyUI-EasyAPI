const PREFIX = "easyapi.promptModelPreferences.v1.";
const ROLES = new Set(["optimize", "tune", "analysis"]);

export function createPromptModelPreferences(storage = () => globalThis.localStorage) {
  const key = (profileId, role, field) => PREFIX + JSON.stringify([profileId, role, field]);
  const validScope = (profileId, role) => typeof profileId === "string" && profileId.length > 0 && ROLES.has(role);
  const validId = (id, role) => typeof id === "string" && (id !== "" || role === "analysis");

  function get(profileId, role) {
    if (!validScope(profileId, role)) return {};
    try {
      const store = storage();
      const fixed = store.getItem(key(profileId, role, "fixed"));
      const recent = store.getItem(key(profileId, role, "recent"));
      return {
        fixed: validId(fixed, role) ? fixed : undefined,
        recent: validId(recent, role) ? recent : undefined,
      };
    } catch {
      return {};
    }
  }

  function write(profileId, role, field, id) {
    if (!validScope(profileId, role) || (id !== undefined && !validId(id, role))) return false;
    try {
      const store = storage();
      const entry = key(profileId, role, field);
      if (id === undefined) store.removeItem(entry);
      else store.setItem(entry, id);
      return true;
    } catch {
      return false;
    }
  }

  return {
    get,
    setFixed: (profileId, role, id) => write(profileId, role, "fixed", id),
    remember: (profileId, role, id) => validId(id, role) && write(profileId, role, "recent", id),
    resolve(profileId, role, models, fallbacks = []) {
      const { fixed, recent } = get(profileId, role);
      const allowed = id => validId(id, role) && (id === "" || models.some(model => model.id === id));
      if (fixed !== undefined) return allowed(fixed) ? { value: fixed } : { value: "", unavailable: fixed };
      return { value: [recent, ...fallbacks].find(allowed) ?? "" };
    },
  };
}
