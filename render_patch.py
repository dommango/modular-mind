"""Render .vcv patches to WAV via headless VCV Rack.

Recipe (verified 2026-07 on WSL2 + Rack 2.6.6 Windows build over interop):
  1. Inject a VCV Recorder into the patch, teeing the cables that feed
     Core:AudioInterface inputs 0/1 into Recorder LEFT/RIGHT. Recorder's
     output path, format and arm state live in its module JSON.
  2. Inject a Fundamental LFO whose square output (high for the first half
     period, +5V >= Recorder's 2V gate threshold) drives Recorder GATE:
     recording runs for exactly RENDER_SECONDS of engine time, then the
     gate falls and Recorder finalizes the WAV mid-run.
  3. Run `Rack.exe -h -u <scratch user dir> <patch>`. Headless Rack blocks
     on stdin ("Press enter to exit.") — keep stdin open while polling for
     the finished WAV, then close stdin for a clean shutdown. With no audio
     device the engine free-runs in real time on its fallback thread.

Recorder port IDs (from VCV-Recorder v2 source, data/repos/VCV-Recorder):
  params 0=Gain 1=Rec | inputs 0=Gate 1=Trig 2=Left 3=Right

Usage:
  python3 render_patch.py <patch.vcv> [more.vcv ...]
  python3 render_patch.py data/generated/          # all .vcv, recursive
"""

import argparse
import importlib
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

from config import (
    AUDIO_DIR,
    RACK_BINARY,
    RACK_HEADLESS_DIR,
    RACK_HEADLESS_DIR_WIN,
    RENDER_SAMPLE_RATE,
    RENDER_SECONDS,
    RENDER_STARTUP_TIMEOUT,
)

parse_vcv = importlib.import_module("03_parse_and_filter").parse_vcv

AUDIO_INTERFACE_MODELS = {"AudioInterface", "AudioInterface2", "AudioInterface16"}

# Recorder input port IDs (VCV-Recorder source enum InputIds)
REC_GATE_INPUT = 0
REC_LEFT_INPUT = 2
REC_RIGHT_INPUT = 3
# Fundamental LFO: param 2 = frequency (2^v Hz), output 3 = square
LFO_FREQ_PARAM = 2
LFO_SQR_OUTPUT = 3


class RenderError(Exception):
    pass


def find_audio_feeds(patch):
    """Return {audio_input_id: (outputModuleId, outputId)} for cables feeding
    the left/right inputs (0/1) of any Core audio interface module."""
    audio_ids = {
        m["id"]
        for m in patch.get("modules", [])
        if m.get("plugin") == "Core" and m.get("model") in AUDIO_INTERFACE_MODELS
    }
    feeds = {}
    for cable in patch.get("cables", []):
        if cable.get("inputModuleId") in audio_ids and cable.get("inputId") in (0, 1):
            feeds.setdefault(cable["inputId"], (cable["outputModuleId"], cable["outputId"]))
    return feeds


def gate_lfo_freq_param(seconds):
    """LFO frequency param value whose square wave stays high for `seconds`."""
    return math.log2(1.0 / (2.0 * seconds))


