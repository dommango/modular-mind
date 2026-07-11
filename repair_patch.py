"""Pure patch-repair transforms: gain scaling and DC-blocker insertion.

Given a patch dict and the analyzer's metrics/verdict (see analyze_audio.py),
these functions compute a *repaired* patch — never mutating the input — plus
a list of the changes applied. No I/O, no rendering: callers (a future
audition-loop orchestrator) own reading/writing files and re-rendering.

The gain fixes walk upstream from the cables feeding Core:AudioInterface(2/16)
to find the nearest module with a known gain-control param (VCMixer, VCA-1,
the interface's own input trim) and scale it toward a target peak/RMS. The DC
fix inserts a Fundamental VCF configured as a high-pass filter between each
audio-interface feed and the interface, since DC offset shows up as an
unwanted near-0Hz component that a VCF's HP output removes.

Usage:
  from repair_patch import apply_repairs
  new_patch, changes = apply_repairs(patch, metrics)
"""

from collections import deque

from render_patch import AUDIO_INTERFACE_MODELS, find_audio_feeds

TARGET_PEAK = 0.7
MIN_SCALE_FACTOR = 0.05
MAX_SCALE_FACTOR = 100.0
# Fundamental VCF cutoff param for a ~25 Hz corner (Hz = 261.63 * 2**(10*v - 5)),
# low enough to pass audio-rate content while blocking DC/near-DC offset.
DC_BLOCK_CUTOFF_PARAM = 0.161
# Median RMS of the 137 non-clipping self-playing corpus patches — matches
# build_bands()'s exclude_clipping=True default, so the fallback agrees with
# what score_metrics grades against when the bands file is unavailable.
FALLBACK_TARGET_RMS = 0.0633

# Nearest-checked-first order matters only in that each entry's specs[0] is
# the stage's primary (overall) gain control; later entries are per-channel
# trims used only when factor > 1 and the primary already caps out.
GAIN_PARAM_SPECS = {
    ("Fundamental", "VCMixer"): [
        {"id": 0, "name": "Mix level", "default": 1.0, "max": 2.0},
        {"id": 1, "name": "Ch1 level", "default": 1.0, "max": 1.41},
        {"id": 2, "name": "Ch2 level", "default": 1.0, "max": 1.41},
        {"id": 3, "name": "Ch3 level", "default": 1.0, "max": 1.41},
        {"id": 4, "name": "Ch4 level", "default": 1.0, "max": 1.41},
    ],
    ("Fundamental", "VCA-1"): [
        {"id": 0, "name": "Level", "default": 1.0, "max": 1.0},
    ],
    ("Core", "AudioInterface2"): [
        {"id": 0, "name": "Level", "default": 1.0, "max": 1.0},
    ],
}


def upstream_gain_stages(patch):
    """Gain-capable modules upstream of the audio interface, nearest first.

    Starts from each interface feed's source module (distance 0) and walks
    backward through cables (a module's inputs come from cables whose
    inputModuleId == that module), breadth-first, deduping visited modules.
    Only modules whose (plugin, model) has a GAIN_PARAM_SPECS entry are
    returned.
    """
    modules_by_id = {m["id"]: m for m in patch.get("modules", [])}
    feeds = find_audio_feeds(patch)

    starts = []
    for source_id, _ in feeds.values():
        if source_id not in starts:
            starts.append(source_id)

    cables = patch.get("cables", [])
    visited = set()
    queue = deque((module_id, 0) for module_id in starts)
    found = []

    while queue:
        module_id, distance = queue.popleft()
        if module_id in visited:
            continue
        visited.add(module_id)
        module = modules_by_id.get(module_id)
        if module is None:
            continue
        if (module.get("plugin"), module.get("model")) in GAIN_PARAM_SPECS:
            found.append((distance, module_id, module))
        for cable in cables:
            if cable.get("inputModuleId") == module_id:
                upstream_id = cable.get("outputModuleId")
                if upstream_id not in visited:
                    queue.append((upstream_id, distance + 1))

    found.sort(key=lambda entry: entry[0])
    return [
        {"module_id": mid, "plugin": m.get("plugin"), "model": m.get("model")}
        for _, mid, m in found
    ]


