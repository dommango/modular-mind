"""Step 6: Deep analysis of fully mapped patches.

Decodes raw .vcv files using the port registry to produce:
  - decoded_patches.json    — named params + named connections per patch
  - connection_patterns.json — aggregated wiring patterns
  - param_distributions.json — knob value distributions
  - analysis_summary.json   — high-level insights
"""

import importlib
import json
import math
from collections import Counter, defaultdict

from config import OUTPUT_DIR, RAW_DIR, METADATA_DIR

parse_filter = importlib.import_module("03_parse_and_filter")
parse_vcv = parse_filter.parse_vcv


def load_inputs():
    patches = json.loads((OUTPUT_DIR / "filtered_patches.json").read_text())
    meta_list = json.loads((METADATA_DIR / "all_patches.json").read_text())
    meta_by_id = {p["id"]: p for p in meta_list}
    registry = json.loads((OUTPUT_DIR / "port_registry.json").read_text())
    return patches, meta_by_id, registry


def identify_fully_mapped(patches, registry):
    registered = set()
    for plugin, models in registry.items():
        for model in models:
            registered.add((plugin, model))

    return [
        p for p in patches
        if all((m["plugin"], m["model"]) in registered for m in p["modules"])
    ]


def build_port_lookup(registry):
    lookup = {}
    for plugin, models in registry.items():
        for model, data in models.items():
            entry = {"params": {}, "inputs": {}, "outputs": {}}
            for kind in ("params", "inputs", "outputs"):
                for i, item in enumerate(data.get(kind, [])):
                    pid = item.get("id", i)
                    name = item.get("name", "") or f"{kind[:-1]}_{pid}"
                    if kind == "params":
                        entry[kind][pid] = {
                            "name": name,
                            "unit": item.get("unit", ""),
                        }
                    else:
                        entry[kind][pid] = name
            lookup[(plugin, model)] = entry
    return lookup


def normalize_cables(patch_data):
    raw = patch_data.get("cables", patch_data.get("wires", []))
    cables = []
    for c in raw:
        cables.append({
            "outputModuleId": c.get("outputModuleId"),
            "outputId": c.get("outputId"),
            "inputModuleId": c.get("inputModuleId"),
            "inputId": c.get("inputId"),
        })
    return cables


def decode_patch(patch_id, meta, patch_data, lookup):
    raw_modules = patch_data.get("modules", [])

    has_id = any("id" in m for m in raw_modules)
    id_to_info = {}
    decoded_modules = []

    for i, m in enumerate(raw_modules):
        mid = m.get("id", i) if has_id else i
        plugin = m.get("plugin", "")
        model = m.get("model", "")
        pos = m.get("pos", m.get("position", [0, 0]))

        id_to_info[mid] = (plugin, model)

        port_entry = lookup.get((plugin, model), {"params": {}, "inputs": {}, "outputs": {}})
        raw_params = m.get("params", [])

        named_params = {}
        for idx, p in enumerate(raw_params):
            if isinstance(p, (int, float)):
                pid = idx
                value = float(p)
            else:
                pid = p.get("id", p.get("paramId"))
                if pid is None:
                    continue
                value = p.get("value", 0.0)
            param_info = port_entry["params"].get(pid)
            if param_info:
                named_params[param_info["name"]] = value
            else:
                named_params[f"param_{pid}"] = value

        decoded_modules.append({
            "instance_id": mid,
            "plugin": plugin,
            "model": model,
            "position": pos,
            "params": named_params,
        })

    cables = normalize_cables(patch_data)
    connections = []
    for c in cables:
        out_mid = c["outputModuleId"]
        in_mid = c["inputModuleId"]
        out_info = id_to_info.get(out_mid)
        in_info = id_to_info.get(in_mid)
        if not out_info or not in_info:
            continue

        out_entry = lookup.get(out_info, {"params": {}, "inputs": {}, "outputs": {}})
        in_entry = lookup.get(in_info, {"params": {}, "inputs": {}, "outputs": {}})

        from_port = out_entry["outputs"].get(c["outputId"], f"output_{c['outputId']}")
        to_port = in_entry["inputs"].get(c["inputId"], f"input_{c['inputId']}")

        connections.append({
            "from_module": out_mid,
            "from_plugin": out_info[0],
            "from_model": out_info[1],
            "from_port": from_port,
            "to_module": in_mid,
            "to_plugin": in_info[0],
            "to_model": in_info[1],
            "to_port": to_port,
        })

    mc = len(decoded_modules)
    cc = len(connections)

    return {
        "id": patch_id,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "like_count": meta.get("like_count", 0),
        "download_count": meta.get("download_count", 0),
        "categories": meta.get("categories", []),
        "tags": meta.get("tags", []),
        "version": patch_data.get("version", ""),
        "modules": decoded_modules,
        "connections": connections,
        "stats": {
            "module_count": mc,
            "cable_count": cc,
            "cables_per_module": round(cc / mc, 2) if mc > 0 else 0,
        },
    }


