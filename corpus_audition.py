"""Audition known corpus patches: sync plugins -> render -> analyze.

Plays real, community-liked patches from data/raw/ through the same
render+analyze loop used for generated patches, producing acoustic
ground truth: what structurally good patches actually sound like, tied
to the parameter settings that produced it. Downstream, these metrics
calibrate the generator's targets (analyze_audio verdicts on corpus
patches vs generated ones).

Rendering uses render_patch.render() unchanged — on the Railway image
that resolves to the native lin-x64 Rack build (see render-service/
Dockerfile, which this worker shares); locally it's the WSL-interop path.

Results accumulate in data/output/corpus_audio_analysis.json keyed by
patch id, written after every patch (crash-safe, resumable — re-runs
skip ids already present unless --redo).

Usage:
  python3 corpus_audition.py --limit 20            # next 20 unprocessed
  python3 corpus_audition.py --ids 100003 100080   # specific patches
  python3 corpus_audition.py --summary             # just print stats
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

from analyze_audio import analyze_file
from config import AUDIO_DIR, OUTPUT_DIR, RACK_RENDER_URL, RAW_DIR
from plugin_sync import ensure_plugins, missing_for_arch
from render_client import render
from render_patch import RenderError

parse_vcv = importlib.import_module("03_parse_and_filter").parse_vcv

CORPUS_ANALYSIS_PATH = OUTPUT_DIR / "corpus_audio_analysis.json"
FILTERED_PATH = OUTPUT_DIR / "filtered_patches.json"


def load_results():
    if CORPUS_ANALYSIS_PATH.exists():
        return json.loads(CORPUS_ANALYSIS_PATH.read_text())
    return {}


def save_results(results):
    CORPUS_ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_ANALYSIS_PATH.write_text(json.dumps(results, indent=2))


def corpus_ids():
    """Filtered-corpus patch ids that have a downloaded .vcv, in id order."""
    patches = json.loads(FILTERED_PATH.read_text())
    return [
        str(p["id"]) for p in patches if (RAW_DIR / f"{p['id']}.vcv").exists()
    ]


def patch_plugins(patch):
    return sorted(
        {m.get("plugin") for m in patch.get("modules", [])} - {"Core", None}
    )


def is_renderable_format(patch):
    """The render recipe needs the Rack v1+ patch shape: id-keyed modules
    and a `cables` list. Pre-v1 patches (~2% of the corpus) address modules
    by array index and store connections under `wires` — the recorder
    injection can't wire into those, and Rack 2 won't load them anyway."""
    modules = patch.get("modules", [])
    return "cables" in patch and all("id" in m for m in modules)


def audition_one(patch_id):
    """Sync plugins, render and analyze one corpus patch. Returns a result
    dict with status ok | old-format | missing-plugins | render-fail |
    analyze-fail."""
    vcv_path = RAW_DIR / f"{patch_id}.vcv"
    patch = parse_vcv(vcv_path)
    if not is_renderable_format(patch):
        return {"status": "old-format", "version": patch.get("version")}
    plugins = patch_plugins(patch)

    if RACK_RENDER_URL:
        # Remote render-service (lin-x64) downloads the plugins itself; only
        # screen out patches whose plugins have no lin-x64 build.
        missing = missing_for_arch(plugins, "lin-x64")
    else:
        missing = ensure_plugins(plugins)["missing"]
    if missing:
        return {"status": "missing-plugins", "plugins": plugins, "missing": missing}

    try:
        wav = render(vcv_path, out_path=AUDIO_DIR / "corpus" / f"{patch_id}.wav")
    except (RenderError, ValueError, OSError, KeyError) as e:
        return {"status": "render-fail", "plugins": plugins, "error": str(e)}

    try:
        metrics = analyze_file(wav)
    except (RuntimeError, ValueError, OSError) as e:
        return {"status": "analyze-fail", "plugins": plugins, "error": str(e)}
    return {"status": "ok", "plugins": plugins, "metrics": metrics}


def print_summary(results):
    by_status = {}
    for r in results.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"{len(results)} corpus patches auditioned")
    for status in sorted(by_status):
        print(f"  {status:16s} {by_status[status]}")
    ok = [r["metrics"] for r in results.values() if r["status"] == "ok"]
    sounding = [m for m in ok if m["verdict"]["makes_sound"]]
    if ok:
        print(f"  makes_sound      {len(sounding)}/{len(ok)}")
    if sounding:
        chars = {}
        for m in sounding:
            chars[m["verdict"]["character"]] = chars.get(m["verdict"]["character"], 0) + 1
        rms = sorted(m["rms"] for m in sounding)
        print(f"  rms median       {rms[len(rms) // 2]:.3f}")
        print("  character        " + ", ".join(f"{k}={v}" for k, v in sorted(chars.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="max patches this run")
    parser.add_argument("--ids", nargs="*", default=None, help="specific patch ids")
    parser.add_argument("--redo", action="store_true", help="re-audition existing ids")
    parser.add_argument("--summary", action="store_true", help="print stats and exit")
    args = parser.parse_args()

    results = load_results()
    if args.summary:
        print_summary(results)
        return 0

    ids = args.ids if args.ids else corpus_ids()
    if not args.redo:
        ids = [i for i in ids if i not in results]
    if args.limit is not None:
        ids = ids[: args.limit]
    if not ids:
        print("Nothing to do — all selected patches already auditioned")
        return 0

    failures = 0
    for i, patch_id in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {patch_id} ...", flush=True)
        try:
            result = audition_one(patch_id)
        except (RuntimeError, ValueError, OSError) as e:
            result = {"status": "unreadable", "error": str(e)}
        results = {**results, patch_id: result}
        save_results(results)
        detail = result.get("error") or ",".join(result.get("missing", [])) or ""
        if result["status"] != "ok":
            failures += 1
        print(f"    {result['status']} {detail}".rstrip(), flush=True)

    print()
    print_summary(results)
    return 1 if failures == len(ids) else 0


if __name__ == "__main__":
    sys.exit(main())
