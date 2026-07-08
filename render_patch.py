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
    DATA_DIR,
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


def patch_slug(vcv_path):
    """Collision-resistant output name: subpath under data/<top>/ joined
    with '-' (batch3/01-drone.vcv -> batch3-01-drone), else the bare stem."""
    p = Path(vcv_path).resolve()
    try:
        rel = p.relative_to(DATA_DIR.resolve())
    except ValueError:
        return p.stem
    parts = rel.with_suffix("").parts
    return "-".join(parts[1:]) if len(parts) > 1 else rel.stem


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


def inject_recorder(patch, wav_path, seconds=RENDER_SECONDS):
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
            "path": wav_path,
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


def rack_is_windows():
    """True when RACK_BINARY points at the Windows build (driven over WSL
    interop); False for a native Linux build."""
    return RACK_BINARY.suffix.lower() == ".exe"


def rack_invocation(name):
    """Return (recorder_wav_path, userdir_arg, patch_arg) for the current
    platform's Rack binary.

    recorder_wav_path is written into the Recorder module's JSON, so it must
    already be in the path convention of the OS actually running Rack.
    userdir_arg/patch_arg are the -u/patch CLI arguments in that same
    convention. The Windows form drives Rack.exe over WSL interop, so paths
    are translated to the Windows-side mount and backslashed; the Linux form
    runs natively and stays POSIX throughout.
    """
    if rack_is_windows():
        recorder_wav_path = f"{RACK_HEADLESS_DIR_WIN}/out/{name}.wav"
        userdir_arg = RACK_HEADLESS_DIR_WIN.replace("/", "\\")
        patch_arg = f"{RACK_HEADLESS_DIR_WIN}/patches/{name}.vcv".replace("/", "\\")
    else:
        recorder_wav_path = str(RACK_HEADLESS_DIR / "out" / f"{name}.wav")
        userdir_arg = str(RACK_HEADLESS_DIR)
        patch_arg = str(RACK_HEADLESS_DIR / "patches" / f"{name}.vcv")
    return recorder_wav_path, userdir_arg, patch_arg


def _ensure_scratch_settings():
    """Disable Rack's startup version check in the scratch user dir — the
    request to api.vcvrack.com can hang for minutes after many rapid
    launches, stalling startup past the render deadline."""
    settings = RACK_HEADLESS_DIR / "settings.json"
    if not settings.exists():
        settings.write_text(json.dumps({"autoCheckUpdates": False}))


def _kill_orphaned_racks():
    """Kill headless Rack.exe instances left behind on timeout — killing
    the WSL interop proxy does not kill the Windows process, and a
    lingering instance starves subsequent renders. No-op on Linux: the
    render() finally block's proc.kill() reaps the child directly there."""
    if not rack_is_windows():
        return
    marker = Path(RACK_HEADLESS_DIR_WIN).name
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='Rack.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _wait_for_wav(wav_path, min_bytes, deadline, proc=None, sleep_fn=None):
    """Return the finished WAV's size, or the largest size seen by deadline.

    The injected gate LFO is a *continuous* square wave, so it re-triggers
    the Recorder every period; with incrementPath=False the WAV cycles
    0 -> full -> 0 -> full ... A complete render is the only thing that
    reaches ~min_bytes, so return as soon as the file crosses 90% of target
    (grabbing it at a finalized peak, before the next re-trigger wipes it).
    Otherwise report the largest size observed — a genuinely short/silent
    render's finalized peak — and let the caller apply its tolerance. This
    replaces size-stability detection, which raced both the ffmpeg 256 KB
    flush plateaus and the re-trigger cycle (the latter bit lin-x64/Railway
    hard: a whole render looked like a 95s stall).

    A healthy headless Rack never exits on its own (it blocks on stdin), so
    if `proc` has exited before the WAV is done it crashed — some plugins
    SIGSEGV in their widget constructor headless (loadFont with no window).
    Return immediately in that case instead of waiting out the full deadline
    for a WAV a dead process will never write."""
    accept = min_bytes * 0.9
    if sleep_fn is None:
        sleep_fn = time.sleep
    max_size = 0
    while time.monotonic() < deadline:
        size = wav_path.stat().st_size if wav_path.exists() else 0
        if size >= accept:
            return size
        if size > max_size:
            max_size = size
        if proc is not None and proc.poll() is not None:
            return max_size  # Rack died (likely a headless-unsafe plugin)
        sleep_fn(0.5)
    return max_size


def render(vcv_path, out_path=None, seconds=RENDER_SECONDS):
    """Render one patch to a WAV under data/audio/. Returns the WAV path."""
    vcv_path = Path(vcv_path)
    if not RACK_BINARY.exists():
        raise RenderError(f"Rack binary not found: {RACK_BINARY}")

    patch = parse_vcv(vcv_path)
    name = patch_slug(vcv_path)

    scratch_patches = RACK_HEADLESS_DIR / "patches"
    scratch_out = RACK_HEADLESS_DIR / "out"
    scratch_patches.mkdir(parents=True, exist_ok=True)
    scratch_out.mkdir(parents=True, exist_ok=True)
    _ensure_scratch_settings()
    # A truncated log (unclean previous exit) makes Rack pop a blocking
    # "Rack crashed" dialog BEFORE loading the patch, even headless
    # (standalone.cpp: logger::wasTruncated() + osdialog_message).
    (RACK_HEADLESS_DIR / "log.txt").unlink(missing_ok=True)

    recorder_path, userdir_arg, patch_arg = rack_invocation(name)
    scratch_wav = scratch_out / f"{name}.wav"
    scratch_wav.unlink(missing_ok=True)

    injected = inject_recorder(patch, recorder_path, seconds)
    tmp_patch = scratch_patches / f"{name}.vcv"
    tmp_patch.write_text(json.dumps(injected))

    proc = subprocess.Popen(
        [str(RACK_BINARY), "-h", "-u", userdir_arg, patch_arg],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=RACK_BINARY.parent,
    )
    try:
        # 16-bit mono lower bound; the header adds a little on top
        min_bytes = seconds * RENDER_SAMPLE_RATE * 2
        deadline = time.monotonic() + seconds + RENDER_STARTUP_TIMEOUT
        final_size = _wait_for_wav(scratch_wav, min_bytes, deadline, proc=proc)
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
            proc.wait()
            # killing the interop proxy orphans the Windows process
            _kill_orphaned_racks()

    # Tolerate a small shortfall from block-boundary timing quantization —
    # only a genuinely short/empty/never-started recording should fail.
    if final_size < min_bytes * 0.8:
        _kill_orphaned_racks()
        raise RenderError(f"short WAV {name}: got {final_size} need>={int(min_bytes*0.8)} (min_bytes={min_bytes})")

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
