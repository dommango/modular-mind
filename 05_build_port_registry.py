"""Step 5: Build port/param registry by parsing plugin C++ source code.

For each plugin with a GitHub sourceUrl:
  1. Shallow-clone the repo
  2. Scan .cpp/.hpp/.h files for configParam/configInput/configOutput calls
  3. Parse enum definitions to map IDs to names
  4. Output: data/output/port_registry.json

Registry schema:
  {
    "plugin_slug": {
      "model_slug": {
        "params": [{"id": 0, "name": "Frequency", "min": -76.0, "max": 76.0, "default": 0.0, "unit": " Hz"}, ...],
        "inputs": [{"id": 0, "name": "1V/octave pitch"}, ...],
        "outputs": [{"id": 0, "name": "Sine"}, ...]
      }
    }
  }
"""

import csv
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from config import OUTPUT_DIR, WHITELIST_DIR


REGISTRY_PATH = OUTPUT_DIR / "port_registry.json"
CLONE_CACHE = Path(__file__).parent / "data" / "repos"


def get_top_plugins(limit=25):
    freq_path = OUTPUT_DIR / "module_frequency.csv"
    plugin_instances = Counter()
    with open(freq_path) as f:
        for row in csv.DictReader(f):
            plugin_instances[row["plugin"]] += int(row["instance_count"])

    sources = {}
    manifests_dir = WHITELIST_DIR / "raw_manifests"
    for f in manifests_dir.glob("*.json"):
        data = json.loads(f.read_text())
        url = data.get("sourceUrl", "")
        if url and "github.com" in url:
            sources[f.stem] = url.rstrip("/").rstrip(".git")

    ranked = [
        (plugin, count, sources[plugin])
        for plugin, count in plugin_instances.most_common(100)
        if plugin in sources
    ][:limit]

    return ranked


def clone_repo(url, dest):
    if dest.exists():
        return True
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
            check=True, capture_output=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"    Clone failed: {e}")
        return False


def find_source_files(repo_dir):
    files = []
    for ext in ("*.cpp", "*.hpp", "*.h"):
        files.extend(repo_dir.rglob(ext))
    return files


def parse_enums(text):
    enums = {}
    for match in re.finditer(
        r'enum\s+(\w+)\s*\{([^}]+)\}', text, re.DOTALL
    ):
        name = match.group(1)
        body = match.group(2)
        members = []
        idx = 0
        for line in body.split("\n"):
            line = line.strip().split("//")[0].strip().rstrip(",")
            if not line or line.startswith("NUM_") or line.startswith("ENUMS(") or "NUM_" in line:
                continue
            if "=" in line:
                parts = line.split("=")
                line = parts[0].strip()
                try:
                    idx = int(parts[1].strip())
                except ValueError:
                    pass
            if line and re.match(r'^[A-Z_][A-Z_0-9]*$', line):
                members.append((idx, line))
                idx += 1
        if members:
            enums[name] = dict(members)
    return enums


def extract_string_arg(args_str, position):
    parts = []
    depth = 0
    current = ""
    for ch in args_str:
        if ch in "({":
            depth += 1
            current += ch
        elif ch in ")}":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())

    if position < len(parts):
        val = parts[position].strip()
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
    return None


def parse_config_calls(text):
    results = {"params": [], "inputs": [], "outputs": []}

    param_patterns = [
        (r'configParam\w*\(([^;]+?)\)\s*;', "param"),
        (r'configSwitch\(([^;]+?)\)\s*;', "param"),
        (r'configButton\(([^;]+?)\)\s*;', "param"),
        (r'configInput\(([^;]+?)\)\s*;', "input"),
        (r'configOutput\(([^;]+?)\)\s*;', "output"),
    ]

    for pattern, kind in param_patterns:
        for match in re.finditer(pattern, text, re.DOTALL):
            args_str = match.group(1).replace("\n", " ").replace("\t", " ")
            args_str = re.sub(r'\s+', ' ', args_str)

            if kind == "param":
                name = extract_string_arg(args_str, 4)
                if name is None:
                    name = extract_string_arg(args_str, 3)
                unit = extract_string_arg(args_str, 5)
                results["params"].append({
                    "raw_id": args_str.split(",")[0].strip(),
                    "name": name or "",
                    "unit": unit or "",
                })
            else:
                name = extract_string_arg(args_str, 1)
                key = "inputs" if kind == "input" else "outputs"
                results[key].append({
                    "raw_id": args_str.split(",")[0].strip(),
                    "name": name or "",
                })

    return results


def resolve_enum_id(raw_id, enums):
    raw_id = raw_id.strip()
    for enum_name, members in enums.items():
        inv = {v: k for k, v in members.items()}
        if raw_id in inv:
            return inv[raw_id]
        short = raw_id.split("::")[-1] if "::" in raw_id else raw_id
        if short in inv:
            return inv[short]
    return None


def build_struct_to_slug_map(all_text, manifest_modules):
    slug_set = set(manifest_modules)
    struct_to_slug = {}

    for filepath, text in all_text.items():
        for m in re.finditer(
            r'createModel\s*<\s*(\w+)[^(]*\(\s*"([^"]+)"', text
        ):
            struct_name = m.group(1)
            slug = m.group(2)
            if slug in slug_set:
                struct_to_slug[struct_name] = slug

    return struct_to_slug


