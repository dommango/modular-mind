import copy

import pytest

import repair_patch as rp
from repair_patch import (
    DC_BLOCK_CUTOFF_PARAM,
    FALLBACK_TARGET_RMS,
    GAIN_PARAM_SPECS,
    MAX_SCALE_FACTOR,
    MIN_SCALE_FACTOR,
    TARGET_PEAK,
    apply_repairs,
    fix_clipping,
    fix_near_silent,
    insert_dc_blocker,
    scale_gain,
    upstream_gain_stages,
)


def vco(module_id=1, pos=(0, 0)):
    return {"id": module_id, "plugin": "Fundamental", "model": "VCO", "params": [], "pos": list(pos)}


def vcmixer(module_id=5, params=None, pos=(8, 0)):
    return {
        "id": module_id,
        "plugin": "Fundamental",
        "model": "VCMixer",
        "params": params if params is not None else [],
        "pos": list(pos),
    }


def vca(module_id=3, params=None, pos=(4, 0)):
    return {
        "id": module_id,
        "plugin": "Fundamental",
        "model": "VCA-1",
        "params": params if params is not None else [],
        "pos": list(pos),
    }


def audio_interface(module_id=9, pos=(16, 0)):
    return {"id": module_id, "plugin": "Core", "model": "AudioInterface", "params": [], "pos": list(pos)}


def mono_mixer_patch(mix_params=None):
    """VCO -> VCMixer -> AudioInterface, mono teed (same source both inputs)."""
    return {
        "version": "1.1.6",
        "modules": [vco(1), vcmixer(5, params=mix_params), audio_interface(9)],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 5, "inputId": 1},
            {"id": 2, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 0},
            {"id": 3, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 1},
        ],
    }


def mixer_and_vca_patch():
    """VCO -> VCA-1 -> VCMixer -> AudioInterface (VCMixer at distance 0, VCA-1 at distance 1)."""
    return {
        "version": "1.1.6",
        "modules": [vco(1), vca(3), vcmixer(5), audio_interface(9)],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 3, "inputId": 0},
            {"id": 2, "outputModuleId": 3, "outputId": 0, "inputModuleId": 5, "inputId": 1},
            {"id": 3, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 0},
            {"id": 4, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 1},
        ],
    }


def no_gain_patch():
    """VCO straight into the interface — no known gain-control module upstream."""
    return {
        "version": "1.1.6",
        "modules": [vco(1), audio_interface(9)],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 9, "inputId": 0},
            {"id": 2, "outputModuleId": 1, "outputId": 0, "inputModuleId": 9, "inputId": 1},
        ],
    }


def dc_source_patch(extra_modules=None, extra_cables=None):
    """Single source mono-teed into both interface inputs, for DC-blocker tests."""
    modules = [vco(1), audio_interface(9)] + (extra_modules or [])
    cables = [
        {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 9, "inputId": 0},
        {"id": 2, "outputModuleId": 1, "outputId": 0, "inputModuleId": 9, "inputId": 1},
    ] + (extra_cables or [])
    return {"version": "1.1.6", "modules": modules, "cables": cables}


# ---- upstream_gain_stages ----


def test_upstream_gain_stages_orders_nearest_first():
    stages = upstream_gain_stages(mixer_and_vca_patch())
    assert [(s["module_id"], s["model"]) for s in stages] == [(5, "VCMixer"), (3, "VCA-1")]


def test_upstream_gain_stages_skips_non_gain_modules():
    assert upstream_gain_stages(no_gain_patch()) == []


def test_upstream_gain_stages_no_mutation():
    patch = mixer_and_vca_patch()
    snapshot = copy.deepcopy(patch)
    upstream_gain_stages(patch)
    assert patch == snapshot


# ---- scale_gain ----


def test_scale_gain_down_touches_only_nearest_primary():
    patch = mixer_and_vca_patch()
    new_patch, changes = scale_gain(patch, 0.5)

    assert changes == [
        {
            "fix": "gain", "module_id": 5, "model": "VCMixer",
            "param_id": 0, "param_name": "Mix level", "old": 1.0, "new": 0.5,
        }
    ]
    mixer = next(m for m in new_patch["modules"] if m["id"] == 5)
    assert mixer["params"] == [{"id": 0, "value": 0.5}]
    upstream_vca = next(m for m in new_patch["modules"] if m["id"] == 3)
    assert upstream_vca["params"] == []  # farther stage untouched