def build_decoded_patches(fully_mapped, meta_by_id, lookup):
    decoded = []
    errors = 0
    for p in fully_mapped:
        pid = p["id"]
        vcv_path = RAW_DIR / f"{pid}.vcv"
        if not vcv_path.exists():
            errors += 1
            continue
        try:
            patch_data = parse_vcv(vcv_path)
            meta = meta_by_id.get(pid, {})
            decoded.append(decode_patch(pid, meta, patch_data, lookup))
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  Decode error {pid}: {e}")
    return decoded, errors


def build_connection_patterns(decoded_patches):
    pair_counts = Counter()
    for patch in decoded_patches:
        for c in patch["connections"]:
            from_key = f"{c['from_plugin']}:{c['from_model']}:{c['from_port']}"
            to_key = f"{c['to_plugin']}:{c['to_model']}:{c['to_port']}"
            pair_counts[(from_key, to_key)] += 1

    port_pairs = [
        {"from": f, "to": t, "count": c}
        for (f, t), c in pair_counts.most_common(200)
    ]

    chain_counts = Counter()
    for patch in decoded_patches:
        adj = defaultdict(list)
        for c in patch["connections"]:
            adj[c["from_module"]].append(c)

        in_modules = {c["to_module"] for c in patch["connections"]}
        out_modules = {c["from_module"] for c in patch["connections"]}
        sources = out_modules - in_modules

        if not sources:
            sources = out_modules

        for start in sources:
            _trace_chains(start, adj, patch, [], set(), chain_counts, max_len=6)

    common_chains = [
        {"chain": list(chain), "count": count}
        for chain, count in chain_counts.most_common(50)
        if count >= 2 and len(chain) >= 3
    ]

    return {"port_pairs": port_pairs, "common_chains": common_chains}


def _trace_chains(module_id, adj, patch, current_chain, visited, chain_counts, max_len):
    if module_id in visited or len(current_chain) >= max_len:
        if len(current_chain) >= 3:
            chain_counts[tuple(current_chain)] += 1
        return

    visited.add(module_id)
    nexts = adj.get(module_id, [])

    if not nexts:
        if len(current_chain) >= 3:
            chain_counts[tuple(current_chain)] += 1
        visited.discard(module_id)
        return

    for c in nexts:
        hop = f"{c['from_model']}:{c['from_port']}->{c['to_model']}:{c['to_port']}"
        _trace_chains(
            c["to_module"], adj, patch,
            current_chain + [hop], visited, chain_counts, max_len,
        )

    visited.discard(module_id)


