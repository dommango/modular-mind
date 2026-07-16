# Web Frontend S1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Session-1 foundation of the Modular Mind showcase site: `web/` Next.js scaffold with nav + placeholder pages, `export_frontend_data.py` (tested) producing UI-shaped JSON/mp3/peaks, typed zod loaders, Dockerfile, and a skeleton deployed to Railway.

**Architecture:** Export-then-serve (spec: `docs/superpowers/specs/2026-07-15-web-frontend-design.md`). A Python export script is the only seam between `data/` and the app; Next.js statically bakes the exported JSON at build time.

**Tech Stack:** Next.js 15 (App Router, TS, Tailwind, `output: 'standalone'`), zod, vitest; Python 3 + numpy + ffmpeg (v6.1 confirmed on PATH); Railway Docker deploy.

**Environment facts (verified):** node v20.20.0, npm 10.8.2, ffmpeg 6.1.1, `.venv` numpy 2.4.6. Branch: `feat/web-frontend`.

**Key data facts (verified):**
- `data/audio/*.wav` — 39 mono 16-bit 44.1 kHz ~10 s renders. Slugs: `01-subtractive-voice`, `batch3-NN-<archetype>`, repairs `batch3-NN-<archetype>-rN`.
- `data/output/audio_analysis.json` — dict keyed by those slugs → metrics + `verdict {makes_sound, character, flags}`.
- `data/output/audio_scores.json` — dict keyed by slug → `{fitness: int, per_metric: {...}}` (37 records — not every track has a score).
- `data/generated/batch3/manifest.json` — **list**; `name` field has NO `batch3-` prefix (`01-omri-seq`); has `audio{structural,render,...}` + `repair{status,accepted}`.
- `data/generated/llm/manifest.json` — list; `{name, archetype, source:"llm", run_id, iterations, score}`.
- `data/output/module_profiles.json` — dict keyed `plugin:model`, 269 records, fields `plugin, model, instance_count, description, tags, manual_url, role, params, inputs, outputs, documentation`.

**Task dependency graph:** 0 and 1 are independent (run in parallel). 2 needs 0+1. 3 needs 1. 4 needs 0. 5 needs 2+3+4.

---

### Task 0: Scaffold `web/` Next.js app with nav and placeholder pages

**Goal:** A building Next.js 15 App Router app in `web/` with shared nav layout and five placeholder routes.

**Files:**
- Create: `web/` (via create-next-app), then `web/src/app/layout.tsx`, `web/src/app/page.tsx`, `web/src/app/pipeline/page.tsx`, `web/src/app/listen/page.tsx`, `web/src/app/modules/page.tsx`, `web/src/app/insights/page.tsx`, `web/src/components/site-nav.tsx`
- Modify: `web/next.config.ts` (standalone output)

**Acceptance Criteria:**
- [ ] `cd web && npm run build` exits 0
- [ ] All five routes render a heading and the shared nav
- [ ] `next.config.ts` sets `output: 'standalone'`

**Verify:** `cd web && npm run build` → "Compiled successfully", route list shows `/`, `/pipeline`, `/listen`, `/modules`, `/insights`

**Steps:**

- [ ] **Step 1: Scaffold**

```bash
cd /home/dom/projects/modular-mind
npx --yes create-next-app@latest web --ts --tailwind --eslint --app --src-dir --no-import-alias --use-npm --turbopack
```

- [ ] **Step 2: Standalone output** — replace `web/next.config.ts`:

```ts
import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'standalone',
}

export default nextConfig
```

- [ ] **Step 3: Nav component** — create `web/src/components/site-nav.tsx`:

```tsx
import Link from 'next/link'

const links = [
  { href: '/', label: 'Home' },
  { href: '/pipeline', label: 'Pipeline' },
  { href: '/listen', label: 'Listen' },
  { href: '/modules', label: 'Modules' },
  { href: '/insights', label: 'Insights' },
]

export function SiteNav() {
  return (
    <nav className="flex gap-6 border-b border-zinc-800 px-6 py-4 text-sm">
      <span className="font-semibold tracking-tight">Modular Mind</span>
      {links.map((l) => (
        <Link key={l.href} href={l.href} className="text-zinc-400 hover:text-zinc-100">
          {l.label}
        </Link>
      ))}
    </nav>
  )
}
```