def _param_value(params, spec):
    """(value, existed) for spec's param id, falling back to spec default
    for sparse (absent) params."""
    for p in params:
        if p.get("id") == spec["id"]:
            return p["value"], True
    return spec["default"], False


def _set_param(params, spec, value):
    """New params list with spec's param id set to value, appending a new
    entry if the param was sparse (absent)."""
    updated = []
    found = False
    for p in params:
        if p.get("id") == spec["id"]:
            updated.append({**p, "value": value})
            found = True
        else:
            updated.append(p)
    if not found:
        updated.append({"id": spec["id"], "value": value})
    return updated


def _gain_change(stage, spec, old, new):
    return {
        "fix": "gain",
        "module_id": stage["module_id"],
        "model": stage["model"],
        "param_id": spec["id"],
        "param_name": spec["name"],
        "old": old,
        "new": new,
    }


def _apply_stage_module(new_modules, index_by_id, stage, params):
    module = new_modules[index_by_id[stage["module_id"]]]
    new_modules[index_by_id[stage["module_id"]]] = {**module, "params": params}


def scale_gain(patch, factor):
    """Scale gain starting at the nearest upstream stage. Returns (new_patch, changes).

    factor < 1 touches only the nearest stage's primary param (specs[0]).
    factor > 1 cascades: once a param's clamp leaves residual gain
    unapplied (residual = remaining / (new/old)), the residual is applied
    to the stage's remaining spec params, then the next upstream stage.
    No gain stages found -> (patch, []) unchanged.
    """
    stages = upstream_gain_stages(patch)
    if not stages:
        return patch, []

    new_modules = list(patch.get("modules", []))
    index_by_id = {m["id"]: i for i, m in enumerate(new_modules)}
    changes = []

    if factor < 1:
        # a stage whose primary gain is already 0 isn't what's passing the
        # signal — skip it and scale the next stage upstream instead
        for stage in stages:
            spec = GAIN_PARAM_SPECS[(stage["plugin"], stage["model"])][0]
            params = list(new_modules[index_by_id[stage["module_id"]]].get("params", []))
            old, _ = _param_value(params, spec)
            if old <= 0:
                continue
            new = max(0.0, min(old * factor, spec["max"]))
            params = _set_param(params, spec, new)
            _apply_stage_module(new_modules, index_by_id, stage, params)
            changes.append(_gain_change(stage, spec, old, new))
            return {**patch, "modules": new_modules}, changes
        return patch, []

    remaining = factor
    for stage in stages:
        specs = GAIN_PARAM_SPECS[(stage["plugin"], stage["model"])]
        params = list(new_modules[index_by_id[stage["module_id"]]].get("params", []))
        touched = False
        for spec in specs:
            old, _ = _param_value(params, spec)
            if old <= 0:
                continue  # 0 * factor stays 0 — this param can't add gain
            attempt = old * remaining
            new = min(attempt, spec["max"])
            if new <= old + 1e-12:
                continue  # already at this param's cap; try the next one
            params = _set_param(params, spec, new)
            changes.append(_gain_change(stage, spec, old, new))
            touched = True
            if new >= attempt - 1e-9:
                remaining = 1.0
                break
            remaining = remaining / (new / old)
        if touched:
            _apply_stage_module(new_modules, index_by_id, stage, params)
        if remaining <= 1.0 + 1e-9:
            break

    return {**patch, "modules": new_modules}, changes


def fix_clipping(patch, peak, target_peak=TARGET_PEAK):
    """Scale down the nearest gain stage so `peak` lands at target_peak."""
    factor = max(MIN_SCALE_FACTOR, target_peak / peak)
    new_patch, changes = scale_gain(patch, factor)
    return new_patch, [{**c, "fix": "clipping"} for c in changes]


def fix_near_silent(patch, rms, target_rms):
    """Scale up the nearest gain stage(s) so `rms` lands at target_rms."""
    factor = min(MAX_SCALE_FACTOR, target_rms / max(rms, 1e-9))
    new_patch, changes = scale_gain(patch, factor)
    return new_patch, [{**c, "fix": "near_silent"} for c in changes]


