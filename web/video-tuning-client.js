export function createVideoTuningClient({ request, bootstrap, pollMs = 300 }) {
  async function call(path, options = {}) {
    const token = await bootstrap();
    return request(path, { ...options, headers: { "X-EasyAPI-Management": token,
      ...(typeof options.body === "string" ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  }

  const cancel = (id) => call("/easyapi/video-tuning/" + encodeURIComponent(id), { method: "DELETE" });
  async function observe(id, signal, onEvent) {
    const abort = () => {
      if (signal?.reason?.name !== "TuningViewDetached") cancel(id).catch(() => {});
    };
    signal?.addEventListener("abort", abort, { once: true });
    let cursor = 0;
    try {
      if (signal?.aborted) { abort(); throw signal.reason; }
      for (;;) {
        signal?.throwIfAborted();
        const state = await call("/easyapi/video-tuning/" + encodeURIComponent(id) + "?after=" + cursor, { signal });
        signal?.throwIfAborted();
        for (const event of state.events || []) onEvent?.(event);
        cursor = state.cursor ?? cursor;
        if (["completed", "success", "succeeded"].includes(state.state)) return state.result;
        if (["failed", "error", "cancelled", "canceled"].includes(state.state)) {
          throw new Error(typeof state.error === "string" ? state.error : state.error?.message || "Video tuning did not complete");
        }
        await new Promise((resolve, reject) => {
          const stopped = () => { clearTimeout(timer); signal.removeEventListener("abort", stopped); reject(signal.reason); };
          const timer = setTimeout(() => { signal?.removeEventListener("abort", stopped); resolve(); }, pollMs);
          signal?.addEventListener("abort", stopped, { once: true });
        });
      }
    } finally { signal?.removeEventListener("abort", abort); }
  }

  async function run(value, signal, onEvent, onStarted) {
    signal?.throwIfAborted();
    // Do not lose the run ID if cancellation races with the accepted POST.
    const state = await call("/easyapi/video-tuning", { method: "POST", body: JSON.stringify(value) });
    onStarted?.(state.run_id);
    return observe(state.run_id, signal, onEvent);
  }
  async function upload(file, signal) {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", file.type.startsWith("video/") || /\.(?:mp4|mov|webm|mkv|m4v)$/i.test(file.name) ? "video" : "image");
    return (await call("/easyapi/video-tuning/media", { method: "POST", body: form, signal })).media;
  }
  return { run, resume: observe, cancel, upload };
}
