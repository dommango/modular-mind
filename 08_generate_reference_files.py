"""Step 8: Generate per-module and per-patch markdown reference files.

Creates structured markdown files with frontmatter for progressive disclosure:
  - data/reference/modules/<plugin>--<model>.md  (269 files)
  - data/reference/patches/<id>.md               (137 files)

Frontmatter enables skill-like lookup by trigger words, tags, and role.
"""

import json
import re
from pathlib import Path

from config import OUTPUT_DIR

MODULES_DIR = OUTPUT_DIR.parent / "reference" / "modules"
PATCHES_DIR = OUTPUT_DIR.parent / "reference" / "patches"


def load_all():
    profiles = json.loads((OUTPUT_DIR / "module_profiles.json").read_text())
    decoded = json.loads((OUTPUT_DIR / "decoded_patches.json").read_text())
    notes = json.loads((OUTPUT_DIR / "patch_notes.json").read_text())
    patterns = json.loads((OUTPUT_DIR / "connection_patterns.json").read_text())
    distributions = json.loads((OUTPUT_DIR / "param_distributions.json").read_text())
    summary = json.loads((OUTPUT_DIR / "analysis_summary.json").read_text())
    return profiles, decoded, notes, patterns, distributions, summary


def slugify(text):
    return re.sub(r'[^a-z0-9-]', '-', text.lower()).strip('-')


def format_param_stats(key, distributions):
    d = distributions.get(key)
    if not d:
        return ""
    return f"mean={d['mean']:.2f}, median={d['median']:.2f}, std={d['std']:.2f}, range=[{d['min']:.2f}, {d['max']:.2f}], n={d['count']}"


def get_module_connections(plugin, model, patterns):
    key = f"{plugin}:{model}"
    outgoing = []
    incoming = []
    for p in patterns["port_pairs"]:
        if p["from"].startswith(key + ":"):
            port = p["from"].split(":", 2)[2]
            target = p["to"]
            outgoing.append((port, target, p["count"]))
        if p["to"].startswith(key + ":"):
            port = p["to"].split(":", 2)[2]
            source = p["from"]
            incoming.append((port, source, p["count"]))
    return outgoing, incoming


def get_patches_using_module(plugin, model, decoded):
    results = []
    for p in decoded:
        if any(m["plugin"] == plugin and m["model"] == model for m in p["modules"]):
            results.append(p)
    return sorted(results, key=lambda x: -x["like_count"])


