import copy
import json

from render_patch import RenderError
from repair_loop import merge_repair_manifest, repair_one


def make_metrics(peak=0.5, rms=0.1, dc_offset=0.0, flags=None, makes_sound=True, character="drone"):
    """Full analyze_audio.analyze_file()-shaped metrics dict."""
    return {
        "duration": 10.0,
        "sample_rate": 44100,
        "peak": peak,
        "rms": rms,
        "dc_offset": dc_offset,
        "silent_ratio": 0.0,
        "spectral_centroid_hz": 500.0,
        "spectral_flatness": 0.1,
        "spectral_bandwidth_hz": 200.0,
        "onset_count": 0,
        "onset_rate": 0.0,
        "voiced_ratio": 0.5,
        "median_f0_hz": 220.0,
        "verdict": {
            "makes_sound": makes_sound,
            "character": character,
            "flags": flags or [],
        },
    }


def mono_mixer_patch():
    """VCO -> VCMixer -> AudioInterface, mono teed — gives apply_repairs a
    real gain stage to scale (see repair_patch.GAIN_PARAM_SPECS)."""
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


def write_patch(tmp_path, name="patch.vcv", patch=None):
    path = tmp_path / name
    path.write_text(json.dumps(patch if patch is not None else mono_mixer_patch()))
    return path


def failing_render(*args, **kwargs):
    raise AssertionError("render_fn should not be called")


def failing_analyze(*args, **kwargs):
    raise AssertionError("analyze_fn should not be called")


def test_repair_one_converges_in_two_attempts(tmp_path):
    vcv_path = write_patch(tmp_path)
    out_dir = tmp_path / "repaired"

    scripted = [make_metrics(peak=0.99, flags=["clipping"]), make_metrics(peak=0.6, flags=[])]

    def fake_render(path, *args, **kwargs):
        return tmp_path / "audio" / (path.stem + ".wav")

    def fake_analyze(wav_path):
        return scripted.pop(0)

    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.99, flags=["clipping"]),
        render_fn=fake_render,
        analyze_fn=fake_analyze,
        out_dir=out_dir,
    )

    assert result["status"] == "repaired"
    assert len(result["attempts"]) == 2
    assert result["accepted"] == str(out_dir / f"{vcv_path.stem}-r2.vcv")
    assert (out_dir / f"{vcv_path.stem}-r1.vcv").exists()
    assert (out_dir / f"{vcv_path.stem}-r2.vcv").exists()
    assert result["attempts"][0]["good"] is False
    assert result["attempts"][1]["good"] is True


def test_repair_one_supplied_baseline_skips_baseline_render(tmp_path):
    vcv_path = write_patch(tmp_path)
    calls = {"render": 0}

    def counting_render(path, *args, **kwargs):
        calls["render"] += 1
        return tmp_path / "audio" / (path.stem + ".wav")

    scripted = [make_metrics(peak=0.6, flags=[])]

    def fake_analyze(wav_path):
        return scripted.pop(0)

    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.99, flags=["clipping"]),
        render_fn=counting_render,
        analyze_fn=fake_analyze,
        out_dir=tmp_path / "repaired",
    )

    # only the one repair-attempt render, none for the baseline itself
    assert calls["render"] == 1
    assert result["status"] == "repaired"


def test_repair_one_clean_baseline_no_attempts_no_files(tmp_path):
    vcv_path = write_patch(tmp_path)
    out_dir = tmp_path / "repaired"

    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.5, flags=[]),
        render_fn=failing_render,
        analyze_fn=failing_analyze,
        out_dir=out_dir,
    )

    assert result["status"] == "clean"
    assert result["attempts"] == []
    assert result["accepted"] is None
    assert not out_dir.exists()


def test_repair_one_gives_up_when_apply_repairs_has_no_changes(tmp_path):
    vcv_path = write_patch(tmp_path)

    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.5, flags=["unhandled_flag"]),
        render_fn=failing_render,
        analyze_fn=failing_analyze,
        out_dir=tmp_path / "repaired",
    )

    assert result["status"] == "gave_up"
    assert result["attempts"] == []


def test_repair_one_render_failure_records_attempt(tmp_path):
    vcv_path = write_patch(tmp_path)

    def broken_render(path, *args, **kwargs):
        raise RenderError("Rack crashed")

    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.99, flags=["clipping"]),
        render_fn=broken_render,
        analyze_fn=failing_analyze,
        out_dir=tmp_path / "repaired",
    )

    assert result["status"] == "render_failed"
    assert len(result["attempts"]) == 1
    attempt = result["attempts"][0]
    assert attempt["render"] == "FAIL"
    assert attempt["good"] is False
    assert "render_error" in attempt


def test_repair_one_baseline_summary_fields(tmp_path):
    vcv_path = write_patch(tmp_path)
    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.5, rms=0.2, dc_offset=0.01, flags=[]),
        render_fn=failing_render,
        analyze_fn=failing_analyze,
        out_dir=tmp_path / "repaired",
    )
    assert result["baseline"] == {
        "flags": [],
        "rms": 0.2,
        "peak": 0.5,
        "dc_offset": 0.01,
        "source": "supplied",
    }


def test_repair_log_entry_shape(tmp_path):
    vcv_path = write_patch(tmp_path)
    result = repair_one(
        vcv_path,
        baseline_metrics=make_metrics(peak=0.5, flags=[]),
        render_fn=failing_render,
        analyze_fn=failing_analyze,
        out_dir=tmp_path / "repaired",
    )
    assert set(["original", "slug", "status", "baseline", "attempts", "accepted"]) <= set(result)


def test_merge_repair_manifest_attaches_repair_block():
    entries = [{"name": "01-x", "archetype": "drone"}, {"name": "02-y", "archetype": "seq"}]
    snapshot = copy.deepcopy(entries)
    results = {"01-x": {"status": "repaired", "accepted": "data/repaired/01-x-r1.vcv"}}

    merged = merge_repair_manifest(entries, results)

    assert merged[0]["repair"] == {"status": "repaired", "accepted": "data/repaired/01-x-r1.vcv"}
    assert merged[0]["archetype"] == "drone"
    assert "repair" not in merged[1]
    assert entries == snapshot  # no mutation


def test_merge_repair_manifest_handles_missing_accepted():
    entries = [{"name": "01-x"}]
    results = {"01-x": {"status": "gave_up"}}
    merged = merge_repair_manifest(entries, results)
    assert merged[0]["repair"] == {"status": "gave_up", "accepted": None}


def test_repair_one_analyze_failure_records_attempt(tmp_path):
    vcv = write_patch(tmp_path)

    def fake_render(path, *args, **kwargs):
        return tmp_path / "out.wav"

    def broken_analyze(wav_path):
        raise RuntimeError("unreadable WAV")

    result = repair_one(
        vcv,
        baseline_metrics=make_metrics(peak=1.0, flags=["clipping"]),
        render_fn=fake_render,
        analyze_fn=broken_analyze,
        out_dir=tmp_path / "repaired",
    )

    assert result["status"] == "analyze_failed"
    assert len(result["attempts"]) == 1
    attempt = result["attempts"][0]
    assert attempt["render"] == "OK"
    assert "analyze_error" in attempt
    assert attempt["good"] is False