def test_scale_gain_down_appends_sparse_param():
    patch = mono_mixer_patch(mix_params=[])
    new_patch, changes = scale_gain(patch, 0.5)

    mixer = next(m for m in new_patch["modules"] if m["id"] == 5)
    assert mixer["params"] == [{"id": 0, "value": 0.5}]
    assert changes[0]["old"] == 1.0  # spec default, since the param was absent


def test_scale_gain_no_stages_unchanged():
    patch = no_gain_patch()
    new_patch, changes = scale_gain(patch, 0.5)
    assert changes == []
    assert new_patch == patch


def test_scale_gain_up_caps_then_cascades_within_stage():
    patch = mixer_and_vca_patch()
    _, changes = scale_gain(patch, 3)

    by_param = {(c["module_id"], c["param_id"]): c for c in changes}
    assert by_param[(5, 0)]["new"] == pytest.approx(2.0)  # Mix level capped
    assert by_param[(5, 1)]["new"] == pytest.approx(1.41)  # Ch1 level capped
    assert by_param[(5, 2)]["new"] == pytest.approx(1.5 / 1.41)  # Ch2 absorbs the residual
    assert (5, 3) not in by_param
    assert (5, 4) not in by_param
    assert not any(c["model"] == "VCA-1" for c in changes)  # next stage never reached


def test_scale_gain_up_cascades_into_next_stage():
    patch = mixer_and_vca_patch()
    vca_module = next(m for m in patch["modules"] if m["id"] == 3)
    vca_module["params"] = [{"id": 0, "value": 0.5}]  # headroom for the cascade
    _, changes = scale_gain(patch, 50)

    by_param = {(c["module_id"], c["param_id"]): c for c in changes}
    assert by_param[(5, 0)]["new"] == pytest.approx(2.0)
    for ch_id in (1, 2, 3, 4):
        assert by_param[(5, ch_id)]["new"] == pytest.approx(1.41)
    assert by_param[(3, 0)]["model"] == "VCA-1"
    assert by_param[(3, 0)]["old"] == pytest.approx(0.5)
    assert by_param[(3, 0)]["new"] == pytest.approx(1.0)  # VCA-1 Level capped


def test_scale_gain_up_skips_already_capped_stage():
    # the factory VCA is sparse -> default 1.0 == its max: it cannot add
    # gain, so it must produce no (no-op) change record at all
    patch = mixer_and_vca_patch()
    _, changes = scale_gain(patch, 50)

    assert not any(c["module_id"] == 3 for c in changes)
    for c in changes:
        assert c["new"] > c["old"]


def test_scale_gain_down_skips_zeroed_primary():
    # a mix level already at 0 is not what's passing signal — the scale-down
    # must move on to the next upstream stage instead of no-opping
    patch = {
        "version": "1.1.6",
        "modules": [
            vco(1),
            vca(3),
            vcmixer(5, params=[{"id": 0, "value": 0.0}]),
            audio_interface(9),
        ],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 3, "inputId": 0},
            {"id": 2, "outputModuleId": 3, "outputId": 0, "inputModuleId": 5, "inputId": 1},
            {"id": 3, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 0},
            {"id": 4, "outputModuleId": 5, "outputId": 0, "inputModuleId": 9, "inputId": 1},
        ],
    }
    _, changes = scale_gain(patch, 0.5)

    assert len(changes) == 1
    assert changes[0]["module_id"] == 3
    assert changes[0]["model"] == "VCA-1"
    assert changes[0]["new"] == pytest.approx(0.5)


def test_scale_gain_up_skips_zeroed_primary():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 0.0}])
    _, changes = scale_gain(patch, 2.0)

    by_param = {(c["module_id"], c["param_id"]): c for c in changes}
    assert (5, 0) not in by_param  # 0 * factor stays 0 — must not be touched
    assert by_param  # channel trims picked up the factor instead
    for c in changes:
        assert c["new"] > c["old"]


