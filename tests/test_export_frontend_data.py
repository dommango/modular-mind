import json
import math
import struct
import wave
from pathlib import Path

import pytest

import export_frontend_data as ex


def write_wav(path, frames=1000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        for i in range(frames):
            w.writeframes(struct.pack("<h", int(20000 * math.sin(i / 10))))


def make_data_tree(root):
    (root / "audio").mkdir(parents=True)
    (root / "output").mkdir()
    (root / "generated/batch3").mkdir(parents=True)
    (root / "generated/llm").mkdir()
    (root / "whitelist").mkdir()
    (root / "metadata").mkdir()
    (root / "raw").mkdir()
    (root / "reference").mkdir()
    verdict_ok = {"makes_sound": True, "character": "rhythmic", "flags": []}
    verdict_flag = {"makes_sound": True, "character": "drone", "flags": ["clipping"]}
    analysis = {
        "batch3-01-omri-seq": {"duration": 10.0, "sample_rate": 44100, "rms": 0.1, "verdict": verdict_ok},
        "batch3-02-drone": {"duration": 10.0, "sample_rate": 44100, "rms": 0.2, "verdict": verdict_flag},
        "batch3-02-drone-r1": {"duration": 10.0, "sample_rate": 44100, "rms": 0.15, "verdict": verdict_ok},
    }
    for slug in analysis:
        write_wav(root / "audio" / f"{slug}.wav")
    (root / "output/audio_analysis.json").write_text(json.dumps(analysis))
    (root / "output/audio_scores.json").write_text(json.dumps(
        {"batch3-01-omri-seq": {"fitness": 80}, "batch3-02-drone-r1": {"fitness": 60}}))
    (root / "generated/batch3/manifest.json").write_text(json.dumps([
        {"name": "01-omri-seq", "archetype": "omri-seq"},
        {"name": "02-drone", "archetype": "drone"},
    ]))
    (root / "generated/llm/manifest.json").write_text(json.dumps([]))
    (root / "output/module_profiles.json").write_text(json.dumps({
        "Fundamental:VCO": {"plugin": "Fundamental", "model": "VCO", "role": "Source",
                            "tags": ["osc"], "description": "d", "instance_count": 5,
                            "manual_url": None, "params": [], "inputs": [], "outputs": []}}))
    # stage-stats sources (consumed by build_stage_stats / build_insights)
    (root / "whitelist/free_plugins.json").write_text(json.dumps({"Fundamental:VCO": 1}))
    (root / "metadata/all_patches.json").write_text(json.dumps([1]))
    (root / "raw/manifest.json").write_text(json.dumps({"1": {}}))
    (root / "output/filtered_patches.json").write_text(json.dumps([1]))
    (root / "output/module_frequency.csv").write_text(
        "plugin,model,patch_count,instance_count,pct_patches\n"
        "Fundamental,VCO,10,15,50.0\n")
    (root / "output/port_registry.json").write_text(json.dumps({}))
    (root / "output/decoded_patches.json").write_text(json.dumps([]))
    (root / "output/analysis_summary.json").write_text(json.dumps({
        "patch_complexity": {}, "top_connection_patterns": [],
        "most_tweaked_params": [], "module_roles": {}, "author_signatures": {}}))
    (root / "output/connection_patterns.json").write_text(json.dumps({
        "port_pairs": [], "common_chains": []}))
    return root


def test_build_tracks_lineage_and_featured(tmp_path):
    root = make_data_tree(tmp_path)
    analysis = json.loads((root / "output/audio_analysis.json").read_text())
    scores = json.loads((root / "output/audio_scores.json").read_text())
    batch = json.loads((root / "generated/batch3/manifest.json").read_text())
    tracks = ex.build_tracks(analysis, scores, batch, [], root / "audio")
    by_slug = {t["slug"]: t for t in tracks}
    assert by_slug["batch3-02-drone-r1"]["parent"] == "batch3-02-drone"
    assert by_slug["batch3-02-drone"]["repairs"] == ["batch3-02-drone-r1"]
    assert by_slug["batch3-02-drone-r1"]["source"] == "repair"
    assert by_slug["batch3-01-omri-seq"]["archetype"] == "omri-seq"
    # flagged track is never featured; both clean scored tracks are
    assert not by_slug["batch3-02-drone"]["featured"]
    assert by_slug["batch3-01-omri-seq"]["featured"]


def test_missing_source_fails_without_partial_output(tmp_path, monkeypatch):
    root = make_data_tree(tmp_path)
    (root / "output/audio_scores.json").unlink()
    out = tmp_path / "web_public"
    monkeypatch.setattr("sys.argv", ["x", "--data-dir", str(root), "--out", str(out)])
    with pytest.raises(SystemExit) as e:
        ex.main()
    assert e.value.code == 1
    assert not (out / "data").exists()


def test_main_end_to_end_writes_full_output_set(tmp_path, monkeypatch):
    root = make_data_tree(tmp_path)
    out = tmp_path / "web_public"
    # don't shell out to ffmpeg in the end-to-end path; behavior under test is the JSON set
    monkeypatch.setattr(ex, "transcode", lambda wav, mp3: False)
    monkeypatch.setattr("sys.argv", ["x", "--data-dir", str(root), "--out", str(out)])
    ex.main()
    tracks_doc = json.loads((out / "data/tracks.json").read_text())
    assert tracks_doc["schema_version"] == 1
    slugs = {t["slug"] for t in tracks_doc["tracks"]}
    assert slugs == {"batch3-01-omri-seq", "batch3-02-drone", "batch3-02-drone-r1"}
    stages_doc = json.loads((out / "data/stages.json").read_text())
    assert {s["slug"] for s in stages_doc["stages"]} >= {"00", "audition"}
    tracks_rendered = next(s["stat"]["value"] for s in stages_doc["stages"]
                           if s["slug"] == "audition")
    assert tracks_rendered == 3
    modules_doc = json.loads((out / "data/modules.json").read_text())
    assert modules_doc["modules"][0]["key"] == "Fundamental:VCO"
    insights_doc = json.loads((out / "data/insights.json").read_text())
    assert "module_frequency" in insights_doc["insights"]
    peaks_doc = json.loads((out / "data/peaks/batch3-01-omri-seq.json").read_text())
    assert peaks_doc["bins"] == ex.PEAK_BINS
    assert len(peaks_doc["peaks"]) == ex.PEAK_BINS


def test_transcode_skips_when_mp3_newer(tmp_path):
    wav = tmp_path / "t.wav"
    mp3 = tmp_path / "t.mp3"
    write_wav(wav)
    # mp3 that is newer than the wav must not be regenerated
    mp3.write_bytes(b"stub")
    import os
    os.utime(mp3, (wav.stat().st_mtime + 10, wav.stat().st_mtime + 10))
    assert ex.transcode(wav, mp3) is False
    assert mp3.read_bytes() == b"stub"


def test_peaks_shape(tmp_path):
    wav = tmp_path / "t.wav"
    write_wav(wav, frames=4000)
    peaks = ex.compute_peaks(wav)
    assert len(peaks) == ex.PEAK_BINS
    assert all(lo <= hi for lo, hi in peaks)
    assert all(-1.0 <= lo and hi <= 1.0 for lo, hi in peaks)


def test_peaks_too_short_fails(tmp_path):
    wav = tmp_path / "short.wav"
    write_wav(wav, frames=ex.PEAK_BINS - 1)
    with pytest.raises(SystemExit) as e:
        ex.compute_peaks(wav)
    assert e.value.code == 1


def test_repaired_llm_track_source_is_repair():
    assert ex.track_source("llm-x-r1") == "repair"
    assert ex.parent_slug("llm-x-r1") == "llm-x"
    assert ex.track_source("llm-x") == "llm"