def compute_histogram(values, n_bins=20):
    if not values:
        return {"bins": [], "counts": []}
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return {"bins": [lo, lo], "counts": [len(values)]}

    step = (hi - lo) / n_bins
    bins = [lo + i * step for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for v in values:
        idx = min(int((v - lo) / step), n_bins - 1)
        counts[idx] += 1
    return {"bins": [round(b, 6) for b in bins], "counts": counts}


def build_param_distributions(decoded_patches):
    groups = defaultdict(list)
    for patch in decoded_patches:
        for mod in patch["modules"]:
            prefix = f"{mod['plugin']}:{mod['model']}"
            for name, value in mod["params"].items():
                groups[f"{prefix}:{name}"].append(value)

    distributions = {}
    for key, values in sorted(groups.items()):
        n = len(values)
        sorted_vals = sorted(values)
        mean = sum(values) / n
        median = sorted_vals[n // 2]
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)

        distributions[key] = {
            "count": n,
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "mean": round(mean, 6),
            "median": round(median, 6),
            "std": round(std, 6),
            "histogram": compute_histogram(values),
        }

    return distributions


def build_analysis_summary(decoded_patches, patterns, distributions):
    top_connections = patterns["port_pairs"][:20]

    most_tweaked = sorted(
        [
            {"param": k, "std": v["std"], "count": v["count"], "mean": v["mean"]}
            for k, v in distributions.items()
            if v["count"] >= 3
        ],
        key=lambda x: -x["std"],
    )[:20]

    out_degree = Counter()
    in_degree = Counter()
    for patch in decoded_patches:
        for c in patch["connections"]:
            out_degree[(c["from_plugin"], c["from_model"])] += 1
            in_degree[(c["to_plugin"], c["to_model"])] += 1

    all_modules = set(out_degree.keys()) | set(in_degree.keys())
    module_roles = {}
    for key in all_modules:
        od = out_degree.get(key, 0)
        iid = in_degree.get(key, 0)
        total = od + iid
        if total < 3:
            role = "utility"
        else:
            ratio = od / total
            if ratio > 0.65:
                role = "source"
            elif ratio < 0.35:
                role = "output"
            else:
                role = "processor"
        module_roles[f"{key[0]}:{key[1]}"] = {
            "role": role,
            "out_connections": od,
            "in_connections": iid,
        }

    mc = [p["stats"]["module_count"] for p in decoded_patches]
    cc = [p["stats"]["cable_count"] for p in decoded_patches]
    cpm = [p["stats"]["cables_per_module"] for p in decoded_patches]

    def stats(vals):
        s = sorted(vals)
        n = len(s)
        return {
            "min": s[0],
            "max": s[-1],
            "mean": round(sum(s) / n, 2),
            "median": s[n // 2],
            "p25": s[n // 4],
            "p75": s[3 * n // 4],
        }

    author_patches = defaultdict(list)
    for p in decoded_patches:
        author_patches[p["author"]].append(p)

    module_global_rate = Counter()
    for p in decoded_patches:
        seen = set()
        for m in p["modules"]:
            key = f"{m['plugin']}:{m['model']}"
            if key not in seen:
                seen.add(key)
                module_global_rate[key] += 1

    total_patches = len(decoded_patches)
    author_signatures = {}
    for author, patches in author_patches.items():
        if len(patches) < 3:
            continue
        author_module_rate = Counter()
        for p in patches:
            seen = set()
            for m in p["modules"]:
                key = f"{m['plugin']}:{m['model']}"
                if key not in seen:
                    seen.add(key)
                    author_module_rate[key] += 1

        sigs = []
        for mod, count in author_module_rate.items():
            author_rate = count / len(patches)
            global_rate = module_global_rate[mod] / total_patches
            if author_rate >= 0.5 and global_rate < 0.2:
                sigs.append({
                    "module": mod,
                    "author_rate": round(author_rate, 2),
                    "global_rate": round(global_rate, 2),
                })
        if sigs:
            author_signatures[author] = sorted(sigs, key=lambda x: -x["author_rate"])

    return {
        "top_connection_patterns": top_connections,
        "most_tweaked_params": most_tweaked,
        "module_roles": dict(sorted(
            module_roles.items(),
            key=lambda x: -(x[1]["out_connections"] + x[1]["in_connections"]),
        )),
        "patch_complexity": {
            "module_count": stats(mc),
            "cable_count": stats(cc),
            "cables_per_module": stats(cpm),
        },
        "author_signatures": author_signatures,
    }


def main():
    patches, meta_by_id, registry = load_inputs()
    print(f"Loaded {len(patches)} filtered patches")

    fully_mapped = identify_fully_mapped(patches, registry)
    print(f"Fully mapped patches: {len(fully_mapped)}")

    lookup = build_port_lookup(registry)

    print("Decoding patches...")
    decoded, errors = build_decoded_patches(fully_mapped, meta_by_id, lookup)
    print(f"  Decoded: {len(decoded)}, errors: {errors}")

    decoded_path = OUTPUT_DIR / "decoded_patches.json"
    decoded_path.write_text(json.dumps(decoded, indent=2))
    print(f"  Written to {decoded_path}")

    print("Analyzing connection patterns...")
    patterns = build_connection_patterns(decoded)
    patterns_path = OUTPUT_DIR / "connection_patterns.json"
    patterns_path.write_text(json.dumps(patterns, indent=2))
    print(f"  {len(patterns['port_pairs'])} port pairs, {len(patterns['common_chains'])} chains")

    print("Computing param distributions...")
    distributions = build_param_distributions(decoded)
    dist_path = OUTPUT_DIR / "param_distributions.json"
    dist_path.write_text(json.dumps(distributions, indent=2))
    print(f"  {len(distributions)} param distributions")

    print("Building analysis summary...")
    summary = build_analysis_summary(decoded, patterns, distributions)
    summary_path = OUTPUT_DIR / "analysis_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\nSummary:")
    print(f"  Decoded patches:     {len(decoded)}")
    print(f"  Connection patterns: {len(patterns['port_pairs'])}")
    print(f"  Signal chains:       {len(patterns['common_chains'])}")
    print(f"  Param distributions: {len(distributions)}")
    roles = Counter(v["role"] for v in summary["module_roles"].values())
    print(f"  Module roles:        {dict(roles)}")
    print(f"\nWritten to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
