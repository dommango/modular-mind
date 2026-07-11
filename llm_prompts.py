"""Prompt construction for LLM-driven patch generation (Loop 3).

Pure text in/out — no network calls, no filesystem writes except the
read-only reference docs loaded by build_context(). Builds the shared
context block (patch format spec + corpus reference docs), the initial
and revision prompts sent to an LLM driver (see llm_driver.py), and the
critique fed back after a failed structural/render/audio/score check.
Also extracts a patch dict back out of a free-form LLM reply and flags
any module outside the headless-renderable whitelist.

Usage:
  from llm_prompts import build_context, build_initial_prompt, extract_patch_json
  context = build_context()
  prompt = build_initial_prompt("drone", band_summary, param_stats, context)
  reply = driver.complete(prompt)
  patch = extract_patch_json(reply)
"""

import json
import re

from config import DATA_DIR
from render_patch import AUDIO_INTERFACE_MODELS

ALLOWED_PLUGINS = {"Fundamental"}
ALLOWED_CORE_MODELS = AUDIO_INTERFACE_MODELS

WHITELIST_REASON = (
    "only Fundamental modules and Core audio interfaces are available in the headless renderer"
)

PATCH_FORMAT_SPEC = """PATCH JSON FORMAT:
A .vcv patch is one JSON object with top-level keys "version", "modules",
and "cables" (a "masterModuleId" key may also appear and can be ignored).

- "version": patch-format version string, always "1.1.6".
- "modules": a list of module objects, each:
    {"id": <unique int>, "plugin": <str>, "model": <str>, "version": "2.0.0",
     "params": [{"id": <int>, "value": <float>}, ...], "pos": [<x>, <y>]}
  "params" is SPARSE and index-keyed by port id — omit any param left at
  its default, never pad with zeros. "pos" is a grid position in module
  widths ([x, y], not pixels); lay modules out left-to-right roughly in
  signal-flow order so the patch is readable.
- "cables": a list of connection objects, each:
    {"id": <unique int>, "outputModuleId": <int>, "outputId": <int>,
     "inputModuleId": <int>, "inputId": <int>}
  outputModuleId/inputModuleId reference module "id" values above;
  outputId/inputId are port indices on that module (not names).
- Every "id" (module ids and cable ids, each in their own namespace) must
  be a unique positive integer within the patch.

HEADLESS RENDERING CONSTRAINTS (non-negotiable):
- ONLY Fundamental-plugin modules and a Core:AudioInterface module render
  in the headless pipeline — any other plugin will fail to load.
- The patch MUST cable audio into Core:AudioInterface inputs 0 AND 1
  (left and right) — a patch that only feeds one channel is rejected.
- The patch must be self-playing: it runs and produces sound with no
  MIDI device and no external input, driven only by its own internal
  LFOs, sequencers, or gates.
"""

REFERENCE_DOCS = (
    "INDEX.md",
    "module-quick-ref.md",
    "archetypes.md",
    "connection-grammar.md",
    "synthesis-fundamentals.md",
    "voice-patterns.md",
    "patch-building-guide.md",
)

HARD_RULES = """HARD RULES:
- Only Fundamental modules and Core:AudioInterface render headless — no other plugin, ever.
- Cable audio into Core:AudioInterface inputs 0 AND 1 (both channels).
- The patch must be self-playing: no MIDI, no external input — only internal LFOs/sequencers/gates.
- Return EXACTLY ONE fenced ```json code block containing the full patch, and nothing else."""

