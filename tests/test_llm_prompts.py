import json

import pytest

from llm_prompts import (
    HARD_RULES,
    build_context,
    build_critique,
    build_initial_prompt,
    build_revision_prompt,
    check_whitelist,
    extract_patch_json,
    param_stats_for_prompt,
)


def sample_patch():
    return {
        "id": 1,
        "modules": [
            {"id": 1, "plugin": "Fundamental", "model": "VCO", "pos": [0, 0]},
            {"id": 9, "plugin": "Core", "model": "AudioInterface", "pos": [16, 0]},
        ],
    }


# --- extract_patch_json ------------------------------------------------


def test_extract_single_fenced_json():
    text = 'Here is the patch:\n```json\n{"a": 1, "b": 2}\n```\nEnjoy.'
    assert extract_patch_json(text) == {"a": 1, "b": 2}


def test_extract_multiple_fences_last_wins():
    text = (
        "First draft:\n```json\n{\"a\": 1}\n```\n"
        "Actually, use this one:\n```json\n{\"a\": 2, \"b\": 3}\n```"
    )
    assert extract_patch_json(text) == {"a": 2, "b": 3}


def test_extract_bare_fence_without_json_tag():
    text = "```\n{\"a\": 1}\n```"
    assert extract_patch_json(text) == {"a": 1}


def test_extract_bare_unfenced_json_object():
    text = 'Sure, here it is: {"a": 1, "b": [1, 2, 3]} enjoy!'
    assert extract_patch_json(text) == {"a": 1, "b": [1, 2, 3]}


def test_extract_prose_wrapped_json_with_nesting():
    text = (
        "Some notes about the design.\n\n"
        '{"modules": [{"id": 1, "params": {"nested": true}}], "cables": []}'
        "\n\nHope this helps!"
    )
    result = extract_patch_json(text)
    assert result["modules"][0]["id"] == 1
    assert result["cables"] == []


def test_extract_garbage_raises_value_error_with_critique_message():
    with pytest.raises(ValueError) as exc_info:
        extract_patch_json("no json here at all, just prose")
    assert "your reply contained no parseable JSON object" in str(exc_info.value)


def test_extract_non_dict_top_level_raises():
    with pytest.raises(ValueError):
        extract_patch_json("```json\n[1, 2, 3]\n```")


# --- build_context -------------------------------------------------------


def test_build_context_skips_missing_includes_present(tmp_path):
    (tmp_path / "INDEX.md").write_text("index contents")
    (tmp_path / "archetypes.md").write_text("archetype contents")
    # module-quick-ref.md, connection-grammar.md, etc. deliberately absent

    context = build_context(reference_dir=tmp_path)

    assert "index contents" in context
    assert "archetype contents" in context
    assert "module-quick-ref.md" not in context
    assert "PATCH JSON FORMAT" in context  # format spec always present


def test_build_context_empty_dir_still_has_format_spec(tmp_path):
    context = build_context(reference_dir=tmp_path)
    assert "PATCH JSON FORMAT" in context


# --- param_stats_for_prompt -----------------------------------------------


def test_param_stats_for_prompt_formats_and_skips_misses():
    port_maps = {
        "Fundamental:VCF": {
            "params": {0: "Cutoff frequency", 2: "Resonance"},
        },
        "Bogaudio:LVCF": {
            "params": {0: "Cutoff"},
        },
    }
    distributions = {
        "Fundamental:VCF:Cutoff frequency": {"min": 0.0, "max": 1.0, "median": 0.61},
        # no distribution for Resonance -> skipped
        "Bogaudio:LVCF:Cutoff": {"min": 0.0, "max": 1.0, "median": 0.5},
    }

    stats = param_stats_for_prompt(port_maps, distributions)

    assert "Fundamental:VCF param 0 (Cutoff frequency): corpus median 0.61, range [0.00, 1.00]" in stats
    assert "Resonance" not in stats
    assert "Bogaudio" not in stats


# --- build_initial_prompt / build_revision_prompt -------------------------


def band_summary():
    return {
        "rms": {"p10": 0.01, "p50": 0.05, "p90": 0.2},
        "spectral_centroid_hz": {"p10": 200.0, "p50": 800.0, "p90": 2000.0},
    }


