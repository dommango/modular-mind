from corpus_audition import is_renderable_format, patch_plugins


def v2_patch():
    return {
        "version": "2.5.2",
        "modules": [
            {"id": 1, "plugin": "Fundamental", "model": "VCO"},
            {"id": 2, "plugin": "Core", "model": "AudioInterface"},
        ],
        "cables": [
            {"id": 1, "outputModuleId": 1, "outputId": 0, "inputModuleId": 2, "inputId": 0}
        ],
    }


def v06_patch():
    # Rack v0.6: modules addressed by index (no id), connections under `wires`
    return {
        "version": "0.6.2b",
        "modules": [
            {"plugin": "Core", "model": "AudioInterface"},
            {"plugin": "Fundamental", "model": "VCO"},
        ],
        "wires": [{"outputModuleId": 1, "inputModuleId": 0}],
    }


def test_is_renderable_format_v2():
    assert is_renderable_format(v2_patch())


def test_is_renderable_format_rejects_old():
    assert not is_renderable_format(v06_patch())


def test_is_renderable_format_rejects_missing_cables():
    p = v2_patch()
    del p["cables"]
    assert not is_renderable_format(p)


def test_is_renderable_format_rejects_partial_ids():
    p = v2_patch()
    p["modules"] = p["modules"] + [{"plugin": "Bogaudio", "model": "LFO"}]
    assert not is_renderable_format(p)


def test_patch_plugins_excludes_core():
    assert patch_plugins(v2_patch()) == ["Fundamental"]
