import copy

import pytest

from score_audio import (
    SCORED_METRICS,
    band_summary,
    build_bands,
    load_bands,
    metric_percentile,
    score_metrics,
)


def make_metrics(
    rms=0.1,
    spectral_centroid_hz=1000.0,
    spectral_flatness=0.2,
    onset_rate=1.0,
    voiced_ratio=0.5,
    silent_ratio=0.1,
    flags=None,
    makes_sound=True,
):
    return {
        "rms": rms,
        "spectral_centroid_hz": spectral_centroid_hz,
        "spectral_flatness": spectral_flatness,
        "onset_rate": onset_rate,
        "voiced_ratio": voiced_ratio,
        "silent_ratio": silent_ratio,
        "verdict": {"makes_sound": makes_sound, "character": "drone", "flags": flags or []},
    }


def make_corpus(rms_values, clipping_ids=()):
    playability = {"self_playing": {pid: {} for pid in rms_values}}
    corpus_analysis = {
        pid: {"status": "ok", "metrics": make_metrics(rms=rms, flags=["clipping"] if pid in clipping_ids else [])}
        for pid, rms in rms_values.items()
    }
    return playability, corpus_analysis


def test_build_bands_drops_clipping_and_sorts():
    rms_values = {"1": 0.3, "2": 0.1, "3": 0.2}
    playability, corpus_analysis = make_corpus(rms_values, clipping_ids=("2",))

    bands = build_bands(playability, corpus_analysis)

    assert bands["generated_from"]["n_reference"] == 2
    assert bands["generated_from"]["excluded_clipping"] == 1
    assert bands["generated_from"]["sources"] == [
        "patch_playability.json",
        "corpus_audio_analysis.json",
    ]
    assert bands["metrics"]["rms"]["values"] == [0.2, 0.3]
    assert set(bands["metrics"]) == set(SCORED_METRICS)
    assert set(bands["metrics"]["rms"]) == {
        "values", "min", "p10", "p25", "p50", "p75", "p90", "max",
    }


def test_build_bands_skips_non_ok_and_missing_metric():
    playability = {"self_playing": {"1": {}, "2": {}}}
    corpus_analysis = {
        "1": {"status": "ok", "metrics": make_metrics(rms=0.5)},
        "2": {"status": "render-fail"},
    }
    bands = build_bands(playability, corpus_analysis)
    assert bands["generated_from"]["n_reference"] == 1
    assert bands["metrics"]["rms"]["values"] == [0.5]


def test_metric_percentile_below_min_and_above_max():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert metric_percentile(0.0, values) == 0.0
    assert metric_percentile(10.0, values) == 1.0


def test_metric_percentile_exact_median():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert metric_percentile(3.0, values) == pytest.approx(0.5)


def test_metric_percentile_tie_block_midrank():
    values = [1.0, 2.0, 2.0, 2.0, 3.0]
    assert metric_percentile(2.0, values) == pytest.approx(0.5)


def test_metric_percentile_interpolates_between_neighbors():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert 0.0 < metric_percentile(2.5, values) < 1.0


def test_score_metrics_value_at_median_scores_one():
    bands = {"metrics": {"rms": {"values": [1.0, 2.0, 3.0, 4.0, 5.0]}}}
    metrics = make_metrics(rms=3.0)

    result = score_metrics(metrics, bands)

    assert result["per_metric"]["rms"]["percentile"] == pytest.approx(0.5)
    assert result["per_metric"]["rms"]["score"] == pytest.approx(1.0)


def test_score_metrics_out_of_range_scores_zero():
    bands = {"metrics": {"rms": {"values": [1.0, 2.0, 3.0, 4.0, 5.0]}}}
    metrics = make_metrics(rms=100.0)

    result = score_metrics(metrics, bands)

    assert result["per_metric"]["rms"]["percentile"] == pytest.approx(1.0)
    assert result["per_metric"]["rms"]["score"] == pytest.approx(0.0)


def test_score_metrics_weights_respected():
    bands = {
        "metrics": {
            "rms": {"values": [1.0, 2.0, 3.0, 4.0, 5.0]},
            "silent_ratio": {"values": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]},
        }
    }
    metrics = make_metrics(rms=3.0, silent_ratio=-5.0)

    result = score_metrics(metrics, bands)

    assert result["per_metric"]["rms"]["score"] == pytest.approx(1.0)
    assert result["per_metric"]["silent_ratio"]["score"] == pytest.approx(0.0)
    # weighted mean: (1.0*1.0 + 0.5*0.0) / (1.0 + 0.5) = 0.6667 -> 67
    assert result["fitness"] == 67


def test_score_metrics_not_making_sound_is_zero_fitness():
    bands = {"metrics": {"rms": {"values": [1.0, 2.0, 3.0]}}}
    metrics = make_metrics(rms=2.0, makes_sound=False)

    result = score_metrics(metrics, bands)

    assert result == {"fitness": 0, "per_metric": {}}


def test_score_metrics_does_not_mutate_input():
    bands = {"metrics": {"rms": {"values": [1.0, 2.0, 3.0, 4.0, 5.0]}}}
    metrics = make_metrics(rms=3.0)
    snapshot = copy.deepcopy(metrics)

    score_metrics(metrics, bands)

    assert metrics == snapshot


def test_band_summary_exposes_p10_p50_p90():
    bands = {
        "metrics": {
            "rms": {"min": 0.0, "p10": 0.1, "p25": 0.2, "p50": 0.3, "p75": 0.4, "p90": 0.5, "max": 0.6, "values": []},
        }
    }
    assert band_summary(bands) == {"rms": {"p10": 0.1, "p50": 0.3, "p90": 0.5}}


def test_load_bands_missing_returns_none(tmp_path):
    assert load_bands(tmp_path / "nope.json") is None


def test_load_bands_roundtrip(tmp_path):
    path = tmp_path / "bands.json"
    path.write_text('{"generated_from": {}, "metrics": {}}')
    assert load_bands(path) == {"generated_from": {}, "metrics": {}}


def test_load_bands_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "bands.json"
    path.write_text('{"generated_from": {"n_ref')  # truncated write
    assert load_bands(path) is None


def test_band_summary_omits_empty_reference_metrics():
    bands = build_bands({"self_playing": {}}, {})
    assert band_summary(bands) == {}
