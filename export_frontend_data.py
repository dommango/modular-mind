#!/usr/bin/env python3
"""Export pipeline artifacts into web/public/ for the frontend.

The single seam between pipeline and UI. Reads data/, writes:
  web/public/data/{tracks,stages,modules,insights}.json  (schema_version'd)
  web/public/data/peaks/<slug>.json                      (waveform peaks)
  web/public/audio/<slug>.mp3                            (transcoded renders)

Idempotent. Hard-fails on missing sources. JSON is staged to a temp dir and
moved into place so the output set is never partial.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1
PEAK_BINS = 800
MP3_BITRATE = "128k"
FEATURED_COUNT = 6
FEATURED_EXTRA: tuple[str, ...] = ()  # manual curation additions, by slug

STAGES = [
    ("00", "Build whitelist", "Catalog every free VCV Rack plugin so only reproducible patches enter the corpus.", "VCV library API", "free_plugins.json", "free_plugins"),
    ("01", "Fetch metadata", "Scrape PatchStorage for every VCV Rack patch listing.", "PatchStorage API", "all_patches.json", "patches_listed"),
    ("02", "Download patches", "Download each .vcv file, crash-safe and resumable.", "all_patches.json", "raw/*.vcv", "patches_downloaded"),
    ("03", "Parse & filter", "Keep only Rack-2, liked, fully-free patches of sane size.", "raw/*.vcv", "filtered_patches.json", "patches_filtered"),
    ("04", "Aggregate", "Count module usage and co-occurrence across the corpus.", "filtered_patches.json", "module_frequency.csv", "modules_seen"),
    ("05", "Port registry", "Clone plugin source and parse C++ enums into port names.", "plugin repos", "port_registry.json", "ports_mapped"),
    ("06", "Deep analysis", "Decode every patch into named connections and parameter stats.", "filtered_patches.json", "decoded_patches.json", "patches_decoded"),
    ("07", "Module profiles", "Merge library metadata and manuals into one profile per module.", "port_registry.json", "module_profiles.json", "modules_profiled"),
    ("08", "Reference files", "Write human/AI-readable markdown for every module and patch.", "module_profiles.json", "reference/*.md", "reference_docs"),
    ("09", "Classify & learn", "Distill patch archetypes and connection grammar.", "decoded_patches.json", "archetypes.md", None),
    ("10", "Knowledge base", "Synthesis fundamentals and patch-building guides.", "everything above", "reference/*.md", None),
    ("gen", "Generate", "Compose new .vcv patches from learned archetypes.", "knowledge base", "generated/*.vcv", "patches_generated"),
    ("audition", "Render & listen", "Render each patch headlessly, analyze the audio, score fitness, auto-repair.", "generated/*.vcv", "audio + verdicts", "tracks_rendered"),
]


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path):
    if not path.exists():
        fail(f"missing source artifact: {path}")
    with open(path) as f:
        return json.load(f)


def slug_title(slug):
    stem = slug.removeprefix("batch3-")
    repair = None
    if "-r" in stem and stem.rsplit("-r", 1)[1].isdigit():
        stem, repair = stem.rsplit("-r", 1)
    parts = stem.split("-")
    if parts and parts[0].isdigit():
        parts = parts[1:]
    title = " ".join(p.capitalize() for p in parts)
    return f"{title} (repair {repair})" if repair else title


def parent_slug(slug):
    if "-r" in slug:
        stem, _, n = slug.rpartition("-r")
        if n.isdigit():
            return stem
    return None


def track_source(slug):
    if parent_slug(slug):
        return "repair"
    if slug.startswith("llm-"):
        return "llm"
    if slug.startswith("batch3-"):
        return "batch"
    return "handcrafted"


def build_tracks(analysis, scores, batch_manifest, llm_manifest, audio_dir):
    batch_by_name = {m["name"]: m for m in batch_manifest}
    # analysis keys LLM tracks as "llm-<manifest name>"; map both ways
    llm_by_slug = {f"llm-{m['name']}": m for m in llm_manifest}
    tracks = []
    for slug, rec in sorted(analysis.items()):
        if not (audio_dir / f"{slug}.wav").exists():
            print(f"  skip {slug}: no WAV", file=sys.stderr)
            continue
        parent = parent_slug(slug)
        base = (parent or slug).removeprefix("batch3-")
        manifest = batch_by_name.get(base)
        llm_rec = llm_by_slug.get(parent or slug)
        source = track_source(slug)
        if manifest:
            archetype = manifest["archetype"]
        elif llm_rec:
            archetype = llm_rec["archetype"]
        else:
            archetype = base
        metrics = {k: v for k, v in rec.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        tracks.append({
            "slug": slug,
            "title": slug_title(slug),
            "archetype": archetype,
            "source": source,
            "verdict": rec["verdict"],
            "fitness": scores.get(slug, {}).get("fitness"),
            "metrics": metrics,
            "duration": rec["duration"],
            "parent": parent,
            "repairs": [],
            "featured": False,
            "audio": f"audio/{slug}.mp3",
            "peaks": f"data/peaks/{slug}.json",
        })
    if not tracks:
        fail("no playable tracks found")
    by_slug = {t["slug"]: t for t in tracks}
    for t in tracks:
        if t["parent"] and t["parent"] in by_slug:
            by_slug[t["parent"]]["repairs"].append(t["slug"])
    clean = [t for t in tracks
             if t["verdict"]["makes_sound"] and not t["verdict"]["flags"]
             and t["fitness"] is not None]
    top = sorted(clean, key=lambda t: t["fitness"], reverse=True)[:FEATURED_COUNT]
    for t in top:
        t["featured"] = True
    for slug in FEATURED_EXTRA:
        if slug in by_slug:
            by_slug[slug]["featured"] = True
    return tracks


def build_stage_stats(data_dir, tracks):
    out = data_dir / "output"
    stats = {}
    stats["free_plugins"] = len(load_json(data_dir / "whitelist/free_plugins.json"))
    stats["patches_listed"] = len(load_json(data_dir / "metadata/all_patches.json"))
    stats["patches_downloaded"] = len(load_json(data_dir / "raw/manifest.json"))
    stats["patches_filtered"] = len(load_json(out / "filtered_patches.json"))
    freq_path = out / "module_frequency.csv"
    if not freq_path.exists():
        fail(f"missing source artifact: {freq_path}")
    with open(freq_path) as f:
        stats["modules_seen"] = sum(1 for _ in f) - 1
    stats["ports_mapped"] = len(load_json(out / "port_registry.json"))
    stats["patches_decoded"] = len(load_json(out / "decoded_patches.json"))
    stats["modules_profiled"] = len(load_json(out / "module_profiles.json"))
    stats["reference_docs"] = sum(1 for _ in (data_dir / "reference").rglob("*.md"))
    stats["patches_generated"] = sum(1 for _ in (data_dir / "generated").rglob("*.vcv"))
    stats["tracks_rendered"] = len(tracks)
    return stats


def build_stages(stats):
    return [
        {"slug": s, "title": t, "blurb": b, "inputs": i, "outputs": o,
         "stat": {"key": key, "value": stats[key]} if key else None}
        for s, t, b, i, o, key in STAGES
    ]


def build_modules(profiles):
    return [
        {"key": key, "plugin": p["plugin"], "model": p["model"], "role": p["role"],
         "tags": p["tags"], "description": p["description"],
         "instances": p["instance_count"], "manual_url": p["manual_url"],
         "n_params": len(p["params"]), "n_inputs": len(p["inputs"]), "n_outputs": len(p["outputs"])}
        for key, p in sorted(profiles.items(), key=lambda kv: -kv[1]["instance_count"])
    ]


def build_insights(data_dir):
    out = data_dir / "output"
    summary = load_json(out / "analysis_summary.json")
    patterns = load_json(out / "connection_patterns.json")
    freq_path = out / "module_frequency.csv"
    if not freq_path.exists():
        fail(f"missing source artifact: {freq_path}")
    with open(freq_path) as f:
        freq = [{"plugin": r["plugin"], "model": r["model"],
                 "patch_count": int(r["patch_count"]), "pct_patches": float(r["pct_patches"])}
                for r in csv.DictReader(f)]
    return {
        "module_frequency": freq[:30],
        "port_pairs": patterns["port_pairs"][:50],
        "common_chains": patterns["common_chains"][:20],
        "patch_complexity": summary["patch_complexity"],
        "top_connection_patterns": summary["top_connection_patterns"][:20],
    }


def compute_peaks(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(samples) < PEAK_BINS:
        fail(f"{wav_path.name}: too short for peaks ({len(samples)} frames < {PEAK_BINS})")
    bins = np.array_split(samples, PEAK_BINS)
    return [[round(float(b.min()), 4), round(float(b.max()), 4)] for b in bins]


def transcode(wav_path, mp3_path):
    if mp3_path.exists() and mp3_path.stat().st_mtime >= wav_path.stat().st_mtime:
        return False
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", MP3_BITRATE, str(mp3_path)],
        capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"ffmpeg failed for {wav_path.name}: {result.stderr.strip()}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("web/public"))
    args = ap.parse_args()
    data_dir, out_dir = args.data_dir, args.out

    analysis = load_json(data_dir / "output/audio_analysis.json")
    scores = load_json(data_dir / "output/audio_scores.json")
    batch_manifest = load_json(data_dir / "generated/batch3/manifest.json")
    llm_manifest = load_json(data_dir / "generated/llm/manifest.json")
    profiles = load_json(data_dir / "output/module_profiles.json")

    tracks = build_tracks(analysis, scores, batch_manifest, llm_manifest, data_dir / "audio")
    stages = build_stages(build_stage_stats(data_dir, tracks))
    modules = build_modules(profiles)
    insights = build_insights(data_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        staging = Path(tmp) / "data"
        (staging / "peaks").mkdir(parents=True)
        for name, payload in [("tracks", tracks), ("stages", stages),
                              ("modules", modules), ("insights", insights)]:
            doc = {"schema_version": SCHEMA_VERSION, name: payload}
            (staging / f"{name}.json").write_text(json.dumps(doc, indent=1))
        for t in tracks:
            peaks = compute_peaks(data_dir / "audio" / f"{t['slug']}.wav")
            (staging / "peaks" / f"{t['slug']}.json").write_text(
                json.dumps({"schema_version": SCHEMA_VERSION, "bins": PEAK_BINS, "peaks": peaks}))
        target = out_dir / "data"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))

    audio_out = out_dir / "audio"
    audio_out.mkdir(parents=True, exist_ok=True)
    n_transcoded = sum(
        transcode(data_dir / "audio" / f"{t['slug']}.wav", audio_out / f"{t['slug']}.mp3")
        for t in tracks)
    print(f"exported {len(tracks)} tracks ({n_transcoded} transcoded), "
          f"{len(modules)} modules, {len(stages)} stages")


if __name__ == "__main__":
    main()
