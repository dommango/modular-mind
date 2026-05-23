# VCV Rack Corpus Pipeline — Handoff Document

**Project:** `~/projects/vcv-corpus`
**Goal:** Build a corpus of high-quality VCV Rack patches using only free, documented modules — to eventually train an AI to understand what sounds good and generate valid `.vcv` files.
**Runtime:** WSL2/Ubuntu on HP OmniBook X (Snapdragon X Elite)

---

## Project Vision

1. **Phase 1 (current):** Build a frequency map of modules used in well-liked patches — identify the ~50–100 module core vocabulary
2. **Phase 2:** Build a param/port index registry for that vocabulary (runtime introspection in VCV Rack)
3. **Phase 3:** Render patches to audio, pair audio with `.vcv` files, build labeled corpus
4. **Phase 4:** Enable AI to generate structurally valid, musically coherent `.vcv` files

---

## Project Structure

```
~/projects/vcv-corpus/
├── data/
│   ├── whitelist/
│   │   ├── raw_manifests/          # one .json per plugin from GitHub API
│   │   ├── free_plugins.json       # {plugin_slug: [module_slugs]} — free only
│   │   └── empty_manifests.json    # free-licensed plugins with 0 modules (unverifiable)
│   ├── metadata/
│   │   ├── pages/                  # page_001.json ... page_NNN.json (raw API pages)
│   │   └── all_patches.json        # consolidated, filtered to vcv-rack + likes ≥ 3
│   ├── raw/
│   │   ├── <patch_id>.vcv          # downloaded patch files
│   │   └── manifest.json           # per-patch status tracker (see schema below)
│   └── output/
│       ├── parsed_patches.json     # all parsed patches with module lists
│       ├── qualified_patches.json  # strictly filtered subset
│       ├── module_frequency.csv    # ranked module usage counts
│       └── co_occurrence.csv       # module co-occurrence matrix
├── 00_build_whitelist.py
├── 01_fetch_metadata.py
├── 02_download_patches.py
├── 03_parse_and_filter.py
├── 04_aggregate.py
├── config.py
└── requirements.txt
```

---

## config.py — Key Settings

```python
RATE_LIMIT_DELAY = 0.5      # seconds between API requests
MIN_LIKES        = 3        # minimum like_count to include a patch
MIN_MODULES      = 8        # minimum module count in a patch
MAX_MODULES      = 50       # maximum module count in a patch
RACK_VERSION     = "2"      # major version prefix — Rack 2.x only
GITHUB_TOKEN     = "..."    # personal access token, no scopes needed
BASE_DIR         = ...      # all paths derived from this
```

---

## Filtering Rules — Non-Negotiable

These are strict. All conditions must be true for a patch to be included:

| Filter | Rule |
|--------|------|
| Platform | `platform.slug == "vcv-rack"` |
| Engagement | `like_count >= 3` |
| Rack version | `version` field starts with `"2"` |
| Module count | `8 <= len(modules) <= 50` |
| Module whitelist | **100% of modules** must have their plugin slug in `free_plugins.json` |
| Unverifiable plugins | Any module whose plugin slug is in `empty_manifests.json` → **reject patch** |

The whitelist derives from `github.com/VCVRack/library/manifests/`. Free = license is not `"proprietary"` and not `"https://vcvrack.com/eula"`.

---

## Whitelist Stats (Stage 0 — Complete)

- Total plugins in library: **503**
- Free plugins: **363** covering **3,630 modules**
- Proprietary/excluded: **140**
- Free plugins with empty module lists (unverifiable): **26** — stored in `empty_manifests.json`, treated same as proprietary

**Key plugin slugs to know** (these appear frequently in patches):