def inject_recorder(patch, wav_path_win, seconds=RENDER_SECONDS):
    """Return a new patch dict with a Recorder + gate LFO wired in.

    Tees the audio-interface feeds into the Recorder, so the original
    signal path is untouched. Raises RenderError if the patch feeds no
    audio interface.
    """
    feeds = find_audio_feeds(patch)
    if not feeds:
        raise RenderError("patch has no cable into an audio interface (inputs 0/1)")

    module_ids = [m["id"] for m in patch.get("modules", [])]
    cable_ids = [c.get("id", 0) for c in patch.get("cables", [])]
    recorder_id = max(module_ids) + 1
    lfo_id = recorder_id + 1
    next_cable_id = max(cable_ids, default=0) + 1

    recorder = {
        "id": recorder_id,
        "plugin": "VCV-Recorder",
        "model": "Recorder",
        "version": "2.0.3",
        "params": [{"id": 0, "value": 1.0}, {"id": 1, "value": 0.0}],
        "pos": [0, 2],
        "data": {
            "format": "wav",
            "path": wav_path_win,
            "incrementPath": False,
            "sampleRate": RENDER_SAMPLE_RATE,
            "depth": 16,
            "bitRate": 256000,
        },
    }
    gate_lfo = {
        "id": lfo_id,
        "plugin": "Fundamental",
        "model": "LFO",
        "version": "2.0.0",
        "params": [{"id": LFO_FREQ_PARAM, "value": gate_lfo_freq_param(seconds)}],
        "pos": [20, 2],
    }

    new_cables = [
        {
            "id": next_cable_id,
            "outputModuleId": lfo_id,
            "outputId": LFO_SQR_OUTPUT,
            "inputModuleId": recorder_id,
            "inputId": REC_GATE_INPUT,
        }
    ]
    left = feeds.get(0) or feeds.get(1)
    new_cables.append(
        {
            "id": next_cable_id + 1,
            "outputModuleId": left[0],
            "outputId": left[1],
            "inputModuleId": recorder_id,
            "inputId": REC_LEFT_INPUT,
        }
    )
    right = feeds.get(1)
    if right is not None and right != left:
        new_cables.append(
            {
                "id": next_cable_id + 2,
                "outputModuleId": right[0],
                "outputId": right[1],
                "inputModuleId": recorder_id,
                "inputId": REC_RIGHT_INPUT,
            }
        )

    return {
        **patch,
        "modules": list(patch.get("modules", [])) + [recorder, gate_lfo],
        "cables": list(patch.get("cables", [])) + new_cables,
    }


def _wait_for_wav(wav_path, min_bytes, deadline):
    """Poll until the WAV stops growing at a plausible size, or deadline."""
    last_size = -1
    stable = 0
    while time.monotonic() < deadline:
        size = wav_path.stat().st_size if wav_path.exists() else 0
        if size >= min_bytes and size == last_size:
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
        last_size = size
        time.sleep(0.5)
    return False


def render(vcv_path, out_path=None, seconds=RENDER_SECONDS):
    """Render one patch to a WAV under data/audio/. Returns the WAV path."""
    vcv_path = Path(vcv_path)
    if not RACK_BINARY.exists():
        raise RenderError(f"Rack binary not found: {RACK_BINARY}")

    patch = parse_vcv(vcv_path)
    name = vcv_path.stem

    scratch_patches = RACK_HEADLESS_DIR / "patches"
    scratch_out = RACK_HEADLESS_DIR / "out"
    scratch_patches.mkdir(parents=True, exist_ok=True)
    scratch_out.mkdir(parents=True, exist_ok=True)

    wav_win = f"{RACK_HEADLESS_DIR_WIN}/out/{name}.wav"
    scratch_wav = scratch_out / f"{name}.wav"
    scratch_wav.unlink(missing_ok=True)

    injected = inject_recorder(patch, wav_win, seconds)
    tmp_patch = scratch_patches / f"{name}.vcv"
    tmp_patch.write_text(json.dumps(injected))

    userdir_win = RACK_HEADLESS_DIR_WIN.replace("/", "\\")
    patch_win = f"{RACK_HEADLESS_DIR_WIN}/patches/{name}.vcv".replace("/", "\\")
    proc = subprocess.Popen(
        [str(RACK_BINARY), "-h", "-u", userdir_win, patch_win],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=RACK_BINARY.parent,
    )
    try:
        # 16-bit mono lower bound; the header adds a little on top
        min_bytes = seconds * RENDER_SAMPLE_RATE * 2
        deadline = time.monotonic() + seconds + RENDER_STARTUP_TIMEOUT
        done = _wait_for_wav(scratch_wav, min_bytes, deadline)
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=30)
        except Exception:
            proc.kill()

    if not done:
        raise RenderError(f"render timed out or produced no/short WAV: {name}")

    if out_path is None:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        out_path = AUDIO_DIR / f"{name}.wav"
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(scratch_wav, out_path)
    return out_path


def collect_patches(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.vcv")))
        else:
            files.append(p)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help=".vcv files or directories")
    parser.add_argument(
        "--seconds", type=int, default=RENDER_SECONDS, help="render length"
    )
    args = parser.parse_args()

    files = collect_patches(args.paths)
    if not files:
        print("No .vcv files found")
        return 1

    failures = 0
    for f in files:
        try:
            wav = render(f, seconds=args.seconds)
            print(f"OK    {f} -> {wav}")
        except (RenderError, ValueError, OSError) as e:
            failures += 1
            print(f"FAIL  {f}: {e}")
    print(f"\n{len(files) - failures}/{len(files)} rendered")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
