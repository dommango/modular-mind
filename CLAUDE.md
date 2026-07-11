# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

A multi-stage Python pipeline that builds a corpus of VCV Rack patches and module knowledge, then uses that corpus to generate new `.vcv` patch files. Goal: train AI to produce structurally valid, musically coherent modular synth patches.

## Setup

```bash
pip install -r requirements.txt   # only dep: requests
# Create .env with: GITHUB_TOKEN=<token>   (any PAT, no scopes needed)
# Stage 3 also needs system libzstd: apt install libzstd1
```

Single dependency (`requests`) plus stdlib. Stage 3 dynamically loads `libzstd` via ctypes for Rack v2 patch decompression.

## Running Stages

All stages are standalone scripts run from the project root. They communicate exclusively through files in `data/`:

```bash
python3 00_build_whitelist.py        # ~30s,  needs network + GITHUB_TOKEN
python3 01_fetch_metadata.py         # ~2min, needs network
python3 02_download_patches.py       # ~1hr,  needs network, RESUMABLE
python3 03_parse_and_filter.py       # seconds
python3 04_aggregate.py              # seconds
python3 05_build_port_registry.py    # ~5min, clones ~25 GitHub repos to data/repos/
python3 06_deep_analysis.py          # seconds (imports stage 03 via importlib)
python3 07_build_module_profiles.py  # ~2min, needs network (PatchStorage detail API)
python3 08_generate_reference_files.py  # seconds, writes data/reference/
python3 09_classify_and_learn.py     # seconds, writes archetype docs
python3 10_build_knowledge_base.py   # seconds, writes synthesis fundamentals
```

Re-running a stage overwrites its outputs. Stage 2 alone is resumable via `data/raw/manifest.json` — never delete that file unless restarting from scratch.

## Patch Generation & Validation

Separate from the corpus pipeline:

```bash
python3 generate_patches.py                  # writes data/generated/*.vcv
python3 generate_batch.py                    # writes data/generated/batch3/*.vcv
python3 validate_patch.py <file.vcv>         # structural check single file
python3 validate_patch.py data/generated/    # batch validate directory
```

Generated patches open in VCV Rack via `\\wsl$\Ubuntu<absolute-path>`. AudioInterface modules require manual driver selection on first open.

## Audio Listening Loop

Renders patches to WAV and scores what they sound like — the acoustic complement
to `validate_patch.py`'s structural checks. Requires the project venv
(`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`):

```bash
.venv/bin/python render_patch.py <file.vcv | dir>   # → data/audio/<name>.wav
.venv/bin/python analyze_audio.py data/audio/       # → data/output/audio_analysis.json
.venv/bin/python audition.py                        # validate→render→analyze data/generated/,
                                                    #   updates batch3 manifest + summary table
.venv/bin/python -m pytest tests/                   # unit tests (injection, metrics, merge)
```

**Do not use librosa/numba here** — their JIT kernels crash LLVM on this ARM CPU
(WSL2 aarch64), even at import time. `analyze_audio.py` is pure numpy by design.

### Headless render recipe (verified 2026-07, Rack 2.6.6)

There is no Linux ARM64 Rack build; rendering drives the **Windows** Rack install
(`/mnt/c/Program Files/VCV/Rack2Free/Rack.exe`) through WSL interop:

- `render_patch.py` injects a **VCV Recorder** (output path/format pre-set in its
  module JSON, teed off the cables feeding `Core:AudioInterface`) plus a
  **Fundamental LFO** whose square output gates the Recorder — exactly
  `RENDER_SECONDS` of engine time, WAV finalized mid-run when the gate falls.
- Headless Rack blocks on stdin ("Press enter to exit."); the renderer holds
  stdin open, polls for the finished WAV, then closes stdin → clean shutdown.
  With no audio device the engine free-runs in real time on its fallback thread.
  (`masterModuleId` is useless here: Recorder 2.0.3's primary-module code is
  commented out in its source — renders are real-time, ~`RENDER_SECONDS` + 2s each.)