def test_apply_repairs_near_silent_none_p50_uses_fallback():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    metrics = {"rms": 0.001, "verdict": {"flags": ["near_silent"]}}
    bands = {"metrics": {"rms": {"values": [], "p50": None}}}

    new_patch, changes = apply_repairs(patch, metrics, bands)

    assert changes  # fell back to FALLBACK_TARGET_RMS instead of crashing
    assert all(c["new"] > c["old"] for c in changes)


def test_scale_gain_no_mutation():
    patch = mixer_and_vca_patch()
    snapshot = copy.deepcopy(patch)
    scale_gain(patch, 3)
    assert patch == snapshot


# ---- fix_clipping / fix_near_silent ----


def test_fix_clipping_scales_toward_target_peak():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    _, changes = fix_clipping(patch, peak=1.0, target_peak=0.7)
    assert changes[0]["fix"] == "clipping"
    assert changes[0]["new"] == pytest.approx(0.7)


def test_fix_clipping_floors_at_min_scale_factor(monkeypatch):
    captured = {}

    def fake_scale_gain(patch, factor):
        captured["factor"] = factor
        return patch, []

    monkeypatch.setattr(rp, "scale_gain", fake_scale_gain)
    fix_clipping(mono_mixer_patch(), peak=100.0, target_peak=TARGET_PEAK)
    assert captured["factor"] == MIN_SCALE_FACTOR


def test_fix_near_silent_scales_toward_target_rms():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    _, changes = fix_near_silent(patch, rms=0.01, target_rms=0.02)
    assert changes[0]["fix"] == "near_silent"
    assert changes[0]["new"] == pytest.approx(2.0)


def test_fix_near_silent_caps_at_max_scale_factor(monkeypatch):
    captured = {}

    def fake_scale_gain(patch, factor):
        captured["factor"] = factor
        return patch, []

    monkeypatch.setattr(rp, "scale_gain", fake_scale_gain)
    fix_near_silent(mono_mixer_patch(), rms=0.0001, target_rms=1.0)
    assert captured["factor"] == MAX_SCALE_FACTOR


def test_fix_clipping_no_mutation():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    snapshot = copy.deepcopy(patch)
    fix_clipping(patch, peak=1.0, target_peak=0.7)
    assert patch == snapshot


# ---- insert_dc_blocker ----


def test_insert_dc_blocker_mono_tee_one_vcf():
    patch = dc_source_patch()
    new_patch, changes = insert_dc_blocker(patch)

    assert changes == [{"fix": "dc_blocker", "vcf_id": 10, "rewired_inputs": [0, 1]}]
    vcf = next(m for m in new_patch["modules"] if m["id"] == 10)
    assert vcf["plugin"] == "Fundamental"
    assert vcf["model"] == "VCF"
    assert vcf["params"][0]["value"] == pytest.approx(DC_BLOCK_CUTOFF_PARAM)
    assert vcf["params"][0]["value"] == pytest.approx(0.161)


def test_insert_dc_blocker_rewires_interface_cables_only():
    scope = {"id": 20, "plugin": "Fundamental", "model": "Scope", "params": [], "pos": [4, 4]}
    scope_cable = {"id": 3, "outputModuleId": 1, "outputId": 0, "inputModuleId": 20, "inputId": 0}
    patch = dc_source_patch(extra_modules=[scope], extra_cables=[scope_cable])

    new_patch, changes = insert_dc_blocker(patch)
    cable_ids = {c["id"] for c in new_patch["cables"]}

    assert 1 not in cable_ids  # original interface feed (input 0) removed
    assert 2 not in cable_ids  # original interface feed (input 1) removed
    assert scope_cable in new_patch["cables"]  # non-interface cable from the source untouched

    vcf_id = changes[0]["vcf_id"]
    into_vcf = [c for c in new_patch["cables"] if c["inputModuleId"] == vcf_id]
    assert into_vcf == [
        {"id": 4, "outputModuleId": 1, "outputId": 0, "inputModuleId": vcf_id, "inputId": 3}
    ]
    out_of_vcf = {c["inputId"]: c for c in new_patch["cables"] if c["outputModuleId"] == vcf_id}
    assert out_of_vcf[0] == {"id": 5, "outputModuleId": vcf_id, "outputId": 1, "inputModuleId": 9, "inputId": 0}
    assert out_of_vcf[1] == {"id": 6, "outputModuleId": vcf_id, "outputId": 1, "inputModuleId": 9, "inputId": 1}