_METRIC_ADVICE = {
    "rms": ("raise output gain", "lower output gain"),
    "peak": ("raise output level", "add headroom / reduce output level"),
    "spectral_centroid_hz": (
        "brighten the tone (raise filter cutoff)",
        "darken the tone (lower filter cutoff)",
    ),
    "spectral_bandwidth_hz": (
        "add richer harmonic content",
        "narrow the spectral content",
    ),
    "spectral_flatness": (
        "add more tonal structure, less noise-like",
        "add more noise/texture",
    ),
    "onset_rate": ("add more rhythmic activity", "reduce rhythmic density"),
    "voiced_ratio": ("add more pitched/tonal content", "reduce pitched content"),
    "silent_ratio": (
        "keep a sound source active more of the time",
        "allow more space/silence",
    ),
}

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def build_context(reference_dir=DATA_DIR / "reference"):
    """Concatenate the patch format spec with whichever reference docs
    exist under `reference_dir`, skipping missing ones silently."""
    parts = [PATCH_FORMAT_SPEC]
    for name in REFERENCE_DOCS:
        path = reference_dir / name
        if path.exists():
            parts.append(f"## {name}\n\n{path.read_text()}")
    return "\n\n".join(parts)


def param_stats_for_prompt(port_maps, distributions):
    """Compact corpus-median lines for every Fundamental param that has a
    matching distribution entry; misses are skipped."""
    lines = []
    for key in sorted(k for k in port_maps if k.startswith("Fundamental:")):
        params = port_maps[key].get("params", {})
        for param_id in sorted(params, key=int):
            name = params[param_id]
            dist = distributions.get(f"{key}:{name}")
            if not dist:
                continue
            lines.append(
                f"{key} param {int(param_id)} ({name}): corpus median "
                f"{dist['median']:.2f}, range [{dist['min']:.2f}, {dist['max']:.2f}]"
            )
    return "\n".join(lines)


def _format_band_summary(band_summary):
    lines = []
    for metric in sorted(band_summary):
        band = band_summary[metric]
        lines.append(
            f"{metric}: p10={band['p10']:.3f} p50={band['p50']:.3f} p90={band['p90']:.3f}"
        )
    return "\n".join(lines)


def build_initial_prompt(archetype, band_summary, param_stats, context):
    """First-draft prompt: context, task, target bands, corpus param
    stats, hard rules stated at both top and bottom."""
    return f"""{HARD_RULES}

{context}

TASK: design a "{archetype}" patch — a self-playing VCV Rack patch built
only from Fundamental modules and Core:AudioInterface — matching the
target acoustic profile below.

TARGET ACOUSTIC BANDS (self-playing corpus, 10th/50th/90th percentile):
{_format_band_summary(band_summary)}

CORPUS PARAMETER STATISTICS:
{param_stats}

{HARD_RULES}
"""


def build_revision_prompt(
    archetype, band_summary, param_stats, context, prev_patch_json, critique
):
    """Revision prompt: same context/bands/stats as the initial prompt,
    plus the previous patch (pretty-printed) and the critique to fix."""
    pretty_patch = json.dumps(prev_patch_json, indent=2)
    return f"""{HARD_RULES}

{context}

TASK: revise the "{archetype}" patch below to fix every issue listed under
WHAT TO FIX, while keeping it a self-playing patch built only from
Fundamental modules and Core:AudioInterface, matching the target acoustic
profile below.

TARGET ACOUSTIC BANDS (self-playing corpus, 10th/50th/90th percentile):
{_format_band_summary(band_summary)}

CORPUS PARAMETER STATISTICS:
{param_stats}

PREVIOUS PATCH:
```json
{pretty_patch}
```

WHAT TO FIX:
{critique}

{HARD_RULES}
"""


