# Modular Mind Web Frontend — Design

**Date:** 2026-07-15
**Status:** Approved (brainstorm session)
**Branch:** `feat/web-frontend`

## Context

Modular Mind is a CLI-only Python pipeline: it builds a corpus of VCV Rack
patches (stages 00–10), generates new `.vcv` patches, and closes the loop with
render → analyze → score → repair → LLM-generate. All of its output lives in
JSON/CSV/markdown files and WAVs that only the maintainer ever sees.

This project adds a **public showcase web app** that tells the story to end
users: what each pipeline stage does, what the machine learned, and — the
centerpiece — what the generated patches actually *sound* like, playable in
the browser.

Decisions made during brainstorming:

- **Audience:** public showcase (portfolio/educational).
- **Hosting:** hosted web app on **Railway** (same platform as the existing
  `modular-mind-render` service).
- **Interactivity at launch:** read-only explorer. No pipeline execution from
  the web. The existing `render-service/` FastAPI app is the documented
  upgrade path for a future "render live" feature — the design leaves a slot
  for it but does not build against it.
- **Audio scope:** curated + browsable — all local renders transcoded to
  compressed audio, featured "best of" selection, full list filterable.
- **Location:** `web/` subdirectory of this repo, versioned with the pipeline.
- **Stack:** Next.js (App Router) + React + TypeScript + Tailwind.

## Goals

1. A beautiful, fast, public site explaining the full pipeline stage by stage
   with real corpus numbers.
2. In-browser listening: waveform players over the rendered patches, with the
   analyzer's verdicts (character, flags, fitness score) and each patch's
   journey (validate → render → analyze → repair) made visible.
3. A repeatable one-command data export so the site refreshes whenever the
   pipeline reruns.
4. A design system iterated in **Claude Design** (via `/design-sync`) across
   sessions, consumed by the app.

## Non-Goals (launch)

- No user accounts, comments, or uploads.
- No live rendering or pipeline triggering from the web.
- No shipping of large intermediates (`patch_index.json` 15 MB,
  `co_occurrence.csv` 12 MB, `data/metadata/` 11 MB) to the browser.
- No CMS — copy lives in the repo.

## Architecture

**Export-then-serve.** A Python export script is the single seam between
pipeline and UI; the Next.js app never reads `data/` directly.

```
data/output/*, data/generated/*, data/audio/*, data/reference/*
        │
        ▼  export_frontend_data.py  (repo root, stdlib + ffmpeg)
web/public/data/*.json      — curated, UI-shaped JSON (small, versioned schema)
web/public/audio/*.mp3      — transcoded renders (~10x smaller than WAV)
web/public/data/peaks/*.json — precomputed waveform peaks per track
        │
        ▼  next build  (standalone output)
Railway service (Dockerfile), sleeps when idle
```

### Export script (`export_frontend_data.py`)

Reads (all already produced by the pipeline):

| Source | Feeds |
| --- | --- |
| `data/generated/batch3/manifest.json`, `data/generated/llm/manifest.json` | track list: name, archetype, audio verdicts, repair status, score |
| `data/output/audio_analysis.json` | per-track metrics + verdict (`makes_sound`, `character`, `flags`) |
| `data/output/audio_scores.json` | fitness 0–100 + per-metric breakdown |
| `data/output/module_profiles.json` | module explorer (269 modules) |
| `data/output/analysis_summary.json`, `connection_patterns.json`, `module_frequency.csv` | insights page |
| `data/output/patch_playability.json`, `corpus_metric_bands.json` | corpus stats for stage cards |
| `data/reference/*.md` frontmatter | stage/knowledge copy support |
| `data/audio/*.wav` | mp3 transcode + peaks |

Writes UI-shaped JSON with an explicit schema version:

- `tracks.json` — one record per playable render: slug, title, archetype,
  source (`handcrafted` / `batch` / `repair` / `llm`), verdict, flags,
  fitness, metrics, duration, repair lineage (`batch3-02-drone` →
  `batch3-02-drone-r1`), `featured: bool` (curation list checked into the
  script).
- `stages.json` — per-stage numbers pulled live from artifacts (patches
  downloaded/filtered, modules profiled, etc.), merged with copy written in
  the repo.
- `modules.json` — trimmed module profiles (name, plugin, role, tags,
  description, instance count, port summary).
- `insights.json` — top connection patterns, common chains, module frequency
  top-N, complexity distribution.
- `peaks/<slug>.json` — ~800 min/max pairs per track for waveform rendering
  (no client-side WAV decode).

Behavior: idempotent, overwrites outputs, fails loudly if a source artifact is
missing or fails schema sanity checks, skips transcode when the mp3 is newer
than the WAV. Requires `ffmpeg` on PATH.

### Web app (`web/`)

- Next.js App Router, TypeScript, Tailwind CSS. Static generation for all
  pages (data is baked at build time); `output: 'standalone'` for a slim
  Docker image.
- Data access through one typed module `web/src/lib/data.ts` (zod-validated
  loaders over `public/data/*.json`) — components never touch raw JSON.