- Scratch user dir `C:\Users\domma\AppData\Local\Temp\rack-headless` (constants in
  `config.py`) isolates settings/autosave from the real install. Its
  `plugins-win-x64\` holds NTFS junctions to just Fundamental + VCV-Recorder —
  startup 0.04s vs ~30s with the full plugin set. If the Temp dir gets wiped,
  recreate with:
  `cmd.exe /c "mklink /J <scratch>\plugins-win-x64\<P> C:\Users\domma\AppData\Local\Rack2\plugins-win-x64\<P>"`
  for each of `Fundamental`, `VCV-Recorder`.
- Recorder port IDs (from source, cached at `data/repos/VCV-Recorder`):
  params 0=Gain 1=Rec; inputs 0=Gate 1=Trig 2=Left 3=Right; gate threshold ≥2V.
- Three failure modes handled by `render_patch.py`, don't regress them:
  1. **Scratch `log.txt` must be deleted before every launch.** If the previous
     exit was unclean, `logger::wasTruncated()` makes Rack pop a blocking "Rack
     crashed" osdialog *before* loading the patch — even headless — and every
     timed-out kill re-truncates the log, wedging all subsequent renders.
  2. Killing the WSL interop proxy does NOT kill the Windows process; the
     renderer force-kills leftover `Rack.exe` whose command line matches the
     scratch dir (PowerShell), never by bare image name (would hit GUI Rack).
  3. Scratch `settings.json` keeps `autoCheckUpdates: false` — the version-check
     request can stall startup when it hangs.

### Remote render backend (Railway)

An alternative to the Windows/WSL-interop path above: a small authenticated
FastAPI service (`render-service/`) runs official Rack Free 2.6.6 **lin-x64**
headless in a Docker container on Railway (native x86_64 Linux — the ARM64
gap that forces the Windows recipe locally doesn't exist there). It reuses
`render_patch.py` unchanged; only the platform-conditional bits added by
`rack_is_windows()`/`rack_invocation()` differ (see module docstring).

- **Dispatch rule:** `render_client.render()` (what `audition.py` always
  imports) checks `RACK_RENDER_URL` in `.env` — unset keeps rendering locally
  via `render_patch.render()`; set, it POSTs the parsed patch JSON to
  `{RACK_RENDER_URL}/render` with `Authorization: Bearer {RENDER_TOKEN}` and
  writes back the returned WAV bytes. `render_patch.py`'s own CLI stays
  local-only regardless.
- **Transient-failure retry:** `render_client.remote_render()` retries only
  *transient* failures — connection errors, timeouts, HTTP 502/503/504 — with
  exponential backoff (`RETRY_BASE_DELAY`×2ⁿ, 5 attempts, capped at
  `RETRY_MAX_DELAY`). Deterministic errors (401/413/422/500) fail fast so a
  wedged real-time render isn't re-attempted 5×. This is what makes a bulk
  corpus run survivable — an idle Railway service cold-starts (502) and local
  DNS can blip mid-run without losing the patch.
- **Railway project:** `modular-mind-render` (Dominic Mangonon's Projects),
  service `modular-mind-render`, domain
  `https://modular-mind-render-production.up.railway.app`. `railway.json` at
  repo root points Railway's Dockerfile builder at `render-service/Dockerfile`;
  build context is the repo root (`.dockerignore` keeps the untracked `data/`
  and `.venv/` out of the upload).
- **Redeploy:** `railway up` from the repo root (must be linked first —
  `railway link 2bc068b3-42bd-4ada-b04d-560c27a0a264` or `railway service
  <id>` if working from a fresh checkout). First build is slow (~15-30 min):
  `render-service/Dockerfile`'s `plugin-builder` stage compiles VCV-Recorder
  from the Rack SDK, vendoring ffmpeg + lame + libopus from source since
  Recorder isn't downloadable prebuilt without a VCV account login. That
  layer is cached on `RECORDER_COMMIT` (a Dockerfile `ARG`) — redeploys that
  don't touch it are fast. **Observed once:** the vendor build hung
  completely mid-`make dep` (opus/SILK compile) with zero log output for
  ~55 minutes and no error — Docker/BuildKit can't detect a wedged
  subprocess inside one long `RUN` step. `railway down` doesn't apply to an
  in-progress build; the fix was just `railway up` again (`railway status
  --json` → `...latestDeployment.status`; a plain retry succeeded and
  finished normally). If it recurs, split `make dep` and `make dist` into
  separate `RUN` lines to narrow down which vendored dep is stalling.
- **Pinned versions:** Rack Free / SDK 2.6.6 lin-x64; VCV-Recorder commit
  `defcf7890bc9630e288ca7cfcc4dd998eb314ccf` (vendors ffmpeg 7.1.1, lame
  3.100, libopus 1.5.2 via its own Makefile).
- **Real-time caveat:** headless Rack has no audio device, so the engine
  free-runs on its fallback thread — a render of `seconds` takes roughly
  `seconds` of wall-clock time plus Rack startup, same as the Windows path.
  Verified live: a 3s render completed in 4.7s end-to-end, a 2s render in
  3.7s. `RACK_RENDER_URL` requests should still budget for Railway cold-start
  on top (`sleepApplication: true` in `railway.json` — the service sleeps
  when idle). `render_client.remote_timeout()` sizes the HTTP client timeout
  accordingly (`10s connect, 2*seconds + 180s read`).