def _balanced_candidates(text):
    """Balanced {...} spans at successive top-level positions, in order.

    Stops dead at the first opening brace whose object never closes: that
    is a truncated reply, and scanning further would only surface inner
    fragments of the very object that got cut off.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        end = None
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            return
        yield text[start : end + 1]
        start = text.find("{", end + 1)


def extract_patch_json(text):
    """Extract the patch dict from an LLM reply.

    Prefers the last fenced code block (```json or bare ```), falling
    back to the first balanced top-level {...} object in the raw text —
    never an inner fragment of a truncated object. Raises ValueError with
    a message suitable to feed straight back as a critique.
    """
    matches = _FENCE_RE.findall(text)
    candidates = [matches[-1]] if matches else list(_balanced_candidates(text))

    if not candidates:
        raise ValueError(
            "your reply contained no parseable JSON object: no fenced code "
            "block or complete balanced { } found (was the reply truncated?)"
        )

    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
            continue
        if isinstance(parsed, dict):
            return parsed
        last_error = "top-level value is not an object"

    raise ValueError(f"your reply contained no parseable JSON object: {last_error}")


def _fmt(value):
    return "?" if value is None else f"{value:.4g}"


def _advice_for(metric, value, p50):
    low, high = _METRIC_ADVICE.get(metric, ("increase it", "decrease it"))
    if p50 is None or value is None or value < p50:
        return low
    return high


def _structural_section(validation):
    if not validation or not validation.get("errors"):
        return None
    lines = "\n".join(f"- {e}" for e in validation["errors"])
    return f"STRUCTURAL:\n{lines}"


def _render_section(render_error):
    return f"RENDER:\n{render_error}" if render_error else None


def _audio_section(metrics):
    if not metrics:
        return None
    verdict = metrics.get("verdict", {})
    lines = [
        f"makes_sound: {verdict.get('makes_sound')}",
        f"flags: {', '.join(verdict.get('flags', [])) or '-'}",
    ]
    for key in (
        "rms",
        "peak",
        "dc_offset",
        "spectral_centroid_hz",
        "spectral_flatness",
        "onset_rate",
        "voiced_ratio",
    ):
        if key in metrics:
            lines.append(f"{key}: {metrics[key]}")
    return "AUDIO:\n" + "\n".join(lines)


def _score_section(score_result, band_summary):
    if not score_result:
        return None
    lines = [f"fitness: {score_result.get('fitness')}"]
    for metric, info in score_result.get("per_metric", {}).items():
        if info.get("score", 1.0) >= 0.5:
            continue
        p50 = band_summary.get(metric, {}).get("p50")
        advice = _advice_for(metric, info.get("value"), p50)
        lines.append(
            f"{metric}={_fmt(info.get('value'))} sits at percentile "
            f"{info.get('percentile', 0.0):.2f} of the self-playing corpus "
            f"(median {_fmt(p50)}) — {advice}"
        )
    return "SCORE:\n" + "\n".join(lines)


def build_critique(validation, render_error, metrics, score_result, band_summary):
    """Feedback for a revision prompt: STRUCTURAL / RENDER / AUDIO / SCORE
    sections, each included only when applicable, ending with a single
    instruction to return a full corrected patch."""
    sections = [
        s
        for s in (
            _structural_section(validation),
            _render_section(render_error),
            _audio_section(metrics),
            _score_section(score_result, band_summary),
        )
        if s
    ]
    sections.append("Return a full corrected patch JSON that addresses every point above.")
    return "\n\n".join(sections)


def check_whitelist(patch):
    """One violation string per module outside the headless-renderable
    whitelist (Fundamental + Core:AudioInterface); empty list when clean.
    Malformed entries (non-list modules, non-dict module) are violations
    too — the LLM's JSON is syntactically valid but structurally wrong."""
    modules = patch.get("modules")
    if not isinstance(modules, list):
        return ['patch has no "modules" list']
    violations = []
    for index, m in enumerate(modules):
        if not isinstance(m, dict):
            violations.append(f"modules[{index}] is not an object")
            continue
        plugin, model = m.get("plugin"), m.get("model")
        if plugin == "Core" and model not in ALLOWED_CORE_MODELS:
            violations.append(f"module {m.get('id')} (Core:{model}) — {WHITELIST_REASON}")
        elif plugin != "Core" and plugin not in ALLOWED_PLUGINS:
            violations.append(f"module {m.get('id')} ({plugin}:{model}) — {WHITELIST_REASON}")
    return violations
