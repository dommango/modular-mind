"""Loop 1 orchestrator: iteratively repair a generated patch's audio flags.

Wraps repair_patch.py's pure gain/DC-blocker transforms in a
validate -> render -> analyze cycle. For each patch: get a baseline
(caller-supplied or freshly rendered), and if it has any analyzer flags,
repeatedly ask apply_repairs() for a new variant, write it to
data/repaired/, render and analyze it, and accept the first variant that
clears audition.is_good. Stops early when apply_repairs runs out of
changes to propose, or after max_attempts.

Usage:
  python3 repair_loop.py                       # all of data/generated/
  python3 repair_loop.py data/generated/batch3 --max-attempts 5
  python3 repair_loop.py --fresh-baseline       # ignore audio_analysis.json baselines
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

from analyze_audio import ANALYSIS_PATH, analyze_file, load_existing
from audition import BATCH3_MANIFEST, GENERATED_DIR, is_good
from config import DATA_DIR, OUTPUT_DIR
from render_client import render as default_render
from render_patch import RenderError, collect_patches, patch_slug
from repair_patch import apply_repairs
from score_audio import load_bands
from validate_patch import PatchValidator

parse_vcv = importlib.import_module("03_parse_and_filter").parse_vcv

REPAIRED_DIR = DATA_DIR / "repaired"
REPAIR_LOG = OUTPUT_DIR / "repair_log.json"


def _is_clean(metrics):
    return not metrics.get("verdict", {}).get("flags", [])


def _baseline_summary(metrics, source):
    return {
        "flags": metrics.get("verdict", {}).get("flags", []),
        "rms": metrics.get("rms"),
        "peak": metrics.get("peak"),
        "dc_offset": metrics.get("dc_offset"),
        "source": source,
    }


def _attempt_record(n, variant_path, changes, structural, render_status, **extra):
    return {
        "attempt": n,
        "file": str(variant_path),
        "changes": changes,
        "structural": structural,
        "render": render_status,
        **extra,
    }


def _run_attempts(slug, patch, metrics, max_attempts, render_fn, analyze_fn, bands, out_dir):
    """The repair/render/analyze cycle shared by repair_one. Returns
    (status, attempts, accepted)."""
    attempts = []
    for n in range(1, max_attempts + 1):
        new_patch, changes = apply_repairs(patch, metrics, bands)
        if not changes:
            return "gave_up", attempts, None

        variant_path = out_dir / f"{slug}-r{n}.vcv"
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        variant_path.write_text(json.dumps(new_patch, indent=2))
        structural = "PASS" if PatchValidator(new_patch).validate() else "FAIL"

        try:
            wav = render_fn(variant_path)
        except (RenderError, ValueError, OSError) as e:
            attempts.append(
                _attempt_record(
                    n, variant_path, changes, structural, "FAIL",
                    render_error=str(e), flags=None, rms=None, peak=None, good=False,
                )
            )
            return "render_failed", attempts, None

        try:
            result_metrics = analyze_fn(wav)
        except (RuntimeError, ValueError, OSError) as e:
            attempts.append(
                _attempt_record(
                    n, variant_path, changes, structural, "OK",
                    analyze_error=str(e), flags=None, rms=None, peak=None, good=False,
                )
            )
            return "analyze_failed", attempts, None

        good = is_good({"structural": structural, "render": "OK", "metrics": result_metrics})
        attempts.append(
            _attempt_record(
                n, variant_path, changes, structural, "OK",
                flags=result_metrics["verdict"]["flags"], rms=result_metrics["rms"],
                peak=result_metrics["peak"], good=good, metrics=result_metrics,
            )
        )
        if good:
            return "repaired", attempts, str(variant_path)

        patch, metrics = new_patch, result_metrics

    return "gave_up", attempts, None


def repair_one(
    vcv_path,
    baseline_metrics=None,
    max_attempts=3,
    render_fn=None,
    analyze_fn=None,
    bands=None,
    out_dir=REPAIRED_DIR,
    target_peak=None,
):
    """Iteratively repair one patch's audio flags. Returns a result dict:
    original/slug/status/baseline/attempts/accepted.

    status is one of:
      clean           baseline already had no flags, nothing to do
      repaired        a variant passed audition.is_good
      gave_up         apply_repairs stopped proposing changes, or
                      max_attempts was exhausted without a good variant
      render_failed   a variant's render_fn call raised
      analyze_failed  a variant rendered but analyze_fn raised

    baseline_metrics is used as-is when supplied; when None, this renders
    and analyzes the original file itself to get one.

    target_peak is currently unused: apply_repairs (repair_patch.py) has no
    target_peak passthrough, so there is nothing to thread it into without
    editing that module.
    """
    render_fn = render_fn if render_fn is not None else default_render
    analyze_fn = analyze_fn if analyze_fn is not None else analyze_file

    vcv_path = Path(vcv_path)
    original_patch = parse_vcv(vcv_path)
    slug = patch_slug(vcv_path)

    source = "supplied"
    if baseline_metrics is None:
        baseline_metrics = analyze_fn(render_fn(vcv_path))
        source = "rendered"
    baseline = _baseline_summary(baseline_metrics, source)
    shell = {
        "original": str(vcv_path),
        "slug": slug,
        "baseline": baseline,
        "baseline_metrics": baseline_metrics,
    }

    if _is_clean(baseline_metrics):
        return {**shell, "status": "clean", "attempts": [], "accepted": None}

    status, attempts, accepted = _run_attempts(
        slug, original_patch, baseline_metrics, max_attempts,
        render_fn, analyze_fn, bands, Path(out_dir),
    )
    return {**shell, "status": status, "attempts": attempts, "accepted": accepted}


def merge_repair_manifest(entries, repair_results):
    """Return manifest entries with a `repair` block attached where the
    patch name has a repair result. Mirrors audition.merge_manifest's shape."""
    merged = []
    for entry in entries:
        result = repair_results.get(entry.get("name"))
        if result is None:
            merged.append(entry)
            continue
        repair = {"status": result["status"], "accepted": result.get("accepted")}
        merged.append({**entry, "repair": repair})
    return merged


