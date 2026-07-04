# AGENTS.md

This file provides guidance to AI coding agents working in this repository. It is derived from the actual source code, data pipelines, and conventions used in the project. Treat this as the authoritative ground truth — do not rely on stale handoff documents or assumptions.

## Project Overview

This is a multi-stage Python pipeline that builds a corpus of VCV Rack (modular synthesizer) patches and module knowledge, then uses that corpus to generate new `.vcv` patch files. The long-term goal is to train AI to produce structurally valid, musically coherent modular synthesizer patches.

The project has two parallel tracks:
1. **Corpus pipeline (stages 00–10):** Downloads, filters, parses, and analyzes thousands of real community patches from PatchStorage to build frequency tables, co-occurrence matrices, port registries, and reference documentation.
2. **Generation & validation (standalone scripts):** Generates new `.vcv` files from learned patterns and validates them structurally before they are opened in VCV Rack.

All source files are in the project root. There is no package structure — stages are standalone scripts that communicate through files in `data/`.

## Technology Stack

- **Language:** Python 3 (std-lib heavy, minimal external dependencies)
- **Single external dependency:** `requests>=2.31.0` (see `requirements.txt`)
- **System library required:** `libzstd1` (for Rack v2 patch decompression via `ctypes` in stage 03)
- **No build tool:** No `pyproject.toml`, `setup.py`, `package.json`, or `Makefile`. Scripts are run directly with `python3`.
- **No database:** All state is file-based (JSON, CSV, Markdown).
- **No formal test framework:** Validation is performed by `validate_patch.py` doing structural checks on generated `.vcv` files.

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` in the project root with:

```
GITHUB_TOKEN=<your_personal_access_token>
```

Any GitHub PAT works — no scopes are needed. The token is used only for GitHub API rate-limit headroom in stage 00.

Stage 03 also needs the system library `libzstd1`:

```bash
apt install libzstd1
```

## Code Organization

| File | Purpose |
|------|---------|
| `config.py` | Central configuration: paths, rate limits, filtering constants, `.env` loader |
| `00_build_whitelist.py` | Fetch VCV Library manifests from GitHub; build whitelist of free plugins |
| `01_fetch_metadata.py` | Paginate PatchStorage API to fetch patch listing metadata |
| `02_download_patches.py` | Fetch patch details and download `.vcv` files (resumable via `manifest.json`) |
| `03_parse_and_filter.py` | Parse raw `.vcv` files (plain JSON or zstd-compressed tar) and apply strict license/module filters |
| `04_aggregate.py` | Compute `module_frequency.csv`, `co_occurrence.csv`, and `patch_index.json` |
| `05_build_port_registry.py` | Shallow-clone top plugin repos and parse C++ source for param/input/output names |
| `06_deep_analysis.py` | Decode patches using the port registry; build connection patterns, param distributions, and analysis summary |
| `07_build_module_profiles.py` | Cross-reference manifests, GitHub docs, patch notes, and analysis to build per-module profiles |
| `08_generate_reference_files.py` | Write YAML-frontmatter-tagged Markdown reference docs for modules and patches |
| `09_classify_and_learn.py` | Classify decoded patches into archetypes; extract voice patterns and connection grammar docs |
| `10_build_knowledge_base.py` | Generate synthesis fundamentals, patch-building guide, and module quick-reference docs |
| `generate_patches.py` | Standalone generator producing 4 hand-designed `.vcv` patches using verified port maps |
| `generate_batch.py` | Standalone batch generator (batch3) replicating proven wiring templates with randomized params |
| `validate_patch.py` | Structural + signal-flow validator for `.vcv` files: traces audio from oscillator → output, checks gate sources, envelope modulation, port ranges, frequencies |

### Data Directory Layout

```
data/
├── whitelist/          # free_plugins.json, freeware_plugins.json, empty_manifests.json, raw_manifests/
├── metadata/           # all_patches.json, pages/page_NNN.json
├── raw/                # <id>.vcv files, manifest.json (download tracker)
├── output/             # JSON/CSV artifacts produced by stages 03–07
├── repos/              # Shallow-cloned plugin repos (cache, safe to delete)
├── reference/          # Markdown docs with YAML frontmatter
│   ├── modules/        # Per-module reference files
│   ├── patches/        # Per-patch reference files
│   └── *.md            # INDEX.md, archetypes.md, synthesis-fundamentals.md, etc.
└── generated/          # Output directory for .vcv files from generators
```

## Running the Pipeline

All stages are run standalone from the project root. They communicate exclusively through files in `data/`.

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

### Patch Generation & Validation

Separate from the corpus pipeline:

```bash
python3 generate_patches.py                  # writes data/generated/*.vcv
python3 generate_batch.py                    # writes data/generated/batch3/*.vcv
python3 validate_patch.py <file.vcv>         # structural check single file
python3 validate_patch.py data/generated/    # batch validate directory
```

## Pipeline Architecture

**File-based stage graph.** No database, no orchestrator. Each stage reads previous stage outputs from `data/output/` (or `data/whitelist/`, `data/metadata/`, `data/raw/`) and writes new artifacts. Stages are idempotent — safe to re-run.

Cross-stage code reuse is done via `importlib.import_module("03_parse_and_filter")` because module names start with digits (see `06_deep_analysis.py`).

**Output bridges between stages:**

| Producer | Artifact | Consumers |
|----------|----------|-----------|
| 00 | `whitelist/free_plugins.json`, `empty_manifests.json` | 03, 04, 07 |
| 01 | `metadata/all_patches.json` | 02, 04, 06, 07 |
| 02 | `raw/<id>.vcv`, `raw/manifest.json` | 03 |
| 03 | `output/filtered_patches.json` | 04, 06 |
| 04 | `output/module_frequency.csv`, `co_occurrence.csv`, `patch_index.json` | 05, downstream analysis |
| 05 | `output/port_registry.json` | 06, 07, 08 |
| 06 | `output/decoded_patches.json`, `connection_patterns.json`, `param_distributions.json`, `analysis_summary.json` | 07, 08, 09, generators |
| 07 | `output/module_profiles.json`, `patch_notes.json` | 08 |
| 08 | `reference/modules/*.md`, `reference/patches/*.md`, `INDEX.md` | humans, AI |
| 09 | `reference/archetypes.md`, `voice-patterns.md`, `connection-grammar.md` | humans, AI |
| 10 | `reference/synthesis-fundamentals.md`, `patch-building-guide.md`, `module-quick-ref.md` | humans, AI |

## Development Conventions

### Naming and Numbering

- Stage scripts are numbered `NN_name.py`. **Do not rename or renumber** — `06_deep_analysis.py` imports stage 03 by string via `importlib`.
- All network-touching code uses `RATE_LIMIT_DELAY` from `config.py` between requests.
- Stage 2 writes `manifest.json` after **every single file operation** (never batch) for crash-safety. Preserve this behavior when modifying.

### Configuration Constants

All filtering constants live in `config.py`:

| Constant | Value | Meaning |
|----------|-------|---------|
| `RATE_LIMIT_DELAY` | `0.5` | Seconds between API requests |
| `MIN_LIKES` | `3` | Minimum `like_count` for a patch to be included |
| `MIN_MODULES` | `8` | Minimum modules in a patch |
| `MAX_MODULES` | `50` | Maximum modules in a patch |
| `RACK_VERSION` | `"2"` | Major version prefix — Rack 2.x only |

### Filtering Rules (Stage 3 — Non-Negotiable)

A patch is included only if **all** are true:

- `platform.slug == "vcv-rack"`
- `like_count >= 3`
- `version` starts with `"2"` (Rack 2.x only)
- `8 <= len(modules) <= 50`
- 100% of modules' `(plugin, model)` pair is in `free_plugins.json`
- No module's plugin slug is in `empty_manifests.json` (unverifiable plugins are rejected, not allowed)

### .vcv File Format

Two on-disk formats, both parsed by stage 3:

- **Plain JSON** — Rack v0.x/v1, file starts with `{`
- **Zstd-compressed tar** — Rack v2, magic bytes `28 b5 2f fd`, contains `./patch.json`

Inside: `modules[]` (with `plugin`, `model`, `params[]`, `pos[x,y]`) and `cables[]` (with `outputModuleId`, `outputId`, `inputModuleId`, `inputId`). **Params and ports are referenced by index, not name** — name resolution requires the port registry.

### Reference Docs as Frontmatter-Tagged Markdown

`data/reference/` files use YAML frontmatter (`name`, `type`, `tags`, `triggers`, `role`) so they can be discovered by skill-like lookup. When generating new reference docs, preserve the frontmatter convention — `INDEX.md` is auto-generated from these tags.

### Two Sources of Truth for Port IDs

The port registry produced by stage 5 (parsed from C++ source) is **incomplete and sometimes wrong** for ID-to-name mapping. Hand-verified port maps live in three places and must stay in sync:

1. **`10_build_knowledge_base.py` → `PORT_MAPS`** — authoritative for reference docs
2. **`validate_patch.py` → `PORT_COUNTS`** — authoritative port counts for validation
3. **`generate_patches.py` docstring** — cheat sheet used by patch construction code

When adding a new module to the generators, verify port IDs against the C++ enum in the cloned repo at `data/repos/<plugin>/src/` before trusting `port_registry.json`.

### Cache Directories

- `data/repos/` — cloned plugin repos are a cache; safe to delete to force re-clone.
- `data/whitelist/raw_manifests/` — cached VCV Library manifest JSONs; safe to delete to force re-download.
- `data/metadata/pages/` — cached PatchStorage API page responses; safe to delete to force re-fetch.

## Testing Instructions

There is no formal test suite (no `pytest`, `unittest`, or CI configuration). Quality assurance is performed as follows:

1. **Signal-flow validation:** Run `python3 validate_patch.py <file.vcv>` or `python3 validate_patch.py data/generated/` on generated patches. This traces the audio path from VCO/Noise sources through mixers/VCAs/filters to the AudioInterface. It also verifies that ADSR envelopes actually connect to VCA or mixer CV inputs (not just audio inputs).
2. **Stage re-runnability:** Each stage should be idempotent — re-running it should produce the same output given the same inputs.

When modifying a stage, verify it still runs to completion and produces valid JSON/CSV output. Check downstream consumers if the schema changes.

## Security Considerations

- `.env` is listed in `.gitignore` and must never be committed. It contains `GITHUB_TOKEN`.
- The GitHub token needs no scopes — it is used only for unauthenticated-rate-limit headroom.
- Network requests use a hardcoded `User-Agent: modular-mind/1.0` and `RATE_LIMIT_DELAY` to be polite to APIs.
- No user input is executed, shell-injected, or dynamically evaluated beyond parsing JSON and CSV.
- `subprocess` is used only for `git clone` with hardcoded arguments.

## Key Files for Quick Reference

| File | What to know |
|------|--------------|
| `config.py` | All paths and constants; auto-loads `.env` |
| `03_parse_and_filter.py` | `parse_vcv()`, `decompress_zstd()`, `load_whitelist()` — reused by stage 06 |
| `validate_patch.py` | `PatchValidator` class and `PORT_COUNTS` dict — ground truth for port counts |
| `generate_patches.py` | Verified port ID cheat sheet in module docstring |
| `10_build_knowledge_base.py` | `PORT_MAPS` dict — authoritative human-verified port names |

## Notes

- `vcv-corpus-handoff.md` is a stale snapshot from when only stages 0–1 existed; do not treat as current documentation.
- The pipeline is designed to run on WSL2/Ubuntu but should work on any Linux system with Python 3, `requests`, and `libzstd1`.
- Stage 01 targets PatchStorage platform ID `745` (VCV Rack). The API returns mixed platforms despite the filter param — the script filters by `platform_slug == "vcv-rack"` during consolidation.