def test_insert_dc_blocker_two_sources_two_vcfs():
    vco2 = {"id": 2, "plugin": "Fundamental", "model": "VCO", "params": [], "pos": [4, 0]}
    patch = dc_source_patch(extra_modules=[vco2])
    patch["cables"][1] = {"id": 2, "outputModuleId": 2, "outputId": 0, "inputModuleId": 9, "inputId": 1}

    new_patch, changes = insert_dc_blocker(patch)

    assert len(changes) == 2
    vcf_ids = {c["vcf_id"] for c in changes}
    assert vcf_ids == {10, 11}
    rewired_flat = sorted(i for c in changes for i in c["rewired_inputs"])
    assert rewired_flat == [0, 1]
    vcf_models = [(m["plugin"], m["model"]) for m in new_patch["modules"] if m["id"] in vcf_ids]
    assert vcf_models == [("Fundamental", "VCF"), ("Fundamental", "VCF")]


def test_insert_dc_blocker_no_feeds_unchanged():
    patch = {"version": "1.1.6", "modules": [vco(1), audio_interface(9)], "cables": []}
    new_patch, changes = insert_dc_blocker(patch)
    assert changes == []
    assert new_patch == patch


def test_insert_dc_blocker_no_mutation():
    scope = {"id": 20, "plugin": "Fundamental", "model": "Scope", "params": [], "pos": [4, 4]}
    scope_cable = {"id": 3, "outputModuleId": 1, "outputId": 0, "inputModuleId": 20, "inputId": 0}
    patch = dc_source_patch(extra_modules=[scope], extra_cables=[scope_cable])
    snapshot = copy.deepcopy(patch)
    insert_dc_blocker(patch)
    assert patch == snapshot


# ---- apply_repairs ----


def test_apply_repairs_dc_and_clipping_combined():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    metrics = {"peak": 1.0, "rms": 0.3, "verdict": {"flags": ["dc_offset", "clipping"]}}

    new_patch, changes = apply_repairs(patch, metrics)

    assert changes[0]["fix"] == "dc_blocker"
    assert any(c["fix"] == "clipping" for c in changes[1:])
    assert any(m["model"] == "VCF" for m in new_patch["modules"])
    clip_change = next(c for c in changes if c["fix"] == "clipping")
    assert clip_change["new"] == pytest.approx(0.7)


def test_apply_repairs_near_silent_uses_bands_p50():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    metrics = {"peak": 0.3, "rms": 0.01, "verdict": {"flags": ["near_silent"]}}
    bands = {"metrics": {"rms": {"p50": 0.015}}}

    _, changes = apply_repairs(patch, metrics, bands=bands)

    assert changes[0]["fix"] == "near_silent"
    assert changes[0]["new"] == pytest.approx(1.0 * (0.015 / 0.01))


def test_apply_repairs_near_silent_fallback_target():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    metrics = {"peak": 0.3, "rms": 0.05, "verdict": {"flags": ["near_silent"]}}

    _, changes = apply_repairs(patch, metrics)

    factor = FALLBACK_TARGET_RMS / 0.05
    spec_max = GAIN_PARAM_SPECS[("Fundamental", "VCMixer")][0]["max"]
    expected = max(0.0, min(1.0 * factor, spec_max))
    assert changes[0]["new"] == pytest.approx(expected)


def test_apply_repairs_clean_flags_no_changes():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    metrics = {"peak": 0.3, "rms": 0.3, "verdict": {"flags": []}}

    new_patch, changes = apply_repairs(patch, metrics)

    assert changes == []
    assert new_patch == patch


def test_apply_repairs_no_mutation():
    patch = mono_mixer_patch(mix_params=[{"id": 0, "value": 1.0}])
    snapshot = copy.deepcopy(patch)
    metrics = {"peak": 1.0, "rms": 0.3, "verdict": {"flags": ["dc_offset", "clipping"]}}
    apply_repairs(patch, metrics)
    assert patch == snapshot
