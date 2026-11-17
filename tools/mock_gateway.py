"""Offline EasyAPI protocol fixture server. It never makes outbound requests.

Run: python tools/mock_gateway.py --host 0.0.0.0 --port 3199
Only the literal API key ``mock-only-key`` is accepted. Do not put real keys here.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import struct
import time
import uuid
import wave
from pathlib import Path

from aiohttp import web, WSMsgType

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1kAAAAASUVORK5CYII=")


def audio_bytes() -> bytes:
    pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / 24000))) for i in range(4800))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return out.getvalue()


def video_bytes() -> bytes:
    import av
    import numpy as np
    out = io.BytesIO()
    with av.open(out, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=4)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for index in range(4):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            frame[:, :, index % 3] = 80 + index * 40
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return out.getvalue()


MODELS = [("mock-text", "text"), ("mock-image", "image"), ("mock-video", "video"),
          ("mock-audio", "audio"), ("mock-realtime", "audio"), ("mock-music", "audio"), ("mock-mj", "image")]


def create_app() -> web.Application:
    app = web.Application(client_max_size=128 * 1024 * 1024)
    app["ledger"] = []
    app["jobs"] = {}
    app["assets"] = {}
    app["video"] = None

    @web.middleware
    async def guard(request, handler):
        if request.path.startswith(("/v1/", "/v1beta/", "/mj/", "/suno/", "/kling/", "/seedance/", "/jimeng/", "/dashscope/", "/usage/", "/fast/mj/", "/relax/mj/", "/turbo/mj/")):
            if request.headers.get("Authorization") != "Bearer mock-only-key":
                return web.json_response({"error": {"message": "Mock requires mock-only-key", "type": "authentication_error"}}, status=401)
        if not request.path.startswith("/_mock/"):
            entry = {"method": request.method, "path": request.path, "at": time.time()}
            app["ledger"].append(entry)
            request["ledger_entry"] = entry
        return await handler(request)
    app.middlewares.append(guard)

    def origin(request):
        return f"{request.scheme}://{request.host}"

    async def parse(request):
        files, body = [], {}
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            async for part in reader:
                if part.filename:
                    size = 0
                    while chunk := await part.read_chunk():
                        size += len(chunk)
                    files.append({"field": part.name, "filename": part.filename, "size": size, "mime": part.headers.get("Content-Type")})
                else:
                    value = await part.text()
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                    body[part.name] = value
        elif request.can_read_body:
            try:
                body = await request.json()
            except (ValueError, UnicodeError):
                body = {"raw": (await request.read()).decode("utf-8", "replace")}
        request["ledger_entry"].update(body=body, files=files)
        return body, files

    async def ledger(request):
        return web.json_response({"requests": app["ledger"], "jobs": len(app["jobs"]), "outbound_requests": 0})

    async def reset(request):
        app["ledger"].clear()
        app["jobs"].clear()
        return web.json_response({"success": True})

    async def models(request):
        rows = [{"id": name, "name": name, "object": "model", "owned_by": "offline-fixture", "category": family,
                 "architecture": {"input_modalities": ["text", "image", "audio", "video"], "output_modalities": [family]},
                 "supported_endpoint_types": {"text": ["openai", "anthropic", "gemini", "responses"], "image": ["image-generation", "image-edit", "image-task"], "video": ["video"], "audio": ["audio", "audio-speech", "realtime"]}[family],
                 "supported_parameters": ["prompt", "seed", "n", "size", "duration"], "pricing": {"prompt": "0", "completion": "0"}} for name, family in MODELS]
        return web.json_response({"object": "list", "data": rows})

    async def pricing(request):
        return web.json_response({"success": True, "data": [{"model_name": name, "category": family} for name, family in MODELS], "pricing_version": "mock/1"})

    async def schema(request):
        name = request.query.get("model_name", "mock-text")
        family = dict(MODELS).get(name, "text")
        properties = {"seed": {"type": "integer", "minimum": 0, "default": 0}, "n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                      "watermark": {"type": "boolean", "default": False}, "size": {"type": "string", "enum": ["64x64", "128x128"]},
                      "duration": {"type": "integer", "enum": [1, 4]}, "input_references": {"type": "array", "items": {"type": "object", "properties": {"type": {"enum": ["image", "audio", "video"]}, "role": {"enum": ["first_frame", "last_frame", "reference_image", "reference_video", "reference_audio"]}, "url": {"type": "string"}}}}}
        if family == "audio":
            properties = {"voice": {"type": "string", "enum": ["Cherry", "Serena"], "default": "Cherry"},
                          "instructions": {"type": "string", "description": "对话或语音的风格要求"},
                          "speech_rate": {"type": "number", "minimum": .5, "maximum": 2, "default": 1},
                          "turn_detection": {"description": "server_vad 或 null（手动模式）", "anyOf": [{"type": "object"}, {"type": "string"}, {"type": "null"}]}}
        elif family == "image":
            properties = {key: val for key, val in properties.items() if key in {"seed", "n", "watermark", "size"}}
        elif family == "video":
            properties = {key: val for key, val in properties.items() if key in {"seed", "duration", "input_references"}}
        else:
            properties = {"temperature": {"type": "number", "minimum": 0, "maximum": 2},
                          "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096},
                          "stream": {"type": "boolean"}}
        spec = {"type": "object", "properties": properties, "additionalProperties": True}
        if name == "mock-audio":
            spec["required"] = ["input", "voice"]
        entries = {"text": [("openai", "/v1/chat/completions", "json"), ("responses", "/v1/responses", "json"), ("anthropic", "/v1/messages", "json"), ("gemini", "/v1beta/models/{model}:generateContent", "json")],
                   "image": [("image-generation", "/v1/images/generations", "json"), ("image-edit", "/v1/images/edits", "multipart"), ("image-task", "/v1/images/tasks", "json")],
                   "video": [("video", "/v1/videos", "multipart")], "audio": [("audio-speech", "/v1/audio/speech", "json"), ("realtime", "/v1/realtime", "websocket")]}
        endpoints = [{"key": key, "path": path, "method": "POST", "transport": transport, "request_schema": spec, "variants": []} for key, path, transport in entries[family]]
        data = {"model": name, "category": family, "docs_version": "model-docs/1", "endpoints": endpoints, "params_declared": True, "request_schema": spec, "params": {family: spec}}
        return web.json_response({"success": True, "data": data, "pricing_version": "mock/1"})

    async def fixture(request):
        name = request.match_info.get("name", "image.png")
        if name.endswith(".png"):
            return web.Response(body=PNG, content_type="image/png")
        if name.endswith(".wav"):
            return web.Response(body=audio_bytes(), content_type="audio/wav")
        if name.endswith(".mp4"):
            if app["video"] is None:
                app["video"] = video_bytes()
            return web.Response(body=app["video"], content_type="video/mp4")
        raise web.HTTPNotFound()

    async def text(request):
        body, _ = await parse(request)
        if "count_tokens" in request.path:
            return web.json_response({"input_tokens": 12})
        if body.get("stream") or "streamGenerateContent" in request.path:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            events = [{"choices": [{"delta": {"content": "离线"}}]}, {"choices": [{"delta": {"content": "验证成功"}}]}, {"usage": {"prompt_tokens": 2, "completion_tokens": 3}}]
            for event in events:
                encoded = ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                # Deliberately split multibyte UTF-8 and SSE records.
                for start in range(0, len(encoded), 7):
                    await response.write(encoded[start:start + 7])
            await response.write(b"data: [DONE]\n\n")
            return response
        if "/messages" in request.path:
            result = {"id": "msg_mock", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "离线验证成功"}], "usage": {"input_tokens": 2, "output_tokens": 3}}
        elif "/responses" in request.path:
            result = {"id": "resp_mock", "object": "response", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "离线验证成功"}]}]}
        elif "/models/" in request.path:
            result = {"candidates": [{"content": {"role": "model", "parts": [{"text": "离线验证成功"}]}}]}
        else:
            result = {"id": "chat_mock", "object": "chat.completion", "choices": [{"index": 0, "text": "离线验证成功", "message": {"role": "assistant", "content": "离线验证成功"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
        return web.json_response(result, headers={"X-EasyAPI-Request-Id": "req_mock"})

    async def image(request):
        body, files = await parse(request)
        if request.path.endswith("/edits") and not files and not body.get("image") and not body.get("images"):
            return web.json_response({"error": {"message": "image file required"}}, status=400)
        count = int(body.get("n", 1))
        if body.get("response_format") == "b64_json":
            data = [{"b64_json": base64.b64encode(PNG).decode()} for _ in range(count)]
        else:
            data = [{"url": origin(request) + "/fixtures/image.png"} for _ in range(count)]
        return web.json_response({"created": int(time.time()), "data": data}, headers={"X-EasyAPI-Request-Id": "req_mock_image"})

    async def submit(request):
        body, files = await parse(request)
        family = "image" if "images/tasks" in request.path else "music" if "/suno/" in request.path else "mj" if "/mj/" in request.path else "video"
        task_id = f"mock_{family}_{uuid.uuid4().hex[:12]}"
        app["jobs"][task_id] = {"family": family, "polls": 0, "fail": "[fail]" in json.dumps(body), "body": body, "files": files}
        if family == "mj":
            return web.json_response({"code": 1, "description": "Submit success", "result": task_id})
        if family == "music":
            return web.json_response({"code": "success", "data": task_id})
        if family == "image":
            return web.json_response({"code": "success", "data": {"task_id": task_id, "status": "IN_PROGRESS"}})
        if request.path.startswith("/seedance/"):
            return web.json_response({"id": task_id, "status": "queued"})
        if request.path.startswith("/jimeng/"):
            return web.json_response({"code": 10000, "data": {"task_id": task_id}})
        if request.path.startswith("/dashscope/"):
            return web.json_response({"output": {"task_id": task_id, "task_status": "PENDING"}})
        return web.json_response({"id": task_id, "task_id": task_id, "status": "queued", "code": "success", "data": {"task_id": task_id, "job_id": task_id}})

    async def poll(request):
        body = {}
        if request.method == "POST":
            body, _ = await parse(request)
        task_id = request.match_info.get("task_id") or body.get("task_id") or body.get("id") or next(iter(body.get("ids", [])), "")
        if not task_id:
            return web.json_response({"code": "success", "data": list(app["jobs"])})
        job = app["jobs"].get(task_id)
        if job is None:
            return web.json_response({"error": {"message": "Task not found"}}, status=404)
        job["polls"] += 1
        done = job["polls"] >= 2
        state = "failed" if job["fail"] else "completed" if done else "in_progress"
        base = origin(request)
        if job["family"] == "image":
            return web.json_response({"code": "success", "data": {"task_id": task_id, "status": "FAILURE" if job["fail"] else "SUCCESS" if done else "IN_PROGRESS", "progress": "100%" if done else "50%", "fail_reason": "Fixture failure" if job["fail"] else "", "images": [{"url": base + "/fixtures/image.png"}] if done else []}})
        if job["family"] == "mj":
            return web.json_response({"id": task_id, "status": "FAILURE" if job["fail"] else "SUCCESS" if done else "IN_PROGRESS", "progress": "100%" if done else "50%", "imageUrl": base + "/fixtures/image.png" if done else "", "buttons": [{"customId": "mock_upscale_1", "label": "U1"}]})
        if job["family"] == "music":
            return web.json_response({"code": "success", "data": {"task_id": task_id, "status": "FAILURE" if job["fail"] else "SUCCESS" if done else "IN_PROGRESS", "data": [{"id": task_id, "audio_url": base + "/fixtures/audio.wav", "image_url": base + "/fixtures/image.png", "status": "complete"}] if done else []}})
        data = {"job_id": task_id, "task_id": task_id, "status": state, "progress": "100%" if done else "50%", "download_url": base + "/fixtures/video.mp4" if done else "", "video_url": base + "/fixtures/video.mp4" if done else "", "fail_reason": "Fixture failure" if job["fail"] else ""}
        if request.path.startswith("/dashscope/"):
            return web.json_response({"output": {"task_id": task_id, "task_status": "SUCCEEDED" if done else "RUNNING", "video_url": base + "/fixtures/video.mp4" if done else ""}})
        if request.path.startswith("/seedance/"):
            return web.json_response({"id": task_id, "status": "succeeded" if done else "running", "content": {"video_url": base + "/fixtures/video.mp4"} if done else {}})
        if request.path.startswith("/kling/"):
            return web.json_response({"code": 0, "data": {"task_id": task_id, "task_status": "succeed" if done else "processing", "task_result": {"videos": [{"url": base + "/fixtures/video.mp4"}]} if done else {}}})
        return web.json_response({"code": 200 if "/videos/" in request.path else "success", "msg": "", "data": data})

    async def jimeng(request):
        return await poll(request) if "GetResult" in request.query.get("Action", "") else await submit(request)

    async def speech(request):
        body, _ = await parse(request)
        if body.get("response_format") == "wav" or request.headers.get("Accept", "").startswith("audio/"):
            return web.Response(body=audio_bytes(), content_type="audio/wav")
        return web.json_response({"audio": {"url": origin(request) + "/fixtures/audio.wav", "format": "wav"}, "text": body.get("input", "离线语音"), "usage": {"input_tokens": 1, "output_tokens": 1}})

    async def transcription(request):
        body, files = await parse(request)
        if not files:
            return web.json_response({"error": {"message": "file required"}}, status=400)
        if body.get("response_format") == "text":
            return web.Response(text="离线转录成功")
        return web.json_response({"text": "离线转录成功"})

    async def simple(request):
        body, _ = await parse(request) if request.method == "POST" else ({}, [])
        if "embeddings" in request.path or "embedContent" in request.path:
            return web.json_response({"data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}]})
        if "rerank" in request.path:
            return web.json_response({"results": [{"index": 0, "relevance_score": 0.99}]})
        if "moderation" in request.path:
            return web.json_response({"results": [{"flagged": False, "categories": {}}]})
        if "/alpha/search" in request.path:
            return web.json_response({"results": [{"title": "Offline fixture", "url": "https://example.invalid/fixture", "snippet": "No outbound request"}]})
        if "/generation" in request.path:
            return web.json_response({"data": {"id": "req_mock", "total_cost": 0}})
        return web.json_response({"data": {"total_granted": 1000000, "total_used": 0, "total_available": 1000000}, "success": True})

    async def upload(request):
        _, files = await parse(request)
        if len(files) != 1:
            return web.json_response({"error": {"message": "one file required"}}, status=400)
        asset = {"id": "media_" + uuid.uuid4().hex, "object": "media", "url": origin(request) + "/fixtures/" + files[0]["filename"], "mime_type": files[0]["mime"], "size": files[0]["size"], "created_at": int(time.time()), "expires_at": int(time.time()) + 72 * 3600}
        app["assets"][asset["id"]] = asset
        return web.json_response(asset)

    async def asset(request):
        media_id = request.match_info["media_id"]
        if media_id not in app["assets"]:
            raise web.HTTPNotFound()
        if request.method == "DELETE":
            del app["assets"][media_id]
            return web.json_response({"id": media_id, "deleted": True})
        return web.json_response(app["assets"][media_id])

    async def realtime(request):
        ws = web.WebSocketResponse(max_msg_size=2 * 1024 * 1024)
        await ws.prepare(request)
        await ws.send_json({"type": "session.created", "session": {"id": "sess_mock"}})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            kind = event.get("type")
            if kind == "session.update":
                await ws.send_json({"type": "session.updated", "session": event.get("session", {})})
            if kind == "input_audio_buffer.append":
                # VAD-style response is emitted periodically, without an external model.
                request.app["ledger"].append({"method": "WS", "path": "/v1/realtime", "type": kind, "audio_bytes": len(event.get("audio", ""))})
            if kind in {"response.create", "input_audio_buffer.commit"}:
                await ws.send_json({"type": "response.created", "response": {"id": "response_mock"}})
                await ws.send_json({"type": "conversation.item.input_audio_transcription.completed", "transcript": "麦克风模拟转录"})
                await ws.send_json({"type": "response.audio_transcript.delta", "delta": "离线实时语音验证成功"})
                pcm = audio_bytes()[44:]
                await ws.send_json({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()})
                await ws.send_json({"type": "response.done", "response": {"id": "response_mock", "status": "completed", "usage": {"total_tokens": 0}}})
            if kind == "response.cancel":
                await ws.send_json({"type": "response.done", "response": {"status": "cancelled"}})
        return ws

    app.router.add_get("/_mock/ledger", ledger)
    app.router.add_post("/_mock/reset", reset)
    app.router.add_get("/fixtures/{name}", fixture)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/v1/models/{model}", models)
    app.router.add_get("/v1beta/models", models)
    app.router.add_get("/v1beta/openai/models", models)
    app.router.add_get("/pricing", pricing)
    for path in ("/pricing/docs", "/pricing/schema", "/pricing/detail"):
        app.router.add_get(path, schema)
    for path in ("/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/responses/compact", "/v1/messages", "/v1/messages/count_tokens", "/v1beta/models/{model_action}", "/v1/models/{model_action}"):
        app.router.add_post(path, text)
    for path in ("/v1/images/generations", "/v1/images/edits", "/v1/edits"):
        app.router.add_post(path, image)
    for path in ("/v1/images/tasks", "/v1/videos", "/v1/video/generations", "/v1/videos/{video_id}/remix", "/kling/v1/videos/text2video", "/kling/v1/videos/image2video", "/seedance/api/v3/contents/generations/tasks", "/dashscope/api/v1/services/aigc/video-generation/video-synthesis", "/suno/submit/{action}"):
        app.router.add_post(path, submit)
    for path in ("/v1/images/tasks/{task_id}", "/v1/videos/{task_id}", "/v1/video/generations/{task_id}", "/kling/v1/videos/text2video/{task_id}", "/kling/v1/videos/image2video/{task_id}", "/seedance/api/v3/contents/generations/tasks/{task_id}", "/dashscope/api/v1/tasks/{task_id}", "/suno/fetch/{task_id}"):
        app.router.add_get(path, poll)
    app.router.add_post("/v1/images/tasks/{task_id}", poll)
    app.router.add_get("/seedance/api/v3/contents/generations/tasks", poll)
    app.router.add_post("/suno/fetch", poll)
    for prefix in ("/mj", "/fast/mj", "/relax/mj", "/turbo/mj"):
        for action in ("action", "shorten", "modal", "imagine", "change", "simple-change", "describe", "blend", "edits", "video", "upload-discord-images"):
            app.router.add_post(prefix + "/submit/" + action, submit)
        app.router.add_post(prefix + "/insight-face/swap", submit)
        app.router.add_get(prefix + "/task/{task_id}/fetch", poll)
        app.router.add_get(prefix + "/task/{task_id}/image-seed", simple)
        app.router.add_post(prefix + "/task/list-by-condition", poll)
        app.router.add_get(prefix + "/image/{task_id}", fixture)
    app.router.add_post("/jimeng/", jimeng)
    app.router.add_post("/v1/audio/speech", speech)
    for path in ("/v1/audio/transcriptions", "/v1/audio/translations"):
        app.router.add_post(path, transcription)
    for path in ("/v1/embeddings", "/v1/rerank", "/v1/moderations", "/v1/alpha/search", "/v1/engines/{model}/embeddings"):
        app.router.add_post(path, simple)
    for action in ("remove-watermark", "remove-background", "upscale", "image-text"):
        app.router.add_post("/v1/tools/" + action, image)
    app.router.add_post("/v1/tools/motion-video", submit)
    app.router.add_get("/v1/generation", simple)
    app.router.add_get("/usage/token", simple)
    app.router.add_post("/v1/media", upload)
    app.router.add_get("/v1/media/{media_id}", asset)
    app.router.add_delete("/v1/media/{media_id}", asset)
    app.router.add_get("/v1/realtime", realtime)
    async def content(request):
        if app["video"] is None:
            app["video"] = video_bytes()
        return web.Response(body=app["video"], content_type="video/mp4")
    app.router.add_get("/v1/videos/{task_id}/content", content)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3199)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, print=lambda message: print(message, flush=True))
