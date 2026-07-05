import copy

from audition import is_good, merge_manifest


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


def test_is_good():
    assert is_good(result_ok()) is True
    assert is_good(result_ok(flags=["clipping"])) is False
    bad = result_ok()
    bad["structural"] = "FAIL"
    assert is_good(bad) is False