def _merge_attempt_metrics(analysis, attempts):
    for attempt in attempts:
        metrics = attempt.get("metrics")
        if metrics:
            # patch_slug, not bare stem: it's what render() named the WAV,
            # and the two diverge for out-dirs nested under data/
            variant_slug = patch_slug(Path(attempt["file"]))
            analysis = {**analysis, variant_slug: metrics}
    return analysis


def _write_json_atomic(path, payload):
    """Write JSON via a temp file + rename so an interrupted run never
    leaves a truncated artifact behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _summary_line(slug, result):
    accepted = result["accepted"] or "-"
    return (
        f"{slug:28s} {result['status']:14s} "
        f"attempts={len(result['attempts'])} accepted={accepted}"
    )


def _update_batch3_manifest(files, results):
    batch3_dir = BATCH3_MANIFEST.parent.resolve()
    stem_results = {
        f.stem: results[patch_slug(f)]
        for f in files
        if f.resolve().parent == batch3_dir and patch_slug(f) in results
    }
    entries = json.loads(BATCH3_MANIFEST.read_text())
    for entry in entries:
        if entry.get("name") not in stem_results:
            print(f"WARN: no repair result for manifest entry {entry.get('name')}")
    BATCH3_MANIFEST.write_text(
        json.dumps(merge_repair_manifest(entries, stem_results), indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="*", default=[str(GENERATED_DIR)], help=".vcv files or dirs"
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="repair attempts per patch")
    parser.add_argument(
        "--fresh-baseline",
        action="store_true",
        help="ignore data/output/audio_analysis.json, render+analyze the baseline fresh",
    )
    parser.add_argument("--out-dir", default=str(REPAIRED_DIR), help="where repaired variants go")
    args = parser.parse_args()

    files = collect_patches(args.paths)
    if not files:
        print("No .vcv files found")
        return 1

    bands = load_bands()
    existing_analysis = load_existing()
    out_dir = Path(args.out_dir)

    results = {}
    analysis = existing_analysis
    for i, f in enumerate(files, 1):
        slug = patch_slug(f)
        print(f"[{i}/{len(files)}] {f.name} ...", flush=True)
        baseline = None if args.fresh_baseline else existing_analysis.get(slug)
        try:
            result = repair_one(
                f, baseline_metrics=baseline, max_attempts=args.max_attempts,
                bands=bands, out_dir=out_dir,
            )
        except (RuntimeError, ValueError, OSError) as e:
            result = {
                "original": str(f), "slug": slug, "status": "render_failed",
                "baseline": {}, "attempts": [], "accepted": None, "error": str(e),
            }
        results[slug] = result
        print(f"    -> {result['status']}")
        analysis = _merge_attempt_metrics(analysis, result["attempts"])
        if result.get("baseline", {}).get("source") == "rendered" and result.get(
            "baseline_metrics"
        ):
            # a freshly rendered baseline is a real ~12s measurement — keep it
            analysis = {**analysis, slug: result["baseline_metrics"]}

    _write_json_atomic(ANALYSIS_PATH, analysis)

    log = json.loads(REPAIR_LOG.read_text()) if REPAIR_LOG.exists() else {}
    for slug, result in results.items():
        log = {**log, slug: result}
    _write_json_atomic(REPAIR_LOG, log)

    if BATCH3_MANIFEST.exists():
        _update_batch3_manifest(files, results)

    print("\n=== Repair summary ===")
    counts = {}
    for slug in sorted(results):
        result = results[slug]
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        print(_summary_line(slug, result))
    print("\n" + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    return 1 if counts.get("render_failed") or counts.get("analyze_failed") else 0


if __name__ == "__main__":
    sys.exit(main())