def find_struct_files(all_text, struct_name):
    files = []
    patterns = [
        rf'struct\s+{re.escape(struct_name)}\b',
        rf'class\s+{re.escape(struct_name)}\b',
    ]
    for filepath, text in all_text.items():
        for pat in patterns:
            if re.search(pat, text):
                files.append(filepath)
                break
    return files


def build_plugin_registry(repo_dir, plugin_slug, manifest_modules):
    source_files = find_source_files(repo_dir)
    all_text = {}
    for f in source_files:
        try:
            all_text[f] = f.read_text(errors="ignore")
        except Exception:
            continue

    combined_text = "\n".join(all_text.values())
    all_enums = parse_enums(combined_text)

    struct_to_slug = build_struct_to_slug_map(all_text, manifest_modules)

    registry = {}

    slug_to_files = {}
    for filepath, text in all_text.items():
        for m in re.finditer(
            r'createModel\s*<\s*(\w+)[^(]*\(\s*"([^"]+)"', text
        ):
            struct_name = m.group(1)
            slug = m.group(2)
            if slug in set(manifest_modules):
                slug_to_files.setdefault(slug, []).append(filepath)
                for sf in find_struct_files(all_text, struct_name):
                    if sf not in slug_to_files[slug]:
                        slug_to_files[slug].append(sf)

    for slug, files in slug_to_files.items():
        merged_text = "\n".join(all_text[f] for f in files if f in all_text)
        if not merged_text:
            continue

        file_enums = parse_enums(merged_text)
        merged_enums = {**all_enums, **file_enums}
        config = parse_config_calls(merged_text)

        if not any(config.values()):
            continue

        for kind in ("params", "inputs", "outputs"):
            for entry in config[kind]:
                resolved_id = resolve_enum_id(entry["raw_id"], merged_enums)
                if resolved_id is not None:
                    entry["id"] = resolved_id
                del entry["raw_id"]

        registry[slug] = config

    # Fallback: filename-based matching for models not found via createModel
    matched_slugs = set(registry.keys())
    unmatched = [s for s in manifest_modules if s not in matched_slugs]

    for filepath, text in all_text.items():
        config = parse_config_calls(text)
        if not any(config.values()):
            continue

        file_enums = parse_enums(text)
        merged_enums = {**all_enums, **file_enums}

        for kind in ("params", "inputs", "outputs"):
            for entry in config[kind]:
                resolved_id = resolve_enum_id(entry["raw_id"], merged_enums)
                if resolved_id is not None:
                    entry["id"] = resolved_id
                del entry["raw_id"]

        fname = filepath.stem.lower()
        for slug in unmatched:
            variants = [
                slug.lower(),
                slug.replace("-", "").lower(),
                slug.split("-")[-1].lower() if "-" in slug else None,
            ]
            for v in variants:
                if v and (v == fname or v in fname):
                    if slug not in registry:
                        registry[slug] = config
                        unmatched = [s for s in unmatched if s != slug]
                    break

    for model, data in registry.items():
        for kind in ("params", "inputs", "outputs"):
            data[kind].sort(key=lambda e: e.get("id", 999))

    registry = {k: v for k, v in registry.items() if any(v.values())}
    return registry


def main():
    CLONE_CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    plugins = get_top_plugins(25)
    print(f"Scraping {len(plugins)} plugin repos...\n")

    manifests_dir = WHITELIST_DIR / "raw_manifests"
    full_registry = {}
    stats = {"cloned": 0, "failed": 0, "models_found": 0, "models_total": 0}

    for plugin_slug, instances, url in plugins:
        manifest_path = manifests_dir / f"{plugin_slug}.json"
        manifest = json.loads(manifest_path.read_text())
        module_slugs = [m["slug"] for m in manifest.get("modules", [])]
        stats["models_total"] += len(module_slugs)

        dest = CLONE_CACHE / plugin_slug
        print(f"  {plugin_slug} ({len(module_slugs)} modules)...", end=" ", flush=True)

        if not clone_repo(url, dest):
            stats["failed"] += 1
            print("SKIP")
            continue

        stats["cloned"] += 1
        plugin_reg = build_plugin_registry(dest, plugin_slug, module_slugs)
        if plugin_reg:
            full_registry[plugin_slug] = plugin_reg
            stats["models_found"] += len(plugin_reg)
            print(f"{len(plugin_reg)}/{len(module_slugs)} models parsed")
        else:
            print("0 models parsed")

    REGISTRY_PATH.write_text(json.dumps(full_registry, indent=2))

    total_params = sum(
        len(model["params"])
        for plugin in full_registry.values()
        for model in plugin.values()
    )
    total_ports = sum(
        len(model["inputs"]) + len(model["outputs"])
        for plugin in full_registry.values()
        for model in plugin.values()
    )

    print(f"\nSummary:")
    print(f"  Repos cloned:    {stats['cloned']}")
    print(f"  Clone failures:  {stats['failed']}")
    print(f"  Models parsed:   {stats['models_found']} / {stats['models_total']}")
    print(f"  Total params:    {total_params}")
    print(f"  Total ports:     {total_ports}")
    print(f"\nWritten to {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