def generate_module_file(key, profile, decoded, patterns, distributions):
    plugin = profile["plugin"]
    model = profile["model"]
    tags = profile["tags"]
    role = profile["role"]
    desc = profile["description"]

    trigger_words = [plugin.lower(), model.lower(), desc.lower()[:40]]
    trigger_words.extend(t.lower() for t in tags)
    if role:
        trigger_words.append(role)
    trigger_words = list(dict.fromkeys(t for t in trigger_words if t))

    lines = [
        "---",
        f"name: {plugin} {model}",
        f"slug: {plugin}--{model}",
        f"plugin: {plugin}",
        f"model: {model}",
        f"type: module",
        f"role: {role or 'unknown'}",
        f"tags: [{', '.join(tags)}]",
        f"triggers: [{', '.join(trigger_words)}]",
        f"instances: {profile['instance_count']}",
        "---",
        "",
        f"# {plugin}: {model}",
        "",
    ]

    if desc:
        lines.append(f"> {desc}")
        lines.append("")

    lines.append(f"**Role:** {role or 'unknown'} | **Tags:** {', '.join(tags) or 'none'} | **Instances in corpus:** {profile['instance_count']}")
    if profile["manual_url"]:
        lines.append(f"**Manual:** {profile['manual_url']}")
    lines.append("")

    if profile["params"] or profile["inputs"] or profile["outputs"]:
        lines.append("## Interface")
        lines.append("")
        if profile["params"]:
            lines.append("### Parameters")
            lines.append("")
            for param in profile["params"]:
                stats_key = f"{plugin}:{model}:{param}"
                stats = format_param_stats(stats_key, distributions)
                if stats:
                    lines.append(f"- **{param}** — {stats}")
                else:
                    lines.append(f"- **{param}**")
            lines.append("")
        if profile["inputs"]:
            lines.append("### Inputs")
            lines.append("")
            for inp in profile["inputs"]:
                lines.append(f"- {inp}")
            lines.append("")
        if profile["outputs"]:
            lines.append("### Outputs")
            lines.append("")
            for out in profile["outputs"]:
                lines.append(f"- {out}")
            lines.append("")

    outgoing, incoming = get_module_connections(plugin, model, patterns)
    if outgoing or incoming:
        lines.append("## Connection Patterns")
        lines.append("")
        if outgoing:
            lines.append("### Common outputs (this module sends to)")
            lines.append("")
            for port, target, count in sorted(outgoing, key=lambda x: -x[2])[:10]:
                lines.append(f"- **{port}** → {target} ({count}x)")
            lines.append("")
        if incoming:
            lines.append("### Common inputs (this module receives from)")
            lines.append("")
            for port, source, count in sorted(incoming, key=lambda x: -x[2])[:10]:
                lines.append(f"- **{port}** ← {source} ({count}x)")
            lines.append("")

    patches = get_patches_using_module(plugin, model, decoded)
    if patches:
        lines.append(f"## Patches Using This Module ({len(patches)})")
        lines.append("")
        for p in patches[:15]:
            likes = p["like_count"]
            lines.append(f"- [{p['title']}](../patches/{p['id']}.md) by {p['author']} ({likes} likes, {p['stats']['module_count']} modules)")
        if len(patches) > 15:
            lines.append(f"- ... and {len(patches) - 15} more")
        lines.append("")

    if profile["documentation"]:
        lines.append("## Documentation")
        lines.append("")
        lines.append(profile["documentation"])
        lines.append("")

    return "\n".join(lines)


def generate_patch_file(patch, notes, profiles, patterns):
    pid = patch["id"]
    note = notes.get(str(pid), {})

    tag_list = patch.get("tags", [])
    cat_list = patch.get("categories", [])
    plugin_set = sorted({m["plugin"] for m in patch["modules"]})

    trigger_words = [patch["title"].lower(), patch["author"].lower()]
    trigger_words.extend(t.lower() for t in tag_list)
    trigger_words.extend(c.lower() for c in cat_list)
    trigger_words = list(dict.fromkeys(t for t in trigger_words if t))

    lines = [
        "---",
        f"name: \"{patch['title']}\"",
        f"id: {pid}",
        f"type: patch",
        f"author: {patch['author']}",
        f"likes: {patch['like_count']}",
        f"downloads: {patch.get('download_count', 0)}",
        f"version: {patch['version']}",
        f"license_tier: {patch.get('license_tier', '')}",
        f"categories: [{', '.join(cat_list)}]",
        f"tags: [{', '.join(tag_list)}]",
        f"triggers: [{', '.join(trigger_words)}]",
        f"module_count: {patch['stats']['module_count']}",
        f"cable_count: {patch['stats']['cable_count']}",
        f"plugins: [{', '.join(plugin_set)}]",
        "---",
        "",
        f"# {patch['title']}",
        "",
        f"**Author:** {patch['author']} | **Likes:** {patch['like_count']} | **Downloads:** {patch.get('download_count', 0)}",
        f"**Version:** {patch['version']} | **Categories:** {', '.join(cat_list)} | **Tags:** {', '.join(tag_list)}",
        f"**Modules:** {patch['stats']['module_count']} | **Cables:** {patch['stats']['cable_count']} | **Cables/module:** {patch['stats']['cables_per_module']}",
        "",
    ]

    if note.get("content"):
        lines.append("## Author Notes")
        lines.append("")
        lines.append(note["content"])
        lines.append("")

    if note.get("preview_url"):
        lines.append(f"**Audio preview:** {note['preview_url']}")
        lines.append("")

    lines.append("## Modules")
    lines.append("")
    seen = {}
    for m in patch["modules"]:
        key = f"{m['plugin']}:{m['model']}"
        seen[key] = seen.get(key, 0) + 1

    for key, count in sorted(seen.items(), key=lambda x: -x[1]):
        plugin, model = key.split(":", 1)
        profile = profiles.get(key, {})
        role = profile.get("role", "")
        desc = profile.get("description", "")
        suffix = f" ×{count}" if count > 1 else ""
        role_badge = f" [{role}]" if role else ""
        lines.append(f"- [{key}](../modules/{plugin}--{model}.md){suffix}{role_badge} — {desc}")
    lines.append("")

    lines.append("## Signal Flow")
    lines.append("")
    lines.append("### Connections")
    lines.append("")

    module_id_to_name = {}
    for m in patch["modules"]:
        module_id_to_name[m["instance_id"]] = f"{m['plugin']}:{m['model']}"

    for c in patch["connections"]:
        from_name = f"{c['from_plugin']}:{c['from_model']}"
        to_name = f"{c['to_plugin']}:{c['to_model']}"
        lines.append(f"- {from_name} **{c['from_port']}** → {to_name} **{c['to_port']}**")
    lines.append("")

    lines.append("## Module Parameters")
    lines.append("")
    for m in patch["modules"]:
        named = {k: v for k, v in m["params"].items() if not k.startswith("param_")}
        if not named:
            continue
        lines.append(f"### {m['plugin']}:{m['model']} (id: {m['instance_id']})")
        lines.append("")
        for name, value in named.items():
            lines.append(f"- **{name}:** {value:.4f}" if isinstance(value, float) else f"- **{name}:** {value}")
        lines.append("")

    return "\n".join(lines)


