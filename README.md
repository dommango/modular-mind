# modular-mind

A multi-stage Python pipeline that builds a corpus of VCV Rack (modular synthesizer) patches and module knowledge, then generates new `.vcv` patch files from learned patterns.

## Quick Start

```bash
pip install -r requirements.txt
apt install libzstd1
```

Create `.env`:
```
GITHUB_TOKEN=<your_github_pat>
```

## Pipeline

Run stages sequentially. Each stage reads files from `data/` produced by previous stages.

```bash
python3 00_build_whitelist.py        # ~30s,  needs network + GITHUB_TOKEN
python3 01_fetch_metadata.py         # ~2min, needs network
python3 02_download_patches.py       # ~1hr,  needs network, RESUMABLE
python3 03_parse_and_filter.py       # seconds
python3 04_aggregate.py              # seconds
python3 05_build_port_registry.py    # ~5min, clones ~25 repos
python3 06_deep_analysis.py          # seconds
python3 07_build_module_profiles.py  # ~2min, needs network
python3 08_generate_reference_files.py  # seconds
python3 09_classify_and_learn.py     # seconds
python3 10_build_knowledge_base.py   # seconds
```

## Patch Generation

```bash
python3 generate_patches.py                  # 4 hand-designed patches
python3 generate_batch.py                    # 20 corpus-derived patches (batch3)
python3 validate_patch.py data/generated/    # verify signal flow
```

## What's in the Box

- **3,530** downloaded community patches from PatchStorage
- **2,818** patches passing strict filtering (100% free modules, Rack 2.x)
- **269** module reference docs with YAML frontmatter
- **137** decoded patch analyses with archetype classification
- **Signal-flow validator** — traces audio from oscillator to output without opening VCV Rack

## License

This project code is unlicensed / provided as-is. Generated `.vcv` files are independent works. Community patches remain property of their original authors.
