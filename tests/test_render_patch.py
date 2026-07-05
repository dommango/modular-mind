import copy
import math

import pytest

from render_patch import (
    REC_GATE_INPUT,
    REC_LEFT_INPUT,
    REC_RIGHT_INPUT,
    RenderError,
    find_audio_feeds,
    gate_lfo_freq_param,
    inject_recorder,
)


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