def main():
    profiles, decoded, notes, patterns, distributions, summary = load_all()

    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating {len(profiles)} module reference files...")
    for key, profile in profiles.items():
        content = generate_module_file(key, profile, decoded, patterns, distributions)
        filename = f"{profile['plugin']}--{profile['model']}.md"
        (MODULES_DIR / filename).write_text(content)

    print(f"  Written to {MODULES_DIR}/")

    print(f"Generating {len(decoded)} patch reference files...")
    for patch in decoded:
        content = generate_patch_file(patch, notes, profiles, patterns)
        (PATCHES_DIR / f"{patch['id']}.md").write_text(content)

    print(f"  Written to {PATCHES_DIR}/")

    index_lines = [
        "---",
        "name: VCV Rack Corpus Reference Index",
        "type: index",
        "triggers: [vcv, rack, patch, module, synth, modular, corpus]",
        "---",
        "",
        "# VCV Rack Corpus Reference",
        "",
        f"**{len(profiles)} modules** across **{len(decoded)} patches** from the VCV Rack community.",
        "",
        "## Modules by Role",
        "",
    ]

    roles = {"source": [], "processor": [], "output": [], "utility": []}
    for key, p in sorted(profiles.items(), key=lambda x: -x[1]["instance_count"]):
        role = p.get("role", "utility") or "utility"
        roles.setdefault(role, []).append((key, p))

    for role in ["source", "processor", "output", "utility"]:
        items = roles.get(role, [])
        index_lines.append(f"### {role.title()} ({len(items)})")
        index_lines.append("")
        for key, p in items[:20]:
            index_lines.append(f"- [{key}](modules/{p['plugin']}--{p['model']}.md) — {p['description'][:60]}")
        if len(items) > 20:
            index_lines.append(f"- ... and {len(items) - 20} more")
        index_lines.append("")

    index_lines.append("## Top Patches by Likes")
    index_lines.append("")
    for p in sorted(decoded, key=lambda x: -x["like_count"])[:20]:
        index_lines.append(f"- [{p['title']}](patches/{p['id']}.md) by {p['author']} ({p['like_count']} likes)")
    index_lines.append("")

    index_path = OUTPUT_DIR.parent / "reference" / "INDEX.md"
    index_path.write_text("\n".join(index_lines))

    print(f"\nSummary:")
    print(f"  Module files: {len(profiles)}")
    print(f"  Patch files:  {len(decoded)}")
    print(f"  Index:        {index_path}")


if __name__ == "__main__":
    main()
