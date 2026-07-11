import copy

from audition import is_good, merge_manifest, summary_line


def result_ok(character="drone", flags=None):
    return {
        "structural": "PASS",
        "render": "OK",
        "metrics": {
            "rms": 0.3,
            "peak": 0.8,
            "verdict": {
                "makes_sound": True,
                "character": character,
                "flags": flags or [],
            },
        },
    }


def test_merge_manifest_attaches_audio():
    entries = [{"name": "01-x", "archetype": "drone"}, {"name": "02-y", "archetype": "seq"}]
    snapshot = copy.deepcopy(entries)
    merged = merge_manifest(entries, {"01-x": result_ok()})

    assert merged[0]["audio"]["makes_sound"] is True
    assert merged[0]["audio"]["character"] == "drone"
    assert merged[0]["archetype"] == "drone"
    assert "audio" not in merged[1]
    assert entries == snapshot  # no mutation


def test_merge_manifest_render_fail():
    entries = [{"name": "01-x", "archetype": "drone"}]
    merged = merge_manifest(
        entries,
        {"01-x": {"structural": "FAIL", "render": "FAIL", "render_error": "boom"}},
    )
    assert merged[0]["audio"] == {"structural": "FAIL", "render": "FAIL"}


def test_merge_manifest_includes_score_when_present():
    entries = [{"name": "01-x", "archetype": "drone"}]
    result = {**result_ok(), "score": 55}
    merged = merge_manifest(entries, {"01-x": result})
    assert merged[0]["audio"]["score"] == 55


def test_merge_manifest_omits_score_when_absent():
    entries = [{"name": "01-x", "archetype": "drone"}]
    merged = merge_manifest(entries, {"01-x": result_ok()})
    assert "score" not in merged[0]["audio"]


def test_is_good():
    assert is_good(result_ok()) is True
    assert is_good(result_ok(flags=["clipping"])) is False
    bad = result_ok()
    bad["structural"] = "FAIL"
    assert is_good(bad) is False


def test_is_good_without_metrics():
    analyze_failed = {"structural": "PASS", "render": "OK", "analyze_error": "boom"}
    assert is_good(analyze_failed) is False
    render_failed = {"structural": "PASS", "render": "FAIL", "render_error": "boom"}
    assert is_good(render_failed) is False


def test_summary_line_without_score_shows_dash():
    line = summary_line("01-x", result_ok())
    assert "score=-" in line


def test_summary_line_with_score_shows_value():
    line = summary_line("01-x", {**result_ok(), "score": 72})
    assert "score=72" in line
