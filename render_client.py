"""Render backend dispatcher: local WSL-interop Rack, or render-service on Railway.

render() picks the backend based on RACK_RENDER_URL: unset keeps rendering
locally via render_patch.render() (the Windows/WSL-interop recipe); set,
it POSTs the parsed patch JSON to render-service's /render endpoint and
writes the returned WAV bytes to disk. audition.py always imports render()
from here, never from render_patch directly, so the dispatch is transparent
to callers.
"""

import importlib
from pathlib import Path

import requests

from config import AUDIO_DIR, RACK_RENDER_URL, RENDER_SECONDS, RENDER_TOKEN
from render_patch import RenderError, patch_slug
from render_patch import render as local_render

parse_vcv = importlib.import_module("03_parse_and_filter").parse_vcv


def select_backend(url):
    """Return "remote" when a render-service URL is configured, else "local"."""
    return "remote" if url else "local"


def remote_timeout(seconds):
    """(connect, read) timeout for a remote render of the given length —
    generous slack for cold start plus the real-time render itself."""
    return (10, 2 * seconds + 180)


def map_remote_error(status, text):
    """Translate an HTTP error from render-service into an actionable message."""
    if status == 401:
        return "render-service rejected the auth token (check RENDER_TOKEN)"
    if status == 413:
        return "patch too large for render-service (RENDER_MAX_PATCH_BYTES)"
    if status == 422:
        return f"render-service rejected the request: {text}"
    if status == 503:
        return "render-service is busy (another render in progress), try again"
    if status == 500:
        return f"render-service render failed: {text}"
    return f"render-service returned {status}: {text}"


def remote_render(vcv_path, out_path=None, seconds=RENDER_SECONDS):
    """Render one patch via the remote render-service. Returns the WAV path."""
    vcv_path = Path(vcv_path)
    patch = parse_vcv(vcv_path)
    name = patch_slug(vcv_path)

    resp = requests.post(
        f"{RACK_RENDER_URL}/render",
        params={"seconds": seconds},
        json=patch,
        headers={"Authorization": f"Bearer {RENDER_TOKEN}"},
        timeout=remote_timeout(seconds),
    )
    if resp.status_code != 200:
        raise RenderError(map_remote_error(resp.status_code, resp.text))

    if out_path is None:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        out_path = AUDIO_DIR / f"{name}.wav"
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    return out_path


def render(vcv_path, out_path=None, seconds=RENDER_SECONDS):
    """Render one patch, dispatching to the remote render-service when
    RACK_RENDER_URL is set, else to the local Windows/WSL-interop recipe."""
    if select_backend(RACK_RENDER_URL) == "remote":
        return remote_render(vcv_path, out_path=out_path, seconds=seconds)
    return local_render(vcv_path, out_path=out_path, seconds=seconds)