- [ ] **Step 4: Layout** — replace `web/src/app/layout.tsx` body wrapper:

```tsx
import type { Metadata } from 'next'
import { SiteNav } from '@/components/site-nav'
import './globals.css'

export const metadata: Metadata = {
  title: 'Modular Mind',
  description: 'An AI that learns to build modular synth patches — explore the pipeline and listen to what it makes.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        <SiteNav />
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  )
}
```

- [ ] **Step 5: Placeholder pages** — five files, identical pattern. `web/src/app/page.tsx`:

```tsx
export default function HomePage() {
  return <h1 className="text-3xl font-semibold">Modular Mind</h1>
}
```

`web/src/app/pipeline/page.tsx`, `listen/page.tsx`, `modules/page.tsx`, `insights/page.tsx` — same shape with headings `The Pipeline`, `Listen`, `Modules`, `Insights` and default-export names `PipelinePage`, `ListenPage`, `ModulesPage`, `InsightsPage`.

- [ ] **Step 6: Build, then commit**

```bash
cd web && npm run build
cd .. && git add web && git commit -m "feat(web): scaffold Next.js app with nav and placeholder routes"
```

Note: create-next-app writes `web/.gitignore` covering `node_modules/`, `.next/`. Verify `git status` shows no build artifacts before committing.

---

### Task 1: `export_frontend_data.py` with pytest coverage

**Goal:** One tested command that snapshots `data/` into `web/public/` (UI JSON + mp3 + peaks), hard-failing on missing/invalid sources, never leaving partial JSON output.

**Files:**
- Create: `export_frontend_data.py` (repo root, matches single-file stage convention)
- Test: `tests/test_export_frontend_data.py`

**Acceptance Criteria:**
- [ ] `.venv/bin/python -m pytest tests/test_export_frontend_data.py -v` all pass
- [ ] Running against real `data/` writes `web/public/data/{tracks,stages,modules,insights}.json`, `web/public/data/peaks/<slug>.json`, `web/public/audio/<slug>.mp3`
- [ ] Repair tracks link to parents (`batch3-02-drone-r1` → parent `batch3-02-drone`); parents list their repairs
- [ ] Missing source artifact → non-zero exit, no partial `data/*.json` written
- [ ] mp3 skipped when newer than source WAV (idempotent re-run is fast)

**Verify:** `.venv/bin/python -m pytest tests/test_export_frontend_data.py -v` → all PASS; then `.venv/bin/python export_frontend_data.py` → summary line `exported N tracks, M modules, K stages`

**Implementation** — `export_frontend_data.py`:

```python
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
    if slug.startswith("batch3-"):
        return "batch"
    return "handcrafted"


def build_tracks(analysis, scores, batch_manifest, llm_manifest, audio_dir):
    batch_by_name = {m["name"]: m for m in batch_manifest}
    llm_names = {m["name"] for m in llm_manifest}
    tracks = []
    for slug, rec in sorted(analysis.items()):
        if not (audio_dir / f"{slug}.wav").exists():
            print(f"  skip {slug}: no WAV", file=sys.stderr)
            continue
        parent = parent_slug(slug)
        base = (parent or slug).removeprefix("batch3-")
        manifest = batch_by_name.get(base)
        source = "llm" if slug in llm_names else track_source(slug)
        metrics = {k: v for k, v in rec.items() if isinstance(v, (int, float))}
        tracks.append({
            "slug": slug,
            "title": slug_title(slug),
            "archetype": manifest["archetype"] if manifest else base,
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
    clean = [t for t in tracks if t["verdict"]["makes_sound"] and not t["verdict"]["flags"] and t["fitness"] is not None]
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
    with open(out / "module_frequency.csv") as f:
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
    with open(out / "module_frequency.csv") as f:
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

    with tempfile.TemporaryDirectory(dir=out_dir.parent if out_dir.exists() else None) as tmp:
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
        target.parent.mkdir(parents=True, exist_ok=True)
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
```