- **Local verification without Railway:** the whole image can be built and
  run locally under Docker Desktop's `linux/amd64` emulation even on an
  ARM64 host — `docker build --platform linux/amd64 -f
  render-service/Dockerfile -t modular-mind-render:test .` then `docker run
  -p 18000:8000 -e RENDER_TOKEN=... modular-mind-render:test`. Emulation
  makes real-time rendering run much slower than native (observed ~4s to
  ~90s wall-clock for the same 3-second render, varying with host load) —
  don't read emulated timing as representative of Railway's native
  performance.
- `render_patch.py`'s `_wait_for_wav()` reports the WAV's final size once it
  stops growing rather than gating on reaching the calculated `min_bytes`
  exactly — block-boundary timing can land a genuinely finished render a
  hair under that theoretical minimum. `render()` applies its own tolerance
  (90% of `min_bytes`) when deciding whether the result is acceptable.

### Cloud audition — known corpus (Railway worker)

Where the render-service renders arbitrary patches on demand over HTTP, the
**corpus worker** batch-plays *known* community patches (`data/raw/`) through
render→analyze to build acoustic ground truth for the generators — tying real
patches' parameter settings to what they sound like.

```bash
.venv/bin/python corpus_audition.py --limit 20   # render+score next 20 corpus patches
.venv/bin/python corpus_audition.py --ids 183245 190118 --redo  # re-audition specific ids
.venv/bin/python corpus_audition.py --summary    # stats from corpus_audio_analysis.json
.venv/bin/python plugin_sync.py Valley Bogaudio  # pre-warm plugin cache
```

Runs locally as an **orchestrator**: it imports `render_client.render()`, so with
`RACK_RENDER_URL` set the ~1GB `data/` stays on your machine while each patch is
rendered on the remote render-service over HTTP. Without `--redo` it skips ids
already in `corpus_audio_analysis.json` (resume); `--ids` targets a specific set
(e.g. re-running only the ids left in a given failure state after a partial run).

- Shares the **same image** as the render-service (`render-service/Dockerfile`)
  — it already bakes lin-x64 Rack + a source-built VCV-Recorder + Fundamental.
  Deploy as a **second Railway service** off that Dockerfile with start command
  `python3 corpus_audition.py` and a volume at `/app/data` (seed with `data/raw/`
  + `data/output/filtered_patches.json`, or run stages 00–03 in-container).
- `plugin_sync.py` downloads any *other* lin-x64 `.vcvplugin` a patch needs from
  `api.vcvrack.com` (public manifests; downloads need `VCV_TOKEN` or
  `VCV_EMAIL`/`VCV_PASSWORD` — Core/Fundamental/VCV-Recorder are already in the
  image). ~90% of the filtered corpus (2544/2818) is fully coverable on lin-x64;
  a patch with an unavailable plugin is recorded `missing-plugins`, and the ~60
  pre-v1 patches (id-less modules, `wires` not `cables`) `old-format` — both
  skipped, not errors (`corpus_audition.is_renderable_format`).
- Results accumulate crash-safe in `data/output/corpus_audio_analysis.json`
  (one write per patch, resumable; `--redo` to re-audition). Renders are
  real-time: N patches ≈ N × (`RENDER_SECONDS` + ~2s startup) of wall clock.
- **`filtered_patches.json` is not v2-only** despite the stage-3 rule — it
  carries v1.1 and pre-v1 patches too; anything that renders/wires patches from
  it must guard for the modern `id`/`cables` shape first.
- **Only self-playing patches make sound headless.** The engine free-runs with
  no MIDI/clock/gate input, so an "instrument" patch waiting for external
  triggers renders silent — not a bug. `data/output/patch_playability.json`
  splits the `ok` renders into `self_playing` (audible: LFO/clock/noise/sequencer
  self-generates) vs `input_driven` (silent, needs external play), keyed off
  `analyze_audio`'s `makes_sound`. This split is **only knowable by rendering** —
  module lists alone predict it at ~66% vs a ~60% base rate (self-play is a
  topological property: is a free-running source cabled to the output through an
  ungated path). `data/output/dns_recovery_buckets.json` is a companion artifact
  segregating any DNS-failed backlog into renderable vs old-format/missing-plugins.
- **Server-side render-fail baseline is high (~83%).** Most failures are
  headless-incompatible plugins that crash/hang Rack before the recorder writes,
  surfacing as `"short WAV got 0"`. A handful of patches also resolve to
  `missing-plugins` *server-side* even when `missing_for_arch` passed them —
  paid plugins (e.g. VCV-Pro) have a lin-x64 manifest entry but can't be
  downloaded without a purchase.

