"""FastAPI render service: takes a parsed .vcv patch as JSON, renders it
locally via render_patch.render() (native Linux Rack build baked into
this image), and returns WAV bytes. Bearer-token authenticated; a single
in-process lock serializes renders (one scratch user dir, one headless
Rack instance at a time).
"""

import json
import os
import threading
import uuid

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from config import RACK_HEADLESS_DIR, RENDER_MAX_PATCH_BYTES, RENDER_MAX_SECONDS
from plugin_sync import ensure_plugins
from render_patch import RenderError, render
from validation import auth_ok, clamp_seconds, validate_patch_request

TOKEN = os.environ["RENDER_TOKEN"]

app = FastAPI()
_render_lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/render")
async def render_endpoint(request: Request, seconds: str | None = None):
    if not auth_ok(request.headers.get("Authorization"), TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.body()
    if len(body) > RENDER_MAX_PATCH_BYTES:
        return JSONResponse({"error": "patch too large"}, status_code=413)

    try:
        patch = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    errors = validate_patch_request(patch)
    if errors:
        return JSONResponse({"error": "; ".join(errors)}, status_code=422)

    render_seconds = clamp_seconds(seconds, default=10, cap=RENDER_MAX_SECONDS)

    name = uuid.uuid4().hex
    incoming_dir = RACK_HEADLESS_DIR / "incoming"
    responses_dir = RACK_HEADLESS_DIR / "responses"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    incoming_patch = incoming_dir / f"{name}.vcv"
    response_wav = responses_dir / f"{name}.wav"
    incoming_patch.write_text(json.dumps(patch))

    if not _render_lock.acquire(timeout=180):
        incoming_patch.unlink(missing_ok=True)
        return JSONResponse({"error": "busy"}, status_code=503)

    try:
        # Install any non-bundled plugins this patch needs (lin-x64) before
        # launching Rack, which loads plugins at startup. Cached across
        # requests for the container's life.
        plug_slugs = sorted({m.get("plugin") for m in patch.get("modules", [])} - {"Core", None})
        sync = await run_in_threadpool(ensure_plugins, plug_slugs)
        if sync["missing"]:
            return JSONResponse(
                {"error": "missing-plugins: " + ",".join(sync["missing"])},
                status_code=422,
            )
        try:
            wav_path = await run_in_threadpool(
                render, incoming_patch, response_wav, render_seconds
            )
        except RenderError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return Response(content=wav_path.read_bytes(), media_type="audio/wav")
    finally:
        _render_lock.release()
        incoming_patch.unlink(missing_ok=True)
        response_wav.unlink(missing_ok=True)