**Test file** — `tests/test_export_frontend_data.py`. Build a miniature `data/` tree in `tmp_path` (two batch tracks, one repair variant, tiny 100-frame sine WAVs written with `wave`), monkeypatch `transcode` where ffmpeg isn't the subject, and assert:

```python
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


def test_peaks_shape(tmp_path):
    wav = tmp_path / "t.wav"
    write_wav(wav, frames=4000)
    peaks = ex.compute_peaks(wav)
    assert len(peaks) == ex.PEAK_BINS
    assert all(lo <= hi for lo, hi in peaks)
    assert all(-1.0 <= lo and hi <= 1.0 for lo, hi in peaks)
```

(`test_missing_source_fails_without_partial_output` requires the stats sources too — `make_data_tree` must also write minimal `whitelist/free_plugins.json` (`[]` → use `{"a": 1}` dict of one), `metadata/all_patches.json` (`[1]`), `raw/manifest.json` (`{"1": {}}`), `output/filtered_patches.json` (`[1]`), `output/module_frequency.csv` (header + 1 row), `output/port_registry.json` (`{}`), `output/decoded_patches.json` (`[]`), `output/analysis_summary.json` (`{"patch_complexity": {}, "top_connection_patterns": [], "most_tweaked_params": [], "module_roles": {}, "author_signatures": {}}`), `output/connection_patterns.json` (`{"port_pairs": [], "common_chains": []}`) — add these lines to `make_data_tree`.)

**Steps:**