def test_initial_prompt_contains_required_pieces():
    stats = "Fundamental:VCO param 2 (Frequency): corpus median 0.50, range [0.00, 1.00]"
    prompt = build_initial_prompt("drone", band_summary(), stats, "CONTEXT-BLOCK")

    assert prompt.count(HARD_RULES) == 2
    assert "drone" in prompt
    assert "0.050" in prompt  # rms p50 rendered
    assert stats in prompt
    assert "CONTEXT-BLOCK" in prompt


def test_revision_prompt_embeds_prev_patch_and_critique():
    prev_patch = sample_patch()
    critique = "STRUCTURAL:\n- no gate on ADSR (id=4)"

    prompt = build_revision_prompt(
        "sequenced-melody", band_summary(), "some stats", "CONTEXT", prev_patch, critique
    )

    assert json.dumps(prev_patch, indent=2) in prompt
    assert critique in prompt
    assert prompt.count(HARD_RULES) == 2
    assert "sequenced-melody" in prompt


# --- build_critique --------------------------------------------------------


def test_critique_lists_structural_errors_verbatim():
    validation = {"pass": False, "errors": ["ADSR (id=4) has no gate input"], "warnings": []}
    critique = build_critique(validation, None, None, None, {})
    assert "STRUCTURAL" in critique
    assert "ADSR (id=4) has no gate input" in critique


def test_critique_includes_render_error_section():
    critique = build_critique(None, "short WAV: got 200 need>=1000", None, None, {})
    assert "RENDER" in critique
    assert "short WAV: got 200 need>=1000" in critique


def test_critique_low_scoring_metric_includes_corpus_median():
    score_result = {
        "fitness": 0.3,
        "per_metric": {
            "rms": {"score": 0.1, "value": 0.0015, "percentile": 0.0},
            "spectral_centroid_hz": {"score": 0.9, "value": 900.0, "percentile": 0.7},
        },
    }
    bands = {"rms": {"p10": 0.01, "p50": 0.075, "p90": 0.2}}

    critique = build_critique(None, None, None, score_result, bands)

    assert "SCORE" in critique
    assert "median 0.075" in critique
    assert "percentile 0.00" in critique
    assert "rms=0.0015" in critique
    # high-scoring metric must not generate a line
    assert "spectral_centroid_hz=" not in critique


def test_critique_clean_case_ends_with_instruction():
    validation = {"pass": True, "errors": [], "warnings": []}
    critique = build_critique(validation, None, None, None, {})
    assert critique == "Return a full corrected patch JSON that addresses every point above."


# --- check_whitelist --------------------------------------------------------


def test_check_whitelist_flags_non_fundamental_plugin():
    patch = {
        "modules": [
            {"id": 7, "plugin": "Bogaudio", "model": "LVCF"},
        ]
    }
    violations = check_whitelist(patch)
    assert len(violations) == 1
    assert "module 7 (Bogaudio:LVCF)" in violations[0]


def test_check_whitelist_flags_non_interface_core_model():
    patch = {
        "modules": [
            {"id": 3, "plugin": "Core", "model": "MIDI-CV"},
        ]
    }
    violations = check_whitelist(patch)
    assert len(violations) == 1
    assert "module 3 (Core:MIDI-CV)" in violations[0]


def test_check_whitelist_passes_fundamental_and_audio_interface():
    patch = sample_patch()
    assert check_whitelist(patch) == []


def test_check_whitelist_flags_non_dict_module_entry():
    patch = {"modules": [{"id": 1, "plugin": "Fundamental", "model": "VCO"}, "oops"]}
    assert check_whitelist(patch) == ["modules[1] is not an object"]


def test_check_whitelist_flags_non_list_modules():
    assert check_whitelist({"modules": {"id": 1}}) == ['patch has no "modules" list']
    assert check_whitelist({}) == ['patch has no "modules" list']


def test_extract_truncated_reply_raises_instead_of_inner_fragment():
    truncated = (
        '{"version": "1.1.6", "modules": [{"id": 1, "plugin": "Fundamental",'
        ' "model": "VCO", "params": [{"id": 2, "value": 0.5}]'
    )
    with pytest.raises(ValueError, match="no parseable JSON object"):
        extract_patch_json("Here is the patch:\n" + truncated)


def test_extract_skips_prose_braces_before_real_object():
    text = 'I tuned {carefully} this time: {"version": "1.1.6", "modules": []}'
    assert extract_patch_json(text) == {"version": "1.1.6", "modules": []}


def test_extract_uppercase_json_fence_tag():
    text = '```JSON\n{"version": "1.1.6", "modules": []}\n```'
    assert extract_patch_json(text) == {"version": "1.1.6", "modules": []}
