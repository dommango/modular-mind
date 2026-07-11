"""LLM-driven patch generation loop (Loop 3): draft -> validate -> render -> analyze -> revise.

Orchestrates llm_driver + llm_prompts + validate_patch + render_client +
analyze_audio + score_audio into a bounded iteration loop. Each iteration
asks the driver for a patch, checks it against the headless-render
whitelist, structurally validates it, renders + analyzes the audio, and
scores it against the corpus's acoustic bands (score_audio) — accepting it
once it is structurally sound, makes sound cleanly, and scores at or above
`target_score`, or else folding a critique into the next revision prompt.

Every iteration — however far it gets — is appended to a JSONL trajectory
log (one record per line, opened fresh per write) so a run can be
inspected or replayed without re-rendering. No step here ever raises past
run_loop(): a driver failure ends the run with status "driver_error"; every
other failure (bad JSON, whitelist violation, render error, analyze error)
is logged with its critique and the loop moves to the next iteration.

Usage:
  python3 llm_patch_loop.py --archetype drone
  python3 llm_patch_loop.py --archetype sequenced-melody --max-iterations 8 --target-score 70
"""

import argparse
import hashlib
import importlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile

from analyze_audio import ANALYSIS_PATH, analyze_file
from audition import is_good
from config import DATA_DIR, OUTPUT_DIR
from llm_driver import DriverError, get_driver
from llm_prompts import (
    build_context,
    build_critique,
    build_initial_prompt,
    build_revision_prompt,
    check_whitelist,
    extract_patch_json,
    param_stats_for_prompt,
)
from render_client import render as render_client_render
from render_patch import RenderError, patch_slug
from score_audio import band_summary, load_bands, score_metrics
from validate_patch import PatchValidator