def _new_vcf(vcf_id):
    return {
        "id": vcf_id,
        "plugin": "Fundamental",
        "model": "VCF",
        "version": "2.0.0",
        "params": [
            {"id": 0, "value": DC_BLOCK_CUTOFF_PARAM},
            {"id": 2, "value": 0.0},
            {"id": 4, "value": 0.0},
        ],
        "pos": [0, 3],
    }


def insert_dc_blocker(patch):
    """Insert one Fundamental VCF (as a high-pass DC blocker) per unique
    source feeding the audio interface. Returns (new_patch, changes).

    Only the cable(s) from that source into interface inputs 0/1 are
    removed and replaced with source -> VCF input 3, VCF output 1 (HP) ->
    each interface input the source fed. A source teed to both interface
    inputs (mono) gets one VCF fanned to both; cables the source has to
    other modules (Scope etc.) are untouched.
    """
    feeds = find_audio_feeds(patch)
    if not feeds:
        return patch, []

    audio_ids = {
        m["id"]
        for m in patch.get("modules", [])
        if m.get("plugin") == "Core" and m.get("model") in AUDIO_INTERFACE_MODELS
    }
    original_cables = patch.get("cables", [])
    feed_cables = [
        c
        for c in original_cables
        if c.get("inputModuleId") in audio_ids and c.get("inputId") in (0, 1)
    ]

    sources = {}
    for cable in feed_cables:
        key = (cable["outputModuleId"], cable["outputId"])
        sources.setdefault(key, []).append(cable)

    module_ids = [m["id"] for m in patch.get("modules", [])]
    next_module_id = max(module_ids, default=0) + 1
    cable_ids = [c.get("id", 0) for c in original_cables]
    next_cable_id = max(cable_ids, default=0) + 1

    remove_ids = {c["id"] for cables in sources.values() for c in cables}
    new_modules = list(patch.get("modules", []))
    new_cables = [c for c in original_cables if c.get("id") not in remove_ids]
    changes = []

    for source, cables in sources.items():
        vcf_id = next_module_id
        next_module_id += 1
        new_modules.append(_new_vcf(vcf_id))

        new_cables.append(
            {
                "id": next_cable_id,
                "outputModuleId": source[0],
                "outputId": source[1],
                "inputModuleId": vcf_id,
                "inputId": 3,
            }
        )
        next_cable_id += 1

        rewired_inputs = []
        for cable in cables:
            new_cables.append(
                {
                    "id": next_cable_id,
                    "outputModuleId": vcf_id,
                    "outputId": 1,
                    "inputModuleId": cable["inputModuleId"],
                    "inputId": cable["inputId"],
                }
            )
            next_cable_id += 1
            rewired_inputs.append(cable["inputId"])

        changes.append(
            {
                "fix": "dc_blocker",
                "vcf_id": vcf_id,
                "rewired_inputs": sorted(rewired_inputs),
            }
        )

    return {**patch, "modules": new_modules, "cables": new_cables}, changes


def apply_repairs(patch, metrics, bands=None):
    """Dispatch repairs from an analyzer verdict. Returns (new_patch, changes).

    dc_offset is fixed first (it changes topology), then clipping (using
    metrics["peak"]); near_silent is only applied when clipping is absent,
    since a patch can't be near-silent AND clipping at once in practice but
    clipping takes priority if both flags somehow appear. No relevant
    flags -> (patch, []) unchanged.
    """
    flags = metrics.get("verdict", {}).get("flags", [])
    working, changes = patch, []

    if "dc_offset" in flags:
        working, dc_changes = insert_dc_blocker(working)
        changes = changes + dc_changes

    if "clipping" in flags:
        working, gain_changes = fix_clipping(working, metrics["peak"])
        changes = changes + gain_changes
    elif "near_silent" in flags:
        p50 = (bands or {}).get("metrics", {}).get("rms", {}).get("p50")
        target_rms = p50 if isinstance(p50, (int, float)) else FALLBACK_TARGET_RMS
        working, gain_changes = fix_near_silent(working, metrics["rms"], target_rms)
        changes = changes + gain_changes

    return working, changes
