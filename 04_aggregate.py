"""Step 4: Aggregate corpus into analysis-ready outputs.

Outputs:
  - module_frequency.csv  — how often each module appears across patches
  - co_occurrence.csv     — which modules tend to appear together (sparse)
  - patch_index.json      — enriched per-patch records with metadata joined
"""

import csv
import json
import sys
from collections import Counter
from itertools import combinations

from config import OUTPUT_DIR, METADATA_DIR, WHITELIST_DIR


def load_inputs():
    patches = json.loads(
        (OUTPUT_DIR / "filtered_patches.json").read_text()
    )
    meta_list = json.loads(
        (METADATA_DIR / "all_patches.json").read_text()
    )
    meta_by_id = {p["id"]: p for p in meta_list}
    free_plugins = json.loads(
        (WHITELIST_DIR / "free_plugins.json").read_text()
    )
    freeware_plugins = json.loads(
        (WHITELIST_DIR / "freeware_plugins.json").read_text()
    )
    return patches, meta_by_id, free_plugins, freeware_plugins


def filter_by_version(patches, major):
    return [
        p for p in patches
        if p.get("version", "").split(".")[0] == major
    ]


def classify_module(plugin, model, free_plugins, freeware_plugins):
    if plugin in free_plugins and model in free_plugins[plugin]:
        return "open_source"
    if plugin in freeware_plugins and model in freeware_plugins[plugin]:
        return "freeware"
    return "unknown"


def build_module_frequency(patches):
    patch_counts = Counter()
    instance_counts = Counter()

    for patch in patches:
        seen = set()
        for m in patch["modules"]:
            key = (m["plugin"], m["model"])
            instance_counts[key] += 1
            if key not in seen:
                seen.add(key)
                patch_counts[key] += 1

    total = len(patches)
    rows = []
    for key, pc in patch_counts.most_common():
        plugin, model = key
        rows.append({
            "plugin": plugin,
            "model": model,
            "patch_count": pc,
            "instance_count": instance_counts[key],
            "pct_patches": round(pc / total * 100, 2),
        })

    return rows


def build_co_occurrence(patches):
    pair_counts = Counter()

    for patch in patches:
        unique = {f"{m['plugin']}:{m['model']}" for m in patch["modules"]}
        for pair in combinations(sorted(unique), 2):
            pair_counts[pair] += 1

    rows = sorted(
        [{"module_a": a, "module_b": b, "count": c} for (a, b), c in pair_counts.items()],
        key=lambda r: (-r["count"], r["module_a"], r["module_b"]),
    )
    return rows


def build_patch_index(patches, meta_by_id, free_plugins, freeware_plugins):
    index = []
    for patch in patches:
        meta = meta_by_id.get(patch["id"], {})
        modules = [
            {
                "plugin": m["plugin"],
                "model": m["model"],
                "license_tier": classify_module(
                    m["plugin"], m["model"], free_plugins, freeware_plugins,
                ),
            }
            for m in patch["modules"]
        ]
        index.append({
            "id": patch["id"],
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "like_count": meta.get("like_count", 0),
            "download_count": meta.get("download_count", 0),
            "categories": meta.get("categories", []),
            "tags": meta.get("tags", []),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "license_tier": patch["license_tier"],
            "version": patch["version"],
            "module_count": patch["module_count"],
            "modules": modules,
        })
    return index


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    rack_version = None
    if "--rack-version" in sys.argv:
        idx = sys.argv.index("--rack-version")
        rack_version = sys.argv[idx + 1]

    patches, meta_by_id, free_plugins, freeware_plugins = load_inputs()
    print(f"Loaded {len(patches)} filtered patches")

    if rack_version:
        patches = filter_by_version(patches, rack_version)
        print(f"Filtered to Rack v{rack_version}: {len(patches)} patches")

    freq_rows = build_module_frequency(patches)
    co_rows = build_co_occurrence(patches)
    index = build_patch_index(patches, meta_by_id, free_plugins, freeware_plugins)

    freq_path = OUTPUT_DIR / "module_frequency.csv"
    write_csv(freq_path, freq_rows, ["plugin", "model", "patch_count", "instance_count", "pct_patches"])

    co_path = OUTPUT_DIR / "co_occurrence.csv"
    write_csv(co_path, co_rows, ["module_a", "module_b", "count"])

    index_path = OUTPUT_DIR / "patch_index.json"
    index_path.write_text(json.dumps(index, indent=2))

    total_instances = sum(r["instance_count"] for r in freq_rows)

    print(f"\nSummary:")
    print(f"  Patches:             {len(patches)}")
    print(f"  Rack version filter: {rack_version or 'all'}")
    print(f"  Unique modules:      {len(freq_rows)}")
    print(f"  Module instances:    {total_instances}")
    print(f"  Co-occurrence pairs: {len(co_rows)}")
    print(f"\nWritten:")
    print(f"  {freq_path}")
    print(f"  {co_path}")
    print(f"  {index_path}")


if __name__ == "__main__":
    main()