- [ ] **Step 1:** Write `tests/test_export_frontend_data.py` (above, with the full `make_data_tree`)
- [ ] **Step 2:** Run: `.venv/bin/python -m pytest tests/test_export_frontend_data.py -v` → FAIL (module doesn't exist)
- [ ] **Step 3:** Write `export_frontend_data.py` (above)
- [ ] **Step 4:** Run tests → all PASS
- [ ] **Step 5:** Commit: `git add export_frontend_data.py tests/test_export_frontend_data.py && git commit -m "feat: export_frontend_data.py — pipeline→web data seam with tests"`

---

### Task 2: Typed data loaders (`web/src/lib/data.ts`) with vitest

**Goal:** zod-validated loaders over `public/data/*.json` — components never touch raw JSON; bad data fails at build/test time.

**Files:**
- Create: `web/src/lib/data.ts`, `web/src/lib/schemas.ts`
- Test: `web/src/lib/data.test.ts`, `web/vitest.config.ts`
- Modify: `web/package.json` (add `zod`, `vitest`, `"test": "vitest run"`)

**Acceptance Criteria:**
- [ ] `cd web && npm test` passes: valid fixture parses; invalid `character` value rejects
- [ ] Loaders read from `web/public/data/` via `fs` (server-side only) and are memoized

**Verify:** `cd web && npm test` → all PASS

**Steps:**

- [ ] **Step 1:** `cd web && npm install zod && npm install -D vitest`
- [ ] **Step 2:** `web/src/lib/schemas.ts`:

```ts
import { z } from 'zod'

export const verdictSchema = z.object({
  makes_sound: z.boolean(),
  character: z.enum(['silent', 'noise', 'rhythmic', 'drone']),
  flags: z.array(z.enum(['clipping', 'near_silent', 'dc_offset'])),
})

export const trackSchema = z.object({
  slug: z.string(),
  title: z.string(),
  archetype: z.string(),
  source: z.enum(['handcrafted', 'batch', 'repair', 'llm']),
  verdict: verdictSchema,
  fitness: z.number().nullable(),
  metrics: z.record(z.string(), z.number()),
  duration: z.number(),
  parent: z.string().nullable(),
  repairs: z.array(z.string()),
  featured: z.boolean(),
  audio: z.string(),
  peaks: z.string(),
})

export const stageSchema = z.object({
  slug: z.string(),
  title: z.string(),
  blurb: z.string(),
  inputs: z.string(),
  outputs: z.string(),
  stat: z.object({ key: z.string(), value: z.number() }).nullable(),
})

export const moduleSchema = z.object({
  key: z.string(),
  plugin: z.string(),
  model: z.string(),
  role: z.string(),
  tags: z.array(z.string()),
  description: z.string(),
  instances: z.number(),
  manual_url: z.string().nullable(),
  n_params: z.number(),
  n_inputs: z.number(),
  n_outputs: z.number(),
})

export const tracksDocSchema = z.object({ schema_version: z.literal(1), tracks: z.array(trackSchema) })
export const stagesDocSchema = z.object({ schema_version: z.literal(1), stages: z.array(stageSchema) })
export const modulesDocSchema = z.object({ schema_version: z.literal(1), modules: z.array(moduleSchema) })

export type Track = z.infer<typeof trackSchema>
export type Stage = z.infer<typeof stageSchema>
export type ModuleProfile = z.infer<typeof moduleSchema>
```

- [ ] **Step 3:** `web/src/lib/data.ts`:

```ts
import fs from 'node:fs'
import path from 'node:path'
import { cache } from 'react'
import { tracksDocSchema, stagesDocSchema, modulesDocSchema } from './schemas'

const dataDir = () => path.join(process.cwd(), 'public', 'data')

function loadJson(name: string): unknown {
  return JSON.parse(fs.readFileSync(path.join(dataDir(), name), 'utf-8'))
}

export const getTracks = cache(() => tracksDocSchema.parse(loadJson('tracks.json')).tracks)
export const getStages = cache(() => stagesDocSchema.parse(loadJson('stages.json')).stages)
export const getModules = cache(() => modulesDocSchema.parse(loadJson('modules.json')).modules)
export const getFeaturedTracks = cache(() => getTracks().filter((t) => t.featured))
```

- [ ] **Step 4:** `web/vitest.config.ts` (`import { defineConfig } from 'vitest/config'; export default defineConfig({ test: { environment: 'node' } })`) and `web/src/lib/data.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { trackSchema } from './schemas'

const validTrack = {
  slug: 'batch3-01-omri-seq', title: 'Omri Seq', archetype: 'omri-seq', source: 'batch',
  verdict: { makes_sound: true, character: 'rhythmic', flags: [] },
  fitness: 80, metrics: { rms: 0.1 }, duration: 10, parent: null, repairs: [],
  featured: true, audio: 'audio/batch3-01-omri-seq.mp3', peaks: 'data/peaks/batch3-01-omri-seq.json',
}

describe('trackSchema', () => {
  it('accepts a valid track', () => {
    expect(trackSchema.parse(validTrack).slug).toBe('batch3-01-omri-seq')
  })
  it('rejects unknown character', () => {
    const bad = { ...validTrack, verdict: { ...validTrack.verdict, character: 'melodic' } }
    expect(() => trackSchema.parse(bad)).toThrow()
  })
  it('rejects unknown source', () => {
    expect(() => trackSchema.parse({ ...validTrack, source: 'wild' })).toThrow()
  })
})
```

- [ ] **Step 5:** Add `"test": "vitest run"` to `web/package.json` scripts. Run `npm test` → PASS. Commit: `git add web && git commit -m "feat(web): zod schemas and typed data loaders with vitest"`

---

### Task 3: Run the real export; commit exported data

**Goal:** Real `web/public/data/` + `web/public/audio/` committed so builds are reproducible without `data/`.

**Files:**
- Create: `web/public/data/*.json`, `web/public/data/peaks/*.json`, `web/public/audio/*.mp3` (generated)
- Modify: `web/.gitignore` — ensure exported outputs are NOT ignored

**Acceptance Criteria:**
- [ ] `.venv/bin/python export_frontend_data.py` exits 0, prints `exported 39 tracks ...` (count = playable WAVs)
- [ ] Total committed audio < 10 MB; `web/public/data/` < 1.5 MB
- [ ] Spot-check: `batch3-02-drone-r1` has `"parent": "batch3-02-drone"`; 6 tracks have `"featured": true`

**Verify:** `.venv/bin/python export_frontend_data.py && du -sh web/public/audio web/public/data && jq '.tracks[] | select(.featured) | .slug' web/public/data/tracks.json | wc -l` → 6

**Steps:**

- [ ] **Step 1:** `.venv/bin/python export_frontend_data.py`
- [ ] **Step 2:** Spot-check acceptance criteria (jq commands above)
- [ ] **Step 3:** `git add web/public && git commit -m "feat(web): export real pipeline data and transcoded audio"`

---

### Task 4: Dockerfile + Railway config for `web/`

**Goal:** A production image for the standalone Next build, ready for a `modular-mind-web` Railway service with root directory `web/`.

**Files:**
- Create: `web/Dockerfile`, `web/.dockerignore`, `web/railway.json`

**Acceptance Criteria:**
- [ ] `docker build` succeeds locally (or, if the Docker daemon is unavailable, the Dockerfile is byte-for-byte the standard Next standalone recipe below and `npm run build` passes — flag it for verification at deploy)
- [ ] Image runs `node server.js` on port 3000 as non-root

**Verify:** `cd web && docker build -t mm-web:test . && docker run -d -p 13000:3000 mm-web:test && curl -s -o /dev/null -w '%{http_code}' localhost:13000` → `200`

**Steps:**

- [ ] **Step 1:** `web/Dockerfile`:

```dockerfile
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production
RUN groupadd -r app && useradd -r -g app app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER app
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
```

- [ ] **Step 2:** `web/.dockerignore`:

```
node_modules
.next
.git
```

- [ ] **Step 3:** `web/railway.json`:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": { "sleepApplication": true, "restartPolicyType": "ON_FAILURE" }
}
```

- [ ] **Step 4:** Build + smoke-test locally (Verify command). Commit: `git add web/Dockerfile web/.dockerignore web/railway.json && git commit -m "feat(web): Dockerfile and Railway config for standalone Next build"`

---

### Task 5: Deploy skeleton to Railway

**Goal:** `modular-mind-web` service live on a Railway domain serving the skeleton.

**Files:** none (operations task)

**Acceptance Criteria:**
- [ ] New service `modular-mind-web` exists in the existing Railway project (`modular-mind-render`'s project, id `2bc068b3-42bd-4ada-b04d-560c27a0a264`)
- [ ] Service root directory is `web/` so its `railway.json`/Dockerfile apply and the render service is untouched
- [ ] Public domain returns HTTP 200 on `/` and `/listen`

**Verify:** `curl -s -o /dev/null -w '%{http_code}' https://<generated-domain>/listen` → `200`

