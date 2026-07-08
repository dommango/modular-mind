import copy
import math
from pathlib import Path

import pytest

import render_patch as rp
from config import DATA_DIR
from render_patch import (
    REC_GATE_INPUT,
    REC_LEFT_INPUT,
    REC_RIGHT_INPUT,
    RenderError,
    find_audio_feeds,
    gate_lfo_freq_param,
    inject_recorder,
    patch_slug,
)


def test_patch_slug_disambiguates_subdirs():
    assert patch_slug(DATA_DIR / "generated" / "01-drone.vcv") == "01-drone"
    assert patch_slug(DATA_DIR / "generated" / "batch3" / "01-drone.vcv") == "batch3-01-drone"
    assert patch_slug(DATA_DIR / "raw" / "183245.vcv") == "183245"
    assert patch_slug("/somewhere/else/01-drone.vcv") == "01-drone"


def stereo_patch():
    return {
        "version": "1.1.6",
        "modules": [
            {"id": 1, "plugin": "Fundamental", "model": "VCO", "params": [], "pos": [0, 0]},
            {"id": 5, "plugin": "Fundamental", "model": "VCMixer", "params": [], "pos": [8, 0]},
            {"id": 9, "plugin": "Core", "model": "AudioInterface", "params": [], "pos": [16, 0]},
        ],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 5, "inputId": 1},
            {"id": 2, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 0},
            {"id": 3, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 1},
        ],
    }


def test_find_audio_feeds_stereo():
    feeds = find_audio_feeds(stereo_patch())
    assert feeds == {0: (5, 0), 1: (5, 0)}


def test_find_audio_feeds_none():
    patch = stereo_patch()
    patch = {**patch, "cables": [patch["cables"][0]]}
    assert find_audio_feeds(patch) == {}


def test_gate_lfo_freq_param():
    # square high for 10s -> 0.05 Hz -> log2
    assert gate_lfo_freq_param(10) == pytest.approx(math.log2(0.05))


def test_inject_recorder_adds_modules_and_cables():
    patch = stereo_patch()
    out = inject_recorder(patch, "C:/tmp/out.wav", seconds=10)

    models = [(m.get("plugin"), m.get("model")) for m in out["modules"]]
    assert ("VCV-Recorder", "Recorder") in models
    assert models.count(("Fundamental", "LFO")) == 1

    recorder = next(m for m in out["modules"] if m.get("model") == "Recorder")
    assert recorder["data"]["path"] == "C:/tmp/out.wav"
    assert recorder["data"]["format"] == "wav"
    assert recorder["data"]["incrementPath"] is False

    # unique ids
    mod_ids = [m["id"] for m in out["modules"]]
    cable_ids = [c["id"] for c in out["cables"]]
    assert len(mod_ids) == len(set(mod_ids))
    assert len(cable_ids) == len(set(cable_ids))


def test_inject_recorder_tees_left_and_right():
    out = inject_recorder(stereo_patch(), "C:/tmp/out.wav")
    recorder = next(m for m in out["modules"] if m.get("model") == "Recorder")
    into_rec = {
        c["inputId"]: (c["outputModuleId"], c["outputId"])
        for c in out["cables"]
        if c["inputModuleId"] == recorder["id"]
    }
    assert into_rec[REC_LEFT_INPUT] == (5, 0)
    # same source on both audio inputs -> mono recording, no RIGHT tee
    assert REC_RIGHT_INPUT not in into_rec
    lfo = next(m for m in out["modules"] if m.get("model") == "LFO")
    assert into_rec[REC_GATE_INPUT] == (lfo["id"], 3)


def test_inject_recorder_distinct_right_channel():
    patch = stereo_patch()
    patch["modules"].append(
        {"id": 6, "plugin": "Fundamental", "model": "VCO", "params": [], "pos": [4, 0]}
    )
    patch["cables"][2] = {
        "id": 3, "outputModuleId": 6, "outputId": 0, "inputModuleId": 9, "inputId": 1,
    }
    out = inject_recorder(patch, "C:/tmp/out.wav")
    recorder = next(m for m in out["modules"] if m.get("model") == "Recorder")
    into_rec = {
        c["inputId"]: (c["outputModuleId"], c["outputId"])
        for c in out["cables"]
        if c["inputModuleId"] == recorder["id"]
    }
    assert into_rec[REC_LEFT_INPUT] == (5, 0)
    assert into_rec[REC_RIGHT_INPUT] == (6, 0)


def test_inject_recorder_does_not_mutate_input():
    patch = stereo_patch()
    snapshot = copy.deepcopy(patch)
    inject_recorder(patch, "C:/tmp/out.wav")
    assert patch == snapshot


def test_inject_recorder_no_audio_raises():
    patch = stereo_patch()
    patch = {**patch, "cables": []}
    with pytest.raises(RenderError):
        inject_recorder(patch, "C:/tmp/out.wav")


def test_rack_is_windows_true_for_exe(monkeypatch):
    monkeypatch.setattr(rp, "RACK_BINARY", Path("/mnt/c/Program Files/VCV/Rack2Free/Rack.exe"))
    assert rp.rack_is_windows() is True


def test_rack_is_windows_false_for_linux_binary(monkeypatch):
    monkeypatch.setattr(rp, "RACK_BINARY", Path("/opt/Rack2Free/Rack"))
    assert rp.rack_is_windows() is False


