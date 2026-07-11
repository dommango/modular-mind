import hashlib
import json
from pathlib import Path

import pytest

from analyze_audio import ANALYSIS_PATH
from llm_driver import DriverError
from llm_patch_loop import run_loop
from score_audio import build_bands

RUN_ID = "drone-test"
ARCHETYPE = "drone"

CLEAN_METRICS = {
    "duration": 10.0,
    "sample_rate": 44100,
    "peak": 0.5,
    "rms": 0.1,
    "dc_offset": 0.0,
    "silent_ratio": 0.1,
    "spectral_centroid_hz": 1000.0,
    "spectral_flatness": 0.2,
    "spectral_bandwidth_hz": 500.0,
    "onset_count": 10,
    "onset_rate": 1.0,
    "voiced_ratio": 0.5,
    "median_f0_hz": 220.0,
    "verdict": {"makes_sound": True, "character": "drone", "flags": []},
}

GARBAGE_RESPONSE = "Sorry, I can't help with that right now."


def bogaudio_patch():
    return {
        "version": "1.1.6",
        "modules": [
            {"id": 1, "plugin": "Bogaudio", "model": "LVCF", "params": [], "pos": [0, 0]},
            {"id": 2, "plugin": "Core", "model": "AudioInterface", "params": [], "pos": [8, 0]},
        ],
        "cables": [],
    }


def valid_patch():
    return {
        "version": "1.1.6",
        "modules": [
            {"id": 1, "plugin": "Fundamental", "model": "VCO", "params": [], "pos": [0, 0]},
            {"id": 2, "plugin": "Core", "model": "AudioInterface", "params": [], "pos": [8, 0]},
        ],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 2, "inputId": 0},
            {"id": 2, "outputModuleId": 1, "outputId": 0, "inputModuleId": 2, "inputId": 1},
        ],
    }


def _fence(patch_dict):
    return f"```json\n{json.dumps(patch_dict)}\n```"


