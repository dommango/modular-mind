"""Score generated-patch audio against the corpus's own acoustic distribution.

analyze_audio.py's verdict flags are pass/fail; this answers a softer
question — how typical does this patch sound next to real, liked corpus
patches? build_bands() turns the self-playing slice of
corpus_audio_analysis.json into per-metric percentile bands (min/p10/
p25/p50/p75/p90/max); score_metrics() maps a patch's metrics onto those
bands and averages how close each lands to the corpus median (0 = at an
extreme, 1 = dead center), weighted into a single 0-100 fitness score.

Bands are a separate, occasionally-rebuilt artifact (corpus_metric_bands.json)
from the per-patch scores (audio_scores.json) so scoring never triggers a
corpus re-scan.

Usage:
  python3 score_audio.py --rebuild-bands       # (re)build bands from the corpus
  python3 score_audio.py                       # score data/output/audio_analysis.json
  python3 score_audio.py data/output/audio_analysis.json --output data/output/audio_scores.json
"""

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np

from analyze_audio import ANALYSIS_PATH
from config import OUTPUT_DIR

BANDS_PATH = OUTPUT_DIR / "corpus_metric_bands.json"
SCORES_PATH = OUTPUT_DIR / "audio_scores.json"
PLAYABILITY_PATH = OUTPUT_DIR / "patch_playability.json"
CORPUS_ANALYSIS_PATH = OUTPUT_DIR / "corpus_audio_analysis.json"

# Weight of each metric in the blended fitness score. silent_ratio is
# downweighted — it's largely redundant with rms and near-zero for most
# self-playing corpus patches, so it has little discriminating power.
SCORED_METRICS = {
    "rms": 1.0,
    "spectral_centroid_hz": 1.0,
    "spectral_flatness": 1.0,
    "onset_rate": 1.0,
    "voiced_ratio": 1.0,
    "silent_ratio": 0.5,
}

BAND_QUANTILES = {"min": 0, "p10": 10, "p25": 25, "p50": 50, "p75": 75, "p90": 90, "max": 100}


def _reference_records(playability, corpus_analysis, exclude_clipping):
    """Metrics dicts for self-playing corpus patches, minus any excluded
    for clipping. Returns (records, excluded_count)."""
    records = []
    excluded = 0
    for patch_id in playability.get("self_playing", {}):
        analysis = corpus_analysis.get(patch_id)
        if not analysis or analysis.get("status") != "ok":
            continue
        metrics = analysis.get("metrics")
        if metrics is None:
            continue
        flags = metrics.get("verdict", {}).get("flags", [])
        if exclude_clipping and "clipping" in flags:
            excluded += 1
            continue
        records.append(metrics)
    return records, excluded


def build_bands(playability, corpus_analysis, exclude_clipping=True):
    """Percentile bands for SCORED_METRICS over self-playing corpus patches."""
    records, excluded = _reference_records(playability, corpus_analysis, exclude_clipping)

    metrics_out = {}
    for name in SCORED_METRICS:
        values = sorted(r[name] for r in records if name in r)
        band = {"values": values}
        for label, q in BAND_QUANTILES.items():
            band[label] = float(np.percentile(values, q)) if values else None
        metrics_out[name] = band

    return {
        "generated_from": {
            "n_reference": len(records),
            "excluded_clipping": excluded,
            "sources": ["patch_playability.json", "corpus_audio_analysis.json"],
        },
        "metrics": metrics_out,
    }