def test_rack_invocation_windows_form(monkeypatch):
    monkeypatch.setattr(rp, "RACK_BINARY", Path("/mnt/c/Program Files/VCV/Rack2Free/Rack.exe"))
    monkeypatch.setattr(rp, "RACK_HEADLESS_DIR_WIN", "C:/Users/domma/AppData/Local/Temp/rack-headless")

    recorder_path, userdir_arg, patch_arg = rp.rack_invocation("01-drone")

    assert recorder_path == "C:/Users/domma/AppData/Local/Temp/rack-headless/out/01-drone.wav"
    assert userdir_arg == "C:\\Users\\domma\\AppData\\Local\\Temp\\rack-headless"
    assert patch_arg == "C:\\Users\\domma\\AppData\\Local\\Temp\\rack-headless\\patches\\01-drone.vcv"


def test_rack_invocation_linux_form(monkeypatch):
    monkeypatch.setattr(rp, "RACK_BINARY", Path("/opt/Rack2Free/Rack"))
    monkeypatch.setattr(rp, "RACK_HEADLESS_DIR", Path("/rack-userdir"))

    recorder_path, userdir_arg, patch_arg = rp.rack_invocation("01-drone")

    assert recorder_path == "/rack-userdir/out/01-drone.wav"
    assert userdir_arg == "/rack-userdir"
    assert patch_arg == "/rack-userdir/patches/01-drone.vcv"


def test_kill_orphaned_racks_noop_on_linux(monkeypatch):
    monkeypatch.setattr(rp, "RACK_BINARY", Path("/opt/Rack2Free/Rack"))
    calls = []
    monkeypatch.setattr(rp.subprocess, "run", lambda *a, **k: calls.append((a, k)))

    rp._kill_orphaned_racks()

    assert calls == []


def test_wait_for_wav_returns_when_target_reached(tmp_path, monkeypatch):
    # Returns as soon as the WAV crosses 90% of min_bytes — the only thing
    # a complete render produces — without waiting out the deadline.
    wav = tmp_path / "out.wav"
    wav.write_bytes(b"")
    sizes = iter([100, 500, 950])  # 950 >= 0.9 * 1000

    def fake_sleep(_):
        try:
            wav.write_bytes(b"x" * next(sizes))
        except StopIteration:
            pass

    monkeypatch.setattr(rp.time, "sleep", fake_sleep)
    deadline = rp.time.monotonic() + 100

    result = rp._wait_for_wav(wav, 1000, deadline)

    assert result == 950


def test_wait_for_wav_reports_peak_across_retrigger_cycle(tmp_path, monkeypatch):
    # The gate LFO re-triggers, so a finished WAV cycles full -> 0 -> full.
    # If it never crosses 90% (short/silent render) the largest size seen
    # must be reported, not whatever it happens to be at the deadline.
    wav = tmp_path / "out.wav"
    wav.write_bytes(b"")
    # peak at 500, then wiped back to 0 by a re-trigger, ending low
    sizes = iter([200, 500, 0, 100])

    def fake_sleep(_):
        try:
            wav.write_bytes(b"x" * next(sizes))
        except StopIteration:
            pass

    monkeypatch.setattr(rp.time, "sleep", fake_sleep)
    deadline = rp.time.monotonic() + 0.2

    # min_bytes huge so the 90% early-return never fires -> deadline wins
    result = rp._wait_for_wav(wav, 10**9, deadline)

    assert result == 500  # the peak, not the ending size


def test_wait_for_wav_returns_zero_when_never_created(tmp_path, monkeypatch):
    wav = tmp_path / "out.wav"  # never created
    monkeypatch.setattr(rp.time, "sleep", lambda _: None)
    deadline = rp.time.monotonic() + 0.05

    result = rp._wait_for_wav(wav, 100_000, deadline)

    assert result == 0


def test_wait_for_wav_bails_when_proc_dies(tmp_path, monkeypatch):
    # A headless-unsafe plugin can SIGSEGV Rack; the WAV never appears, but
    # we must not wait out the deadline — bail as soon as the process exits.
    wav = tmp_path / "out.wav"  # never created

    class DeadProc:
        def poll(self):
            return 139  # exited (segfault)

    monkeypatch.setattr(rp.time, "sleep", lambda _: None)
    deadline = rp.time.monotonic() + 100

    result = rp._wait_for_wav(wav, 100_000, deadline, proc=DeadProc())

    assert result == 0


def test_wait_for_wav_bails_when_no_output_after_grace(tmp_path, monkeypatch):
    # Some plugins hang the engine headless instead of crashing; the process
    # stays alive but the WAV never grows. Give up after the grace window
    # rather than the full deadline.
    wav = tmp_path / "out.wav"  # never grows
    clock = {"t": 0.0}
    monkeypatch.setattr(rp.time, "monotonic", lambda: clock["t"])

    def fake_sleep(_):
        clock["t"] += 1.0

    monkeypatch.setattr(rp.time, "sleep", fake_sleep)

    result = rp._wait_for_wav(wav, 1000, deadline=100.0)

    assert result == 0
    assert clock["t"] <= 20  # bailed near the 15s grace, not the 100s deadline
