"""FastAPI render service: takes a parsed .vcv patch as JSON, renders it
locally via render_patch.render() (native Linux Rack build baked into
this image), and returns WAV bytes. Bearer-token authenticated; a single
in-process lock serializes renders (one scratch user dir, one headless
Rack instance at a time).
"""

import json
import os
import shutil
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

# Plugins baked into the image; everything else is downloaded per patch and
# pruned afterward. Rack loads *every* plugin in this dir at startup, so
# letting downloads accumulate makes startup slower and slower until renders
# time out — keep the set to bundled + the current patch only.
_KEEP_PLUGINS = {"Fundamental", "VCV-Recorder"}

app = FastAPI()
_render_lock = threading.Lock()


def prune_downloaded_plugins():
    """Remove all but the bundled plugins from the Rack plugin dir, so the
    next render's Rack startup only loads what that patch needs."""
    pdir = RACK_HEADLESS_DIR / "plugins-lin-x64"
    if not pdir.exists():
        return
    for entry in pdir.iterdir():
        if entry.name in _KEEP_PLUGINS:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:  # leftover .vcvplugin packages
            entry.unlink(missing_ok=True)


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
        # Clear the previous patch's downloaded plugins so Rack startup stays
        # fast (it loads every plugin in the dir), then install this patch's.
        await run_in_threadpool(prune_downloaded_plugins)
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