- Audio playback: a custom player component drawing precomputed peaks to
  `<canvas>` + native `<audio>` element (no wavesurfer dependency; peaks are
  already computed server-side).
- Charts on Insights follow the `dataviz` skill's system.

### Pages

1. **`/` Home** — one-scroll story: corpus → learning → generation →
   listening loop; hero with a featured player; links into each section.
2. **`/pipeline`** — the stage walk. A flow of stage cards (00–10, then
   generate → validate → render → analyze → score → repair → LLM loop), each
   with purpose, inputs → outputs, and real numbers from `stages.json`.
   Stage detail expands in place; each stage has an anchor (`/pipeline#03`)
   so stages are deep-linkable without separate routes.
3. **`/listen`** — the centerpiece gallery. Track cards with waveform player,
   verdict badges (character `rhythmic`/`drone`/`noise`/`silent`, flags
   `clipping`/`near_silent`/`dc_offset`, fitness score), filters by
   archetype / character / source / score, and a track detail view showing
   its journey (structural PASS → render OK → analyzer verdict → repair
   lineage → final fitness).
4. **`/modules`** — searchable explorer over 269 module profiles: role
   filter (Source/Processor/…), tags, corpus frequency, port summary.
5. **`/insights`** — corpus dataviz: module frequency, top connection
   patterns, common chains, patch complexity.

### Deployment

- `web/Dockerfile` (multi-stage: install → build → standalone runner). New
  Railway service `modular-mind-web` in the existing project, with
  `sleepApplication` consistent with the render service. The repo-root
  `railway.json` currently targets `render-service/Dockerfile`; the web
  service gets its own Railway service config (root directory `web/`) so the
  two deploy independently.
- Exported data + mp3s are committed to the repo (small: JSON < 1 MB total,
  mp3s ~4–6 MB) so builds are reproducible without `data/` present.

### Error handling

- Export script: hard-fail with a clear message per missing/invalid source;
  never write partial output sets (write to temp dir, move into place).
- App: loaders zod-validate at build time — bad data fails the build, not the
  user. Missing audio file for a track ⇒ track renders without player and
  logs at build.
- 404s for unknown module/track slugs.

## Design system & Claude Design workflow

- `web/design-system/` holds the component library previews (tokens,
  typography, verdict badges, stage card, track card, waveform player shell,
  filter chips, chart styles) as self-contained HTML previews with `@dsCard`
  markers.
- Synced to a Claude Design project ("Modular Mind") with `/design-sync`
  (DesignSync tool) — incremental, component-at-a-time.
- Loop across sessions: iterate visually in Claude Design → pull decisions
  back into Tailwind config + React components → re-sync. The design system
  is the contract; app components implement it.
- Aesthetic direction (to refine in Claude Design session): dark,
  studio/hardware-inspired — patch-cable accents, oscilloscope-style
  waveforms — while staying clean and typographic, not skeuomorphic.

## Multi-session roadmap

Each session is one bounded chunk with a shippable checkpoint; state lives in
this spec + the implementation plan + commits, not in conversation memory.

| Session | Where | Deliverable |
| --- | --- | --- |
| S1 | Claude Code | This spec + implementation plan; scaffold `web/`; `export_frontend_data.py` + tests; skeleton (nav + placeholder pages) deployed to Railway |
| S2 | Claude Design | Brand + design system: tokens, badges, cards, player shell; synced via DesignSync; decisions folded into Tailwind config |
| S3 | Claude Code | `/pipeline` stage walk with real numbers |
| S4 | Claude Code | `/listen` gallery: player, verdicts, filters, track detail |
| S5 | Claude Code | `/modules` explorer + `/insights` dataviz |
| S6 | Claude Code | Home page, polish pass against design system, Playwright e2e, SEO/meta, `/ship` |

Sessions 3–5 are independent once S1–S2 land and can be reordered or fanned
out to subagents.

## Testing

- **Export script:** pytest — given fixture `data/` inputs, asserts output
  schemas, repair-lineage linking, curation flags, and hard-fail on missing
  sources. Lives in existing `tests/`.
- **Web unit:** vitest for `lib/data.ts` loaders (zod schemas reject bad
  shapes) and filter logic on `/listen`.
- **E2E (S6):** Playwright — load `/listen`, filter by archetype, play a
  track (audio element reaches `readyState >= 2`), open track detail; stage
  walk renders all stages. Satisfies the `require-tests-for-pr` hook.

## Risks / open items

- **mp3 vs ogg:** mp3 chosen for universal support; revisit only if quality
  at ~128 kbps disappoints on drone content.
- **Corpus renders:** ~258 corpus WAVs were rendered via the remote service
  but are not all in `data/audio/`; launch scope is the local generated-patch
  renders (39). Corpus audio can join `/listen` later via the same export
  path.
- **Live rendering (post-launch):** `/listen` detail page reserves a spot for
  "render a variation" wired to the existing `render-service` `POST /render`
  API behind a server-side token. Explicitly out of scope for S1–S6.
