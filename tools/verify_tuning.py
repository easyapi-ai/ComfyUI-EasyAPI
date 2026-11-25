"""Verify video tuning on real ComfyUI using only the isolated mock connection.

Run inside the ComfyUI container so the unexposed mock Docker hostname resolves.
The script never creates connections, reads keys, resets the mock ledger or invokes
models through another profile. It writes only its own verification report/media.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.cookiejar
import io
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


MOCK_GATEWAY = "http://comfytv-easyapi-mock:3199"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--ledger-url", default=MOCK_GATEWAY)
    parser.add_argument("--ca", default=None)
    parser.add_argument("--output", default="output/verification/video-tuning.json")
    args = parser.parse_args()
    if args.ledger_url.rstrip("/") != MOCK_GATEWAY:
        raise SystemExit("Refusing verification: the ledger must be the isolated mock Docker service.")
    base = args.url.rstrip("/")
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookie_jar),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=args.ca)))

    def request(path, body=None, headers=None, method=None, raw=False):
        url = path if path.startswith(("http://", "https://")) else base + path
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = ({"Content-Type": "application/json"} if payload is not None else {}) | (headers or {})
        req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
        try:
            with opener.open(req, timeout=30) as response:
                return response.read() if raw else json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{path}: HTTP {error.code}: {error.read().decode(errors='replace')[:2000]}") from error

    profiles = request("/easyapi/profiles").get("profiles", [])
    mock = next((profile for profile in profiles if profile.get("id") == "mock"), None)
    if not mock or mock.get("base_url") != MOCK_GATEWAY or not mock.get("has_key"):
        raise SystemExit("Refusing verification: create an explicitly configured mock connection to the isolated fixture first.")
    definitions = request("/object_info")
    tuner = definitions.get("EasyAPIVideoPromptTuner", {})
    if tuner.get("output") != ["STRING", "STRING", "EASYAPI_JSON"] or tuner.get("output_node"):
        raise SystemExit("The installed video prompt tuner must be an ordinary three-output node.")
    if definitions.get("EasyAPIVideo", {}).get("output") != ["VIDEO", "STRING", "EASYAPI_JSON"]:
        raise SystemExit("The installed video generator must expose video, URL and response.")

    def assert_idle():
        queue = request("/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("ComfyUI queue is busy; verification has not submitted another task.")

    def counts():
        ledger = request(args.ledger_url.rstrip("/") + "/_mock/ledger")
        rows = ledger.get("requests", [])
        return {"chat": sum(row.get("method") == "POST" and row.get("path") == "/v1/chat/completions" for row in rows),
                "video": sum(row.get("method") == "POST" and row.get("path") == "/v1/videos" for row in rows),
                "outbound": ledger.get("outbound_requests")}

    def node(kind, **overrides):
        inputs = {}

        def defaults(fields, prefix=""):
            for name, schema in fields.items():
                key = prefix + name
                options = schema[1] if len(schema) > 1 and isinstance(schema[1], dict) else {}
                if "default" in options:
                    inputs[key] = options["default"]
                elif isinstance(schema[0], list):
                    inputs[key] = schema[0][0]
                elif schema[0] == "COMBO" and options.get("options"):
                    inputs[key] = options["options"][0]
                elif schema[0] == "COMFY_DYNAMICCOMBO_V3" and options.get("options"):
                    branch = options["options"][0]
                    inputs[key] = branch["key"]
                    defaults({**branch["inputs"].get("required", {}), **branch["inputs"].get("optional", {})}, key + ".")

        defaults(definitions[kind]["input"].get("required", {}))
        inputs.update(overrides)
        return {"class_type": kind, "inputs": inputs}

    def run_graph(graph, targets=None):
        assert_idle()
        body = {"prompt": graph, "client_id": "easyapi-tuning-verification-" + uuid.uuid4().hex}
        if targets is not None:
            body["partial_execution_targets"] = targets
        prompt_id = request("/prompt", body)["prompt_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            history = request("/history/" + prompt_id).get(prompt_id)
            if history is not None:
                if history.get("status", {}).get("status_str") != "success":
                    raise RuntimeError("Mock tuning workflow failed: " + json.dumps(history.get("status", {}), ensure_ascii=False)[:2000])
                return prompt_id, history
            time.sleep(.15)
        raise TimeoutError("Mock tuning workflow did not finish in 180 seconds.")

    scope = "verify-tuning-" + uuid.uuid4().hex
    original = "A person reads one page beside a window."
    graph = {
        "1": node("EasyAPIVideoPromptTuner", profile_id="mock", text_model="mock-text", analysis_model="",
                  target_profile_id="mock", target_model="mock-video", target_params_json='{"duration":1}',
                  prompt=original, scope_id=scope, revision=0, domain="general", mode="auto", output="full", analyze=False, locale="en"),
        "2": node("EasyAPIVideo", profile_id="mock", model="mock-video", operation="video-generation",
                  prompt=["1", 0], params_json='{"duration":1}', mode="auto", wait_for_result=True),
        "3": node("SaveVideo", video=["2", 0], filename_prefix="EasyAPI-verify/tuning/" + scope),
        "4": node("EasyAPIPreview", text=["1", 0], json_value=["1", 2]),
        "5": node("EasyAPIVideoPromptTuner", profile_id="mock", text_model="mock-text", prompt="Unconnected tuner must never run.",
                  scope_id=scope + "-unconnected", revision=0, domain="general", analyze=False),
    }
    baseline = counts()
    report = {"url": base, "scope_id": scope, "steps": [], "ok": False}

    def record(name, expected_chat, expected_video, prompt_id=None):
        observed = counts()
        delta = {key: observed[key] - baseline[key] for key in ("chat", "video")}
        if delta != {"chat": expected_chat, "video": expected_video} or observed["outbound"] != 0:
            raise AssertionError(f"{name}: unexpected fixture calls {delta}; outbound={observed['outbound']}")
        report["steps"].append({"case": name, "prompt_id": prompt_id, "calls": delta})
        print(name, "PASS", delta, flush=True)

    try:
        prompt_id, history = run_graph(graph, ["4"])
        if "2" in history.get("outputs", {}) or "3" in history.get("outputs", {}):
            raise AssertionError("Preview-only execution returned downstream video outputs.")
        record("preview-only", 1, 0, prompt_id)
        prompt_id, _ = run_graph(graph, ["4"])
        record("identical-preview-reused", 1, 0, prompt_id)

        # A panel job with the exact runtime input must reuse the queued result.
        nonce = request("/easyapi/bootstrap")["management_nonce"]
        management = {"Origin": base, "X-EasyAPI-Management": nonce}
        payload = {"scope_id": scope, "nonce": 0, "prompt": original, "profile_id": "mock", "model": "mock-text",
                   "analysis_model": "", "target_profile_id": "mock", "target_model": "mock-video", "params": {"duration": 1},
                   "domain": "general", "mode": "auto", "output": "full", "analyze": False, "issue": None,
                   "media": [], "resultMedia": [], "locale": "en"}
        run_id = request("/easyapi/video-tuning", payload, headers=management)["run_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = request("/easyapi/video-tuning/" + run_id, headers=management)
            if state["state"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(.1)
        if state["state"] != "completed" or not state.get("result", {}).get("prompt"):
            raise AssertionError("Panel job did not reuse the complete tuning result.")
        record("panel-and-queue-share-result", 1, 0)

        prompt_id, history = run_graph(graph)
        record("generate-video-from-tuned-prompt", 1, 1, prompt_id)
        saved = [item for group in history.get("outputs", {}).get("3", {}).values() if isinstance(group, list)
                 for item in group if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith((".mp4", ".webm", ".mkv"))]
        if not saved:
            raise AssertionError("SaveVideo did not return a video file.")
        asset = saved[0]
        content = request("/view?" + urllib.parse.urlencode({"filename": asset["filename"], "subfolder": asset.get("subfolder", ""),
                                                           "type": asset.get("type", "output")}), raw=True)
        import av
        with av.open(io.BytesIO(content)) as video:
            frames = sum(1 for _ in video.decode(video=0))
        if frames <= 0:
            raise AssertionError("SaveVideo file contains no decodable frames.")
        report["video"] = {"bytes": len(content), "frames": frames, "sha256": hashlib.sha256(content).hexdigest()}
        prompt_id, _ = run_graph(graph)
        record("new-video-does-not-retune", 1, 2, prompt_id)
        graph = copy.deepcopy(graph)
        graph["1"]["inputs"]["revision"] = 1
        prompt_id, _ = run_graph(graph, ["4"])
        record("explicit-revision-retunes-only", 2, 2, prompt_id)
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)[:2000]
        raise
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