| What you might see | Actual slug in .vcv files |
|-------------------|--------------------------|
| VCV Free / Fundamental | `Fundamental` |
| Bogaudio | `Bogaudio` |
| Audible Instruments | `AudibleInstruments` |
| Valley | `Valley` |
| Befaco | `Befaco` |
| Surge XT | `SurgeXTRack` |
| Surge (older) | `SurgeRack` |
| Count Modula | `CountModula` |
| Impromptu Modular | `ImpromptuModular` |

---

## Stage 0 — Status: ✅ COMPLETE

`00_build_whitelist.py` ran successfully. Outputs exist:
- `data/whitelist/raw_manifests/` — populated
- `data/whitelist/free_plugins.json` — 363 plugins
- `data/whitelist/empty_manifests.json` — 26 slugs

---

## Stage 1 — Status: ✅ COMPLETE (needs verification)

`01_fetch_metadata.py` ran and completed. **However, the output was not verified before this handoff.** This is the first thing to do in the next session.

### API Details
- Endpoint: `https://patchstorage.com/api/alpha/patches/`
- Params: `platforms=vcv-rack&per_page=100&page=N`
- **Note:** API returns mixed platforms despite the filter param — script filters by `platform.slug == "vcv-rack"` during consolidation
- Platform numeric ID is `745` (discovered during probe)
- Total pages: **167** across **16,677 total patches** (all platforms)
- Estimated VCV patches before likes filter: **~7,700**

### `all_patches.json` Record Schema (expected)
```json
{
  "id": 192270,
  "title": "...",
  "like_count": 5,
  "platform": {"slug": "vcv-rack"},
  "created": "...",
  "updated": "..."
}
```

### ⚠️ FIRST TASK IN NEXT SESSION — Verify Stage 1 output:
Ask Claude Code:
1. Show one sample record from `all_patches.json` with all fields
2. Total patch count in `all_patches.json`
3. Total pages fetched, any errors
4. First 5 lines of `data/metadata/pages/page_001.json` to confirm raw pages saved

**Do not start Stage 2 until this verification is complete.**

---

## Stage 2 — Status: ⏳ NOT STARTED

### What it does
For each patch in `all_patches.json`:
1. Fetch detail endpoint: `GET /api/alpha/patches/{id}` → extract `files[]` array to find `.vcv` download URL
2. Download the `.vcv` file to `data/raw/<id>.vcv`
3. Update `manifest.json` after **each individual file** — never batch write

### manifest.json Schema
```json
{
  "192270": {
    "detail_fetched": false,
    "status": "pending",
    "filename": null,
    "reason": null
  }
}
```

Status values:
- `"pending"` — not yet attempted
- `"downloaded"` — `.vcv` file successfully saved
- `"failed"` — HTTP error or bad response (log `reason`)
- `"skipped"` — not a `.vcv` file (zip, bin, etc.)

### Resumability
- On start: load existing `manifest.json`, skip anything not `"pending"`
- `detail_fetched: false` + `status: "pending"` → fetch detail then download
- `detail_fetched: true` + `status: "pending"` → detail already known, just download
- Write manifest after **every single file operation** — not in batches

### Rate limiting
- Use `RATE_LIMIT_DELAY` from config between every request
- Two requests per patch (detail fetch + file download) = ~2× the time
- Estimated runtime at 0.5s delay: **2–3 hours** for ~7,700 patches
- This stage should be started and left to run — do not interrupt if possible

---

## Stage 3 — Status: ⏳ NOT STARTED

### What it does
Parse every `"downloaded"` `.vcv` file, apply all strict filters, output qualified corpus.

### `.vcv` File Structure (JSON)
```json
{
  "version": "2.4.0",
  "modules": [
    {
      "id": 1,
      "plugin": "Bogaudio",
      "model": "Bogaudio-VCO",
      "params": [...],
      "pos": [x, y]
    }
  ],
  "cables": [
    {
      "id": 1,
      "outputModuleId": 1,
      "outputId": 0,
      "inputModuleId": 2,
      "inputId": 0
    }
  ]
}
```