def load_bands(path=BANDS_PATH):
    """Bands dict, or None when the file is absent or unreadable — callers
    treat None as "score nothing" rather than crashing a render batch."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN: ignoring unreadable bands file {path}: {e}", file=sys.stderr)
        return None


def metric_percentile(value, sorted_values):
    """Where value falls in [0, 1] against sorted_values' distribution.

    Below the minimum -> 0.0, above the maximum -> 1.0. A value that ties
    one or more reference points gets the midrank of that tie block (so
    the exact median of an all-distinct list lands at ~0.5); an
    in-between value is linearly interpolated between its two bracketing
    order statistics.
    """
    n = len(sorted_values)
    if n == 0:
        return 0.5
    if value < sorted_values[0]:
        return 0.0
    if value > sorted_values[-1]:
        return 1.0
    if n == 1:
        return 0.5

    lo = bisect_left(sorted_values, value)
    hi = bisect_right(sorted_values, value)
    if hi > lo:
        avg_index = (lo + hi - 1) / 2.0
        return avg_index / (n - 1)

    i0, i1 = lo - 1, lo
    x0, x1 = sorted_values[i0], sorted_values[i1]
    frac = (value - x0) / (x1 - x0)
    r0, r1 = i0 / (n - 1), i1 / (n - 1)
    return r0 + frac * (r1 - r0)


def score_metrics(metrics, bands):
    """Blend a patch's metrics against corpus bands into a 0-100 fitness."""
    if not metrics.get("verdict", {}).get("makes_sound", False):
        return {"fitness": 0, "per_metric": {}}

    per_metric = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for name, weight in SCORED_METRICS.items():
        if name not in metrics:
            continue
        band = bands.get("metrics", {}).get(name)
        if not band or not band.get("values"):
            continue
        value = metrics[name]
        percentile = metric_percentile(value, band["values"])
        score = 1 - 2 * abs(percentile - 0.5)
        per_metric[name] = {
            "value": round(value, 4),
            "percentile": round(percentile, 4),
            "score": round(score, 4),
        }
        weighted_sum += weight * score
        weight_total += weight

    fitness = round(100 * weighted_sum / weight_total) if weight_total else 0
    return {"fitness": int(fitness), "per_metric": per_metric}


def band_summary(bands):
    """Compact {metric: {p10, p50, p90}} view for LLM prompts. Metrics whose
    reference set was empty (all-None quantiles) are omitted — downstream
    formatters render these values with float format specs."""
    return {
        name: {"p10": band["p10"], "p50": band["p50"], "p90": band["p90"]}
        for name, band in bands.get("metrics", {}).items()
        if band.get("p50") is not None
    }


def _write_json_atomic(path, payload):
    """Write JSON via a temp file + rename so an interrupted run never
    leaves a truncated artifact behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _rebuild_bands():
    playability = json.loads(PLAYABILITY_PATH.read_text())
    corpus_analysis = json.loads(CORPUS_ANALYSIS_PATH.read_text())
    bands = build_bands(playability, corpus_analysis)
    _write_json_atomic(BANDS_PATH, bands)
    gf = bands["generated_from"]
    print(
        f"n_reference={gf['n_reference']} excluded_clipping={gf['excluded_clipping']} "
        f"-> {BANDS_PATH}"
    )


def _lowest_metric(per_metric):
    if not per_metric:
        return "-"
    name, entry = min(per_metric.items(), key=lambda kv: kv[1]["score"])
    return f"{name}={entry['score']:.2f}"


def _score_all(analysis_path, bands_path, output_path):
    bands = load_bands(bands_path)
    if bands is None:
        print(f"No bands file at {bands_path} — run --rebuild-bands first", file=sys.stderr)
        return 1

    analysis = json.loads(Path(analysis_path).read_text())
    existing = json.loads(Path(output_path).read_text()) if Path(output_path).exists() else {}

    scores = {}
    for name, metrics in analysis.items():
        scores[name] = score_metrics(metrics, bands)

    for name in sorted(scores):
        entry = scores[name]
        print(f"{name:28s} fitness={entry['fitness']:3d}  lowest={_lowest_metric(entry['per_metric'])}")

    merged = existing
    for name, entry in scores.items():
        merged = {**merged, name: entry}

    _write_json_atomic(output_path, merged)
    print(f"\n{len(scores)} scored -> {output_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "analysis", nargs="?", default=str(ANALYSIS_PATH), help="audio_analysis.json path"
    )
    parser.add_argument(
        "--rebuild-bands",
        action="store_true",
        help="rebuild corpus_metric_bands.json from patch_playability.json + corpus_audio_analysis.json",
    )
    parser.add_argument("--bands", default=str(BANDS_PATH), help="bands file to score against")
    parser.add_argument("--output", default=str(SCORES_PATH), help="scores output path")
    args = parser.parse_args()

    if args.rebuild_bands:
        _rebuild_bands()
        return 0

    return _score_all(args.analysis, args.bands, args.output)


if __name__ == "__main__":
    sys.exit(main())