class FakeDriver:
    """Scripted driver: returns `responses` in order, or raises DriverError
    on the call numbered `fail_at` (1-based) before consuming a response."""

    def __init__(self, responses, fail_at=None):
        self.responses = list(responses)
        self.fail_at = fail_at
        self.calls = 0
        self.prompts = []

    def complete(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail_at is not None and self.calls == self.fail_at:
            raise DriverError(f"boom on call {self.calls}")
        return self.responses.pop(0)


class FakeRenderFn:
    def __init__(self, wav_path):
        self.wav_path = wav_path
        self.calls = []

    def __call__(self, vcv_path):
        self.calls.append(Path(vcv_path))
        return self.wav_path


def fake_analyze_fn(wav_path):
    return CLEAN_METRICS


def fixed_now():
    return "2026-01-01T00:00:00+00:00"


def build_test_bands():
    """Bands where CLEAN_METRICS sits exactly at the corpus median for every
    scored metric -> a clean render always fitness-scores near 100."""
    samples = {
        "rms": [0.05, 0.08, 0.1, 0.12, 0.15],
        "spectral_centroid_hz": [500.0, 800.0, 1000.0, 1200.0, 1500.0],
        "spectral_flatness": [0.1, 0.15, 0.2, 0.25, 0.3],
        "onset_rate": [0.5, 0.8, 1.0, 1.2, 1.5],
        "voiced_ratio": [0.3, 0.4, 0.5, 0.6, 0.7],
        "silent_ratio": [0.05, 0.08, 0.1, 0.12, 0.15],
    }
    n = len(next(iter(samples.values())))
    playability = {"self_playing": {str(i): {} for i in range(n)}}
    corpus_analysis = {
        str(i): {
            "status": "ok",
            "metrics": {name: values[i] for name, values in samples.items()}
            | {"verdict": {"flags": []}},
        }
        for i in range(n)
    }
    return build_bands(playability, corpus_analysis)


def _loop_kwargs(tmp_path, driver, render_fn):
    return dict(
        max_iterations=5,
        target_score=60,
        driver=driver,
        render_fn=render_fn,
        analyze_fn=fake_analyze_fn,
        bands=build_test_bands(),
        context="CONTEXT-BLOCK",
        param_stats="some param stats",
        out_dir=tmp_path / "out",
        candidates_dir=tmp_path / "candidates",
        log_dir=tmp_path / "trajectories",
        run_id=RUN_ID,
        now_fn=fixed_now,
        analysis_path=tmp_path / "audio_analysis.json",
    )


def _run_three_iteration_script(tmp_path):
    wav_path = tmp_path / "fake.wav"
    wav_path.write_bytes(b"not-really-a-wav")
    driver = FakeDriver([GARBAGE_RESPONSE, _fence(bogaudio_patch()), _fence(valid_patch())])
    render_fn = FakeRenderFn(wav_path)
    summary = run_loop(ARCHETYPE, **_loop_kwargs(tmp_path, driver, render_fn))
    return summary, driver, render_fn


def _read_jsonl(log_path):
    lines = log_path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


# --- happy path: extract failure -> whitelist violation -> accepted --------


def test_run_loop_accepts_on_third_iteration(tmp_path):
    summary, driver, render_fn = _run_three_iteration_script(tmp_path)

    assert summary["status"] == "accepted"
    assert summary["run_id"] == RUN_ID
    assert summary["iterations"] == 3
    assert summary["score"] == 100


def test_iteration1_critique_appears_in_iteration2_prompt(tmp_path):
    _summary, driver, _render_fn = _run_three_iteration_script(tmp_path)

    assert len(driver.prompts) == 3
    assert "your reply contained no parseable JSON object" in driver.prompts[1]


def test_whitelist_violation_iteration_does_not_call_render(tmp_path):
    _summary, _driver, render_fn = _run_three_iteration_script(tmp_path)

    assert len(render_fn.calls) == 1


def test_jsonl_has_three_well_formed_records(tmp_path):
    _summary, _driver, _render_fn = _run_three_iteration_script(tmp_path)

    log_path = tmp_path / "trajectories" / f"{RUN_ID}.jsonl"
    records = _read_jsonl(log_path)

    assert len(records) == 3
    assert [r["accepted"] for r in records] == [False, False, True]
    for r in records:
        expected_hash = hashlib.sha256(r["prompt"].encode("utf-8")).hexdigest()
        assert r["prompt_sha256"] == expected_hash
        assert r["run_id"] == RUN_ID
        assert r["archetype"] == ARCHETYPE

    assert records[0]["patch_json"] is None
    assert records[0]["render"]["status"] == "skipped"
    assert records[1]["patch_json"] is not None
    assert records[1]["render"]["status"] == "skipped"
    assert records[2]["render"]["status"] == "ok"
    assert records[2]["metrics"] == CLEAN_METRICS
    assert records[2]["score"]["fitness"] == 100


def test_acceptance_copies_vcv_and_updates_manifest(tmp_path):
    summary, _driver, _render_fn = _run_three_iteration_script(tmp_path)

    accepted_path = Path(summary["accepted_path"])
    assert accepted_path.exists()
    assert json.loads(accepted_path.read_text()) == valid_patch()

    manifest_path = tmp_path / "out" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["name"] == RUN_ID
    assert entry["archetype"] == ARCHETYPE
    assert entry["source"] == "llm"
    assert entry["run_id"] == RUN_ID
    assert entry["iterations"] == 3
    assert entry["score"] == 100


def test_acceptance_merges_metrics_into_injected_analysis_path_only(tmp_path):
    summary, _driver, _render_fn = _run_three_iteration_script(tmp_path)

    analysis_path = tmp_path / "audio_analysis.json"
    merged = json.loads(analysis_path.read_text())
    assert CLEAN_METRICS in merged.values()

    # the real project artifact must never be touched by an injected path
    if ANALYSIS_PATH.exists():
        real_before = ANALYSIS_PATH.read_text()
        assert str(analysis_path) != str(ANALYSIS_PATH)
        assert ANALYSIS_PATH.read_text() == real_before


# --- driver error mid-run ---------------------------------------------------


def test_driver_error_stops_run_and_logs_partial_trajectory(tmp_path):
    wav_path = tmp_path / "fake.wav"
    wav_path.write_bytes(b"not-really-a-wav")
    driver = FakeDriver([GARBAGE_RESPONSE], fail_at=2)
    render_fn = FakeRenderFn(wav_path)

    summary = run_loop(ARCHETYPE, **_loop_kwargs(tmp_path, driver, render_fn))

    assert summary["status"] == "driver_error"
    assert summary["iterations"] == 2

    log_path = tmp_path / "trajectories" / f"{RUN_ID}.jsonl"
    records = _read_jsonl(log_path)
    assert len(records) == 2
    assert records[0]["accepted"] is False
    assert records[1]["accepted"] is False
    assert "driver error" in records[1]["critique"]
    assert records[1]["response_raw"] is None
    assert render_fn.calls == []


# --- budget exhaustion -------------------------------------------------------


def test_budget_respected_exhausted_at_max_iterations(tmp_path):
    wav_path = tmp_path / "fake.wav"
    wav_path.write_bytes(b"not-really-a-wav")
    driver = FakeDriver([GARBAGE_RESPONSE])
    render_fn = FakeRenderFn(wav_path)

    kwargs = _loop_kwargs(tmp_path, driver, render_fn)
    kwargs["max_iterations"] = 1
    summary = run_loop(ARCHETYPE, **kwargs)

    assert summary["status"] == "exhausted"
    assert summary["iterations"] == 1
    assert summary["best_score"] == 0

    log_path = tmp_path / "trajectories" / f"{RUN_ID}.jsonl"
    records = _read_jsonl(log_path)
    assert len(records) == 1
    assert records[0]["accepted"] is False
    assert render_fn.calls == []


def test_default_run_ids_are_unique(tmp_path):
    def run_once():
        summary = run_loop(
            ARCHETYPE,
            driver=FakeDriver([], fail_at=1),
            bands=None,
            context="ctx",
            param_stats="",
            out_dir=tmp_path / "out",
            candidates_dir=tmp_path / "candidates",
            log_dir=tmp_path / "trajectories",
            analysis_path=tmp_path / "analysis.json",
        )
        return summary["run_id"]

    assert run_once() != run_once()