**Key facts:**
- `plugin` field = plugin slug (matches `free_plugins.json` keys)
- `model` field = module slug (matches values in `free_plugins.json` arrays)
- `params` are stored by index, not name — index order = declaration order in source code
- Cable ports are also by index — no human-readable names in the file

### Filter logic in `03_parse_and_filter.py`
```python
def load_whitelist():
    # loads free_plugins.json AND empty_manifests.json
    # returns (allowed_set, unverifiable_set)
    # allowed_set = {(plugin_slug, module_slug), ...}
    # unverifiable_set = {plugin_slug, ...}

def is_module_allowed(plugin, model, allowed_set, unverifiable_set):
    if plugin in unverifiable_set:
        return False   # empty manifest — can't verify
    if (plugin, model) not in allowed_set:
        return False   # proprietary or unknown
    return True
```

### Outputs
- `parsed_patches.json` — all successfully parsed patches with module lists
- `qualified_patches.json` — patches passing all strict filters

---

## Stage 4 — Status: ⏳ NOT STARTED

### What it does
Reads `qualified_patches.json`, computes:
- `module_frequency.csv` — plugin, model, patch_count, pct_of_patches (sorted descending)
- `co_occurrence.csv` — plugin_a, model_a, plugin_b, model_b, co_occurrence_count

### Why co-occurrence matters
The frequency table tells you what's popular. The co-occurrence matrix tells you what gets used *together* — which is the real signal for understanding patch architecture patterns. Both are needed for Phase 2.

---

## Key Design Decisions (don't relitigate these)

- **Strict 100% free module filter** — no exceptions, no lenient mode
- **Staged pipeline** — each stage writes to disk, fully resumable independently
- **Manifest written after every file** — never batch, crash-safe
- **Rack 2.x only** — version 1 patches use different slugs in some cases
- **8–50 module range** — excludes trivial patches and overwhelming mega-patches
- **Likes ≥ 3** — weak but real community quality signal
- **No zip extraction** — only direct `.vcv` files, skip everything else
- **Co-occurrence collected now** — cheap to compute alongside frequency, invaluable later

---

## Long-Term Context

This pipeline is Phase 1 of a larger research project:

**The ultimate goal** is for an AI (Claude) to generate valid, musical `.vcv` patch files — understanding not just structure but what combinations of modules and parameter settings produce sounds that humans find interesting.

**Why free modules only:** So the corpus is reproducible — anyone can install the plugins and open the patches. Also ensures good documentation exists for Phase 2 param mapping.

**Phase 2 (after this):** Runtime introspection — load each module in VCV Rack, read `paramQuantities` to build a param name/index/range registry for the core vocabulary identified in Phase 1.

**Phase 3 (after that):** Render qualified patches to audio. Pair `.vcv` files with rendered audio. This is where the "sounds good" label problem becomes real — will need a strategy for audio quality annotation.

---

## Useful Reference

### Patchstorage API
- Base: `https://patchstorage.com/api/alpha/`
- List: `GET /patches/?platforms=vcv-rack&per_page=100&page=N`
- Detail: `GET /patches/{id}`
- Download URL lives in `files[]` array on the detail endpoint only

### VCV Library GitHub
- Manifests: `https://api.github.com/repos/VCVRack/library/contents/manifests`
- Each manifest: `https://raw.githubusercontent.com/VCVRack/library/master/manifests/{Slug}.json`
- License field: `"proprietary"` or `"https://vcvrack.com/eula"` = paid; anything else = free

### Patchstorage Scale
- ~8,730 VCV Rack entries total on site
- ~16,677 across all platforms in API
- ~7,700 estimated VCV patches after platform filter, before likes filter
- Final qualified corpus size: unknown until Stage 3 completes — expect significant reduction

---

*Handoff generated from conversation with Claude (claude.ai). Continue in a new chat or Claude Code session — all state lives on disk at `~/projects/vcv-corpus/data/`.*