LLM_LOOP_DIR = DATA_DIR / "llm_loop"
CANDIDATES_DIR = LLM_LOOP_DIR / "candidates"
TRAJECTORIES_DIR = LLM_LOOP_DIR / "trajectories"
LLM_OUT_DIR = DATA_DIR / "generated" / "llm"
DEFAULT_TARGET_SCORE = 60
DEFAULT_MAX_ITERATIONS = 5


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _default_param_stats():
    """Corpus param-stats block for the prompt, or "" if either the stage 10
    port maps or the param distributions artifact is unavailable."""
    try:
        port_maps = importlib.import_module("10_build_knowledge_base").PORT_MAPS
    except (ImportError, AttributeError):
        return ""
    dist_path = OUTPUT_DIR / "param_distributions.json"
    if not dist_path.exists():
        return ""
    try:
        distributions = json.loads(dist_path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return param_stats_for_prompt(port_maps, distributions)


def log_iteration(log_path, record):
    """Append one JSON record + newline to `log_path`, opened fresh per call
    so a crash mid-run never corrupts already-written iterations."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _whitelist_critique(violations):
    lines = "\n".join(f"- {v}" for v in violations)
    return (
        f"WHITELIST:\n{lines}\n\n"
        "Return a full corrected patch JSON that addresses every point above."
    )


def _analysis_failure_critique(validation, error, bsum):
    return build_critique(validation, f"audio analysis failed: {error}", None, None, bsum)


def _write_json_atomic(path, payload):
    """Write JSON via a temp file + rename so an interrupted run never
    leaves a truncated artifact behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _append_manifest_entry(manifest_path, entry):
    manifest_path = Path(manifest_path)
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    _write_json_atomic(manifest_path, existing + [entry])


def _merge_audio_analysis(analysis_path, slug, metrics):
    analysis_path = Path(analysis_path)
    existing = json.loads(analysis_path.read_text()) if analysis_path.exists() else {}
    _write_json_atomic(analysis_path, {**existing, slug: metrics})


def _progress_line(i, max_iterations, stage, fitness=None):
    fitness_str = str(fitness) if fitness is not None else "-"
    return f"[{i}/{max_iterations}] {stage:20s} fitness={fitness_str}"


def run_loop(
    archetype,
    max_iterations=DEFAULT_MAX_ITERATIONS,
    target_score=DEFAULT_TARGET_SCORE,
    driver=None,
    render_fn=None,
    analyze_fn=None,
    bands=None,
    context=None,
    param_stats=None,
    out_dir=None,
    candidates_dir=None,
    log_dir=None,
    run_id=None,
    now_fn=None,
    analysis_path=None,
):
    """Run the draft/critique/revise loop for `archetype` up to `max_iterations`
    times. Returns a summary dict with status "accepted", "exhausted", or
    "driver_error" — see module docstring for the per-iteration contract."""
    driver = driver or get_driver()
    render_fn = render_fn or render_client_render
    analyze_fn = analyze_fn or analyze_file
    bands = bands if bands is not None else load_bands()
    context = context if context is not None else build_context()
    param_stats = param_stats if param_stats is not None else _default_param_stats()
    now_fn = now_fn or _utc_now_iso
    run_id = run_id or (
        f"{archetype}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    out_dir = Path(out_dir) if out_dir is not None else LLM_OUT_DIR
    candidates_dir = Path(candidates_dir) if candidates_dir is not None else CANDIDATES_DIR
    log_dir = Path(log_dir) if log_dir is not None else TRAJECTORIES_DIR
    analysis_path = Path(analysis_path) if analysis_path is not None else ANALYSIS_PATH

    bsum = band_summary(bands) if bands else {}
    log_path = log_dir / f"{run_id}.jsonl"

    prev_patch = None
    prev_critique = None
    best_score = 0

    for i in range(1, max_iterations + 1):
        if i == 1:
            prompt = build_initial_prompt(archetype, bsum, param_stats, context)
        else:
            prompt = build_revision_prompt(
                archetype, bsum, param_stats, context, prev_patch, prev_critique
            )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        base_record = {
            "run_id": run_id,
            "iteration": i,
            "timestamp": now_fn(),
            "archetype": archetype,
            "prompt": prompt,
            "prompt_sha256": prompt_sha256,
        }

        try:
            response = driver.complete(prompt)
        except DriverError as e:
            critique = f"driver error: {e}"
            log_iteration(
                log_path,
                {
                    **base_record,
                    "response_raw": None,
                    "patch_json": None,
                    "validation": None,
                    "render": {"status": "skipped", "error": None},
                    "metrics": None,
                    "score": None,
                    "critique": critique,
                    "accepted": False,
                },
            )
            print(_progress_line(i, max_iterations, "driver_error"))
            return {"status": "driver_error", "run_id": run_id, "iterations": i, "error": str(e)}

        try:
            patch = extract_patch_json(response)
        except ValueError as e:
            critique = str(e)
            log_iteration(
                log_path,
                {
                    **base_record,
                    "response_raw": response,
                    "patch_json": None,
                    "validation": None,
                    "render": {"status": "skipped", "error": None},
                    "metrics": None,
                    "score": None,
                    "critique": critique,
                    "accepted": False,
                },
            )
            print(_progress_line(i, max_iterations, "extract_error"))
            prev_critique = critique
            continue

        violations = check_whitelist(patch)
        if violations:
            critique = _whitelist_critique(violations)
            log_iteration(
                log_path,
                {
                    **base_record,
                    "response_raw": response,
                    "patch_json": patch,
                    "validation": None,
                    "render": {"status": "skipped", "error": None},
                    "metrics": None,
                    "score": None,
                    "critique": critique,
                    "accepted": False,
                },
            )
            print(_progress_line(i, max_iterations, "whitelist_violation"))
            prev_patch, prev_critique = patch, critique
            continue

        validator = PatchValidator(patch)
        structural_pass = validator.validate()
        validation = {
            "pass": structural_pass,
            "errors": validator.errors,
            "warnings": validator.warnings,
        }

        candidates_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = candidates_dir / f"{run_id}-i{i}.vcv"
        candidate_path.write_text(json.dumps(patch, indent=2))

        try:
            wav_path = render_fn(candidate_path)
        except (RenderError, ValueError, OSError) as e:
            critique = build_critique(validation, str(e), None, None, bsum)
            log_iteration(
                log_path,
                {
                    **base_record,
                    "response_raw": response,
                    "patch_json": patch,
                    "validation": validation,
                    "render": {"status": "error", "error": str(e)},
                    "metrics": None,
                    "score": None,
                    "critique": critique,
                    "accepted": False,
                },
            )
            print(_progress_line(i, max_iterations, "render_error"))
            prev_patch, prev_critique = patch, critique
            continue

        try:
            metrics = analyze_fn(wav_path)
        except (RuntimeError, ValueError, OSError) as e:
            critique = _analysis_failure_critique(validation, e, bsum)
            log_iteration(
                log_path,
                {
                    **base_record,
                    "response_raw": response,
                    "patch_json": patch,
                    "validation": validation,
                    "render": {"status": "ok", "error": None},
                    "metrics": None,
                    "score": None,
                    "critique": critique,
                    "accepted": False,
                },
            )
            print(_progress_line(i, max_iterations, "analyze_error"))
            prev_patch, prev_critique = patch, critique
            continue

        score = score_metrics(metrics, bands) if bands else {"fitness": 0, "per_metric": {}}
        audition_result = {
            "structural": "PASS" if validation["pass"] else "FAIL",
            "render": "OK",
            "metrics": metrics,
        }
        accepted = is_good(audition_result) and score["fitness"] >= target_score
        critique = build_critique(validation, None, metrics, score, bsum)

        log_iteration(
            log_path,
            {
                **base_record,
                "response_raw": response,
                "patch_json": patch,
                "validation": validation,
                "render": {"status": "ok", "error": None},
                "metrics": metrics,
                "score": score,
                "critique": critique,
                "accepted": accepted,
            },
        )
        best_score = max(best_score, score["fitness"])

        if accepted:
            out_dir.mkdir(parents=True, exist_ok=True)
            # run_id already starts with the archetype — don't double it
            accepted_path = out_dir / f"{run_id}.vcv"
            copyfile(candidate_path, accepted_path)
            _append_manifest_entry(
                out_dir / "manifest.json",
                {
                    "name": run_id,
                    "archetype": archetype,
                    "source": "llm",
                    "run_id": run_id,
                    "iterations": i,
                    "score": score["fitness"],
                },
            )
            _merge_audio_analysis(analysis_path, patch_slug(accepted_path), metrics)
            print(_progress_line(i, max_iterations, "accepted", score["fitness"]))
            return {
                "status": "accepted",
                "run_id": run_id,
                "iterations": i,
                "accepted_path": str(accepted_path),
                "score": score["fitness"],
            }

        print(_progress_line(i, max_iterations, "scored", score["fitness"]))
        prev_patch, prev_critique = patch, critique

    return {
        "status": "exhausted",
        "run_id": run_id,
        "iterations": max_iterations,
        "best_score": best_score,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archetype", required=True, help="archetype name to design for")
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    parser.add_argument("--target-score", type=int, default=DEFAULT_TARGET_SCORE)
    parser.add_argument("--driver", default="claude-cli", help="driver name (see llm_driver.py)")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    summary = run_loop(
        args.archetype,
        max_iterations=args.max_iterations,
        target_score=args.target_score,
        driver=get_driver(args.driver),
        run_id=args.run_id,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("status") == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
