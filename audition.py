"""Audition generated patches: validate -> render -> analyze -> record.

The first recorded quality signal for generated patches. For every .vcv
under data/generated/ this runs the structural validator, renders it to
WAV via headless Rack (render_patch), analyzes the audio
(analyze_audio), merges all analyzer results into
data/output/audio_analysis.json, attaches verdicts to
data/generated/batch3/manifest.json entries, and prints a summary table.

A patch is considered good when it passes structural validation, makes
sound, and carries no flags.

Usage:
  python3 audition.py                      # all of data/generated/
  python3 audition.py data/generated/batch3
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

from analyze_audio import ANALYSIS_PATH, analyze_file, load_existing
from config import DATA_DIR
from render_patch import RenderError, collect_patches, patch_slug, render
from validate_patch import PatchValidator

parse_vcv = importlib.import_module("03_parse_and_filter").parse_vcv

GENERATED_DIR = DATA_DIR / "generated"
BATCH3_MANIFEST = GENERATED_DIR / "batch3" / "manifest.json"


def audition_one(vcv_path):
    """Validate, render and analyze a single patch. Returns a result dict."""
    patch = parse_vcv(Path(vcv_path))
    validator = PatchValidator(patch)
    structural_pass = validator.validate()

    result = {
        "structural": "PASS" if structural_pass else "FAIL",
        "structural_errors": len(validator.errors),
        "structural_warnings": len(validator.warnings),
    }

    try:
        wav = render(vcv_path)
    except (RenderError, ValueError, OSError) as e:
        return {**result, "render": "FAIL", "render_error": str(e)}

    try:
        metrics = analyze_file(wav)
    except (RuntimeError, ValueError, OSError) as e:
        return {**result, "render": "OK", "analyze_error": str(e)}
    return {**result, "render": "OK", "metrics": metrics}


def merge_manifest(entries, results):
    """Return manifest entries with an `audio` block attached where the
    patch name has an audition result."""
    merged = []
    for entry in entries:
        result = results.get(entry.get("name"))
        if result is None:
            merged.append(entry)
            continue
        audio = {
            "structural": result["structural"],
            "render": result["render"],
        }
        metrics = result.get("metrics")
        if metrics:
            audio = {
                **audio,
                "makes_sound": metrics["verdict"]["makes_sound"],
                "character": metrics["verdict"]["character"],
                "flags": metrics["verdict"]["flags"],
                "rms": metrics["rms"],
                "peak": metrics["peak"],
            }
        merged.append({**entry, "audio": audio})
    return merged


def is_good(result):
    metrics = result.get("metrics")
    return bool(
        metrics
        and result["structural"] == "PASS"
        and result["render"] == "OK"
        and metrics["verdict"]["makes_sound"]
        and not metrics["verdict"]["flags"]
    )


def summary_line(name, result):
    if result["render"] != "OK":
        detail = result.get("render_error", "render failed")
        return f"{name:28s} {result['structural']:4s} RENDER-FAIL  {detail}"
    if not result.get("metrics"):
        detail = result.get("analyze_error", "analysis failed")
        return f"{name:28s} {result['structural']:4s} ANALYZE-FAIL {detail}"
    v = result["metrics"]["verdict"]
    flag_str = ",".join(v["flags"]) or "-"
    good = "GOOD" if is_good(result) else "    "
    return (
        f"{name:28s} {result['structural']:4s} {v['character']:9s} "
        f"rms={result['metrics']['rms']:.3f} peak={result['metrics']['peak']:.2f} "
        f"flags={flag_str:22s} {good}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="*", default=[str(GENERATED_DIR)], help=".vcv files or dirs"
    )
    args = parser.parse_args()

    files = collect_patches(args.paths)
    if not files:
        print("No .vcv files found")
        return 1

    results = {}
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name} ...", flush=True)
        slug = patch_slug(f)
        try:
            results[slug] = audition_one(f)
        except (RuntimeError, ValueError, OSError) as e:
            results[slug] = {
                "structural": "FAIL",
                "structural_errors": -1,
                "structural_warnings": 0,
                "render": "FAIL",
                "render_error": f"unreadable patch: {e}",
            }

    # merge analyzer metrics into the shared analysis artifact
    analysis = load_existing()
    for name, result in results.items():
        if result.get("metrics"):
            analysis = {**analysis, name: result["metrics"]}
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_PATH.write_text(json.dumps(analysis, indent=2))

    if BATCH3_MANIFEST.exists():
        # manifest entries are named by file stem; map them to slug-keyed results
        batch3_dir = BATCH3_MANIFEST.parent.resolve()
        stem_results = {
            f.stem: results[patch_slug(f)]
            for f in files
            if f.resolve().parent == batch3_dir and patch_slug(f) in results
        }
        entries = json.loads(BATCH3_MANIFEST.read_text())
        for entry in entries:
            if entry.get("name") not in stem_results:
                print(f"WARN: no audition result for manifest entry {entry.get('name')}")
        BATCH3_MANIFEST.write_text(
            json.dumps(merge_manifest(entries, stem_results), indent=2)
        )

    print("\n=== Audition summary ===")
    for name in sorted(results):
        print(summary_line(name, results[name]))
    good = sum(1 for r in results.values() if r["render"] == "OK" and is_good(r))
    rendered = sum(1 for r in results.values() if r["render"] == "OK")
    print(f"\n{rendered}/{len(results)} rendered, {good}/{len(results)} good")
    return 0 if rendered == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