**Steps:**

- [ ] **Step 1:** `railway link 2bc068b3-42bd-4ada-b04d-560c27a0a264` (if not already linked), then `railway add --service modular-mind-web`
- [ ] **Step 2:** Set the service's root directory to `web`. Try `railway service` CLI settings first; if the CLI version has no root-directory flag, set it in the Railway dashboard (Service → Settings → Root Directory = `web`) — this is the one potentially-manual step; surface it to the user if dashboard access is needed.
- [ ] **Step 3:** `railway up --service modular-mind-web` from the repo root (or `railway up` from `web/` once root dir is set); watch build logs
- [ ] **Step 4:** `railway domain --service modular-mind-web` to generate the public domain; run the Verify curl on `/` and `/listen`
- [ ] **Step 5:** Record the domain in `web/README.md` (create: one paragraph — what the app is, `npm run dev`, export command, deploy command, live URL). Commit: `git add web/README.md && git commit -m "docs(web): deployment notes and live URL"`

---

## Self-Review

- **Spec coverage:** S1 scope = scaffold, export script + tests, loaders, skeleton deploy — Tasks 0–5 cover all; S2–S6 items (design system, real pages, e2e) are explicitly later sessions per spec roadmap.
- **Placeholders:** none — all code inline; Task 5's dashboard fallback is a documented manual step, not a TBD.
- **Type consistency:** export writes `{schema_version, tracks|stages|modules|insights}` docs; `schemas.ts` mirrors exactly (verdict enums match `analyze_audio.py` vocabulary: `silent|noise|rhythmic|drone`, flags `clipping|near_silent|dc_offset`; source enum matches `track_source()` + llm).