## Pipeline Architecture

**File-based stage graph.** No database, no orchestrator. Each stage reads previous stage outputs from `data/output/` (or `data/whitelist/`, `data/metadata/`, `data/raw/`) and writes new artifacts. Stages are idempotent — safe to re-run.

Cross-stage code reuse is done via `importlib.import_module("03_parse_and_filter")` because module names start with digits (see `06_deep_analysis.py`).

**Output bridges between stages:**

| Producer | Artifact                                                                                                       | Consumers               |
| -------- | -------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 00       | `whitelist/free_plugins.json`, `empty_manifests.json`                                                          | 03, 04, 07              |
| 01       | `metadata/all_patches.json`                                                                                    | 02, 04, 06, 07          |
| 02       | `raw/<id>.vcv`, `raw/manifest.json`                                                                            | 03                      |
| 03       | `output/filtered_patches.json`                                                                                 | 04, 06                  |
| 04       | `output/module_frequency.csv`, `co_occurrence.csv`, `patch_index.json`                                         | 05, downstream analysis |
| 05       | `output/port_registry.json`                                                                                    | 06, 07, 08              |
| 06       | `output/decoded_patches.json`, `connection_patterns.json`, `param_distributions.json`, `analysis_summary.json` | 07, 08, 09, generators  |
| 07       | `output/module_profiles.json`, `patch_notes.json`                                                              | 08                      |
| 08       | `reference/modules/*.md`, `reference/patches/*.md`, `INDEX.md`                                                 | humans, AI              |
| 09       | `reference/archetypes.md`, `voice-patterns.md`, `connection-grammar.md`                                        | humans, AI              |
| 10       | `reference/synthesis-fundamentals.md`, `patch-building-guide.md`, `module-quick-ref.md`                        | humans, AI              |

## Two Sources of Truth for Port IDs

The port registry produced by stage 5 (parsed from C++ source) is **incomplete and sometimes wrong** for ID-to-name mapping. Hand-verified port maps live in two places and must stay in sync:

1. **`10_build_knowledge_base.py` → `PORT_MAPS`** — authoritative for reference docs
2. **`validate_patch.py` → `PORT_COUNTS`** — authoritative port counts for validation
3. **`generate_patches.py` docstring** — cheat sheet used by patch construction code

When adding a new module to the generators, verify port IDs against the C++ enum in the cloned repo at `data/repos/<plugin>/src/` before trusting `port_registry.json`.

## Filtering Rules (Stage 3 — Non-Negotiable)

A patch is included only if **all** are true:

- `platform.slug == "vcv-rack"`
- `like_count >= 3`
- `version` starts with `"2"` (Rack 2.x only)
- `8 <= len(modules) <= 50`
- 100% of modules' `(plugin, model)` pair is in `free_plugins.json`
- No module's plugin slug is in `empty_manifests.json` (unverifiable plugins are rejected, not allowed)

Constants live in `config.py` (`MIN_LIKES`, `MIN_MODULES`, `MAX_MODULES`, `RACK_VERSION`, `RATE_LIMIT_DELAY`).

## .vcv File Format

Two on-disk formats, both parsed by stage 3:

- **Plain JSON** — Rack v0.x/v1, file starts with `{`
- **Zstd-compressed tar** — Rack v2, magic bytes `28 b5 2f fd`, contains `./patch.json`

Inside: `modules[]` (with `plugin`, `model`, `params[]`, `pos[x,y]`) and `cables[]` (with `outputModuleId`, `outputId`, `inputModuleId`, `inputId`). **Params and ports are referenced by index, not name** — name resolution requires the port registry.

## Reference Docs as Frontmatter-Tagged Markdown

`data/reference/` files use YAML frontmatter (`name`, `type`, `tags`, `triggers`, `role`) so they can be discovered by skill-like lookup. When generating new reference docs, preserve the frontmatter convention — `INDEX.md` is auto-generated from these tags.

## Conventions

- Stage scripts are numbered `NN_name.py`. Don't rename or renumber — `06_deep_analysis.py` imports stage 03 by string.
- Network-touching code uses `RATE_LIMIT_DELAY` from `config.py` between requests.
- Stage 2 writes `manifest.json` after **every single file operation** (never batch) for crash-safety. Preserve this when modifying.
- Cloned plugin repos in `data/repos/` are a cache — safe to delete to force re-clone.
- `vcv-corpus-handoff.md` is a stale snapshot from when only stages 0–1 existed; do not treat as current.
