"""Step 7: Build comprehensive module profiles from multiple sources.

Sources:
  1. VCV Library manifests — description, tags, manualUrl
  2. GitHub README docs — per-module sections from cloned repos
  3. GitHub doc/ directories — per-module markdown files
  4. Port registry — param/input/output names
  5. PatchStorage detail — author patch notes (for the 137 decoded patches)
  6. Analysis summary — module roles from connection topology

Outputs:
  - data/output/module_profiles.json — one profile per module
  - data/output/patch_notes.json — author descriptions for decoded patches
"""

import importlib
import json
import os
import re
import time

import requests

from config import OUTPUT_DIR, METADATA_DIR, WHITELIST_DIR, RATE_LIMIT_DELAY

REPOS_DIR = OUTPUT_DIR.parent / "repos"
MANIFESTS_DIR = WHITELIST_DIR / "raw_manifests"
HEADERS = {"User-Agent": "vcv-corpus/1.0"}
DETAIL_URL = "https://patchstorage.com/api/alpha/patches/{id}"


def get_target_modules():
    decoded = json.loads((OUTPUT_DIR / "decoded_patches.json").read_text())
    modules = {}
    for p in decoded:
        for m in p["modules"]:
            key = (m["plugin"], m["model"])
            if key not in modules:
                modules[key] = 0
            modules[key] += 1
    return modules


def load_manifest_metadata(plugin, model):
    path = MANIFESTS_DIR / f"{plugin}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    for m in data.get("modules", []):
        if m["slug"] == model:
            return {
                "description": m.get("description", ""),
                "tags": m.get("tags", []),
                "manual_url": m.get("manualUrl", ""),
            }
    return {}


def load_registry_info(plugin, model):
    registry = json.loads((OUTPUT_DIR / "port_registry.json").read_text())
    model_data = registry.get(plugin, {}).get(model, {})
    return {
        "params": [
            {"name": p.get("name", ""), "unit": p.get("unit", "")}
            for p in model_data.get("params", [])
            if p.get("name")
        ],
        "inputs": [p.get("name", "") for p in model_data.get("inputs", []) if p.get("name")],
        "outputs": [p.get("name", "") for p in model_data.get("outputs", []) if p.get("name")],
    }


def load_module_role(plugin, model):
    summary_path = OUTPUT_DIR / "analysis_summary.json"
    if not summary_path.exists():
        return ""
    summary = json.loads(summary_path.read_text())
    roles = summary.get("module_roles", {})
    entry = roles.get(f"{plugin}:{model}", {})
    return entry.get("role", "")


def extract_readme_sections(repo_name):
    readme_path = None
    for candidate in ["README.md", "readme.md"]:
        p = REPOS_DIR / repo_name / candidate
        if p.exists():
            readme_path = p
            break
    if not readme_path:
        return {}

    text = readme_path.read_text(errors="ignore")
    sections = {}

    anchor_pattern = re.compile(
        r'<a\s+name="([^"]+)"\s*>\s*</a>\s*(.+?)(?=\n)',
        re.IGNORECASE,
    )
    header_pattern = re.compile(r'^(#{2,4})\s+(.+)', re.MULTILINE)

    headers = []
    for m in header_pattern.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        title = re.sub(r'<[^>]+>', '', title).strip()
        headers.append((m.start(), level, title))

    for i, (start, level, title) in enumerate(headers):
        if i + 1 < len(headers):
            next_start = headers[i + 1][0]
            next_level = headers[i + 1][1]
            if next_level <= level:
                body = text[start:next_start]
            else:
                for j in range(i + 1, len(headers)):
                    if headers[j][1] <= level:
                        body = text[start:headers[j][0]]
                        break
                else:
                    body = text[start:]
        else:
            body = text[start:]

        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.strip()

        if len(body) > 50:
            clean_title = re.sub(r'[^a-zA-Z0-9]', '', title).lower()
            sections[clean_title] = {
                "title": title,
                "content": body[:3000],
            }

    return sections


def extract_doc_files(repo_name):
    docs = {}
    for dirname in ["doc", "docs", "manual"]:
        doc_dir = REPOS_DIR / repo_name / dirname
        if not doc_dir.is_dir():
            continue
        for f in doc_dir.iterdir():
            if f.suffix == ".md" and f.stat().st_size > 100:
                slug = f.stem.lower()
                content = f.read_text(errors="ignore")[:3000]
                docs[slug] = {
                    "title": f.stem,
                    "content": content,
                }
    return docs


def match_module_to_doc(model, readme_sections, doc_files):
    model_lower = model.lower()
    model_clean = re.sub(r'[^a-z0-9]', '', model_lower)

    prefixed_variants = [model_clean]
    if "-" in model:
        parts = model.split("-")
        prefixed_variants.append(parts[-1].lower())
        prefixed_variants.append(re.sub(r'[^a-z0-9]', '', parts[-1].lower()))

    for variant in prefixed_variants:
        if variant in readme_sections:
            return readme_sections[variant]["content"]
        if variant in doc_files:
            return doc_files[variant]["content"]

    for key, section in readme_sections.items():
        if model_clean in key or key in model_clean:
            return section["content"]

    for key, doc in doc_files.items():
        if model_clean in key or key in model_clean:
            return doc["content"]

    return ""


def fetch_patch_notes(patch_ids):
    notes = {}
    fetched = 0
    skipped = 0

    notes_path = OUTPUT_DIR / "patch_notes.json"
    if notes_path.exists():
        notes = json.loads(notes_path.read_text())

    remaining = [pid for pid in patch_ids if str(pid) not in notes]
    if not remaining:
        print(f"  All {len(patch_ids)} patch notes already cached")
        return notes

    print(f"  Fetching {len(remaining)} patch details from PatchStorage...")
    for i, pid in enumerate(remaining, 1):
        try:
            resp = requests.get(
                DETAIL_URL.format(id=pid),
                headers=HEADERS, timeout=30,
            )
            if resp.status_code == 200:
                detail = resp.json()
                content = detail.get("content", "")
                content = re.sub(r'<[^>]+>', '', content).strip()
                notes[str(pid)] = {
                    "content": content[:2000] if content else "",
                    "license": detail.get("license", {}).get("name", ""),
                    "preview_url": detail.get("preview_url", ""),
                }
                fetched += 1
            else:
                notes[str(pid)] = {"content": "", "license": "", "preview_url": ""}
                skipped += 1
        except Exception:
            notes[str(pid)] = {"content": "", "license": "", "preview_url": ""}
            skipped += 1

        if i % 20 == 0:
            notes_path.write_text(json.dumps(notes, indent=2))
            print(f"    [{i}/{len(remaining)}] fetched={fetched} skipped={skipped}")

        time.sleep(RATE_LIMIT_DELAY)

    notes_path.write_text(json.dumps(notes, indent=2))
    print(f"  Done: {fetched} fetched, {skipped} skipped")
    return notes


def _html_to_text(html):
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _fetch_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _extract_fragment_section(html, fragment):
    pattern = rf'id="{re.escape(fragment)}"[^>]*>(.+?)(?=id="[A-Z])'
    m = re.search(pattern, html, re.DOTALL)
    if m:
        return _html_to_text(m.group(1))[:3000]

    idx = html.find(f'id="{fragment}"')
    if idx >= 0:
        chunk = html[idx:idx + 5000]
        return _html_to_text(chunk)[:3000]
    return ""


def fetch_manual_docs(profiles):
    page_cache = {}
    fetched = 0
    skipped = 0

    to_fetch = [
        (key, p) for key, p in profiles.items()
        if not p["documentation"] and p["manual_url"]
    ]
    if not to_fetch:
        return

    print(f"  Fetching {len(to_fetch)} manual URLs...")

    for i, (key, profile) in enumerate(to_fetch, 1):
        url = profile["manual_url"]
        fragment = ""
        if "#" in url:
            url, fragment = url.rsplit("#", 1)

        if url not in page_cache:
            html = _fetch_page(url)
            page_cache[url] = html
            time.sleep(RATE_LIMIT_DELAY)
        else:
            html = page_cache[url]

        if not html:
            skipped += 1
            continue

        if fragment:
            content = _extract_fragment_section(html, fragment)
        else:
            content = _html_to_text(html)[:3000]

        if len(content) > 50:
            profile["documentation"] = content
            fetched += 1
        else:
            skipped += 1

        if i % 20 == 0:
            print(f"    [{i}/{len(to_fetch)}] fetched={fetched} skipped={skipped}")

    print(f"  Manual docs: {fetched} fetched, {skipped} skipped")


def build_profiles(target_modules):
    registry = json.loads((OUTPUT_DIR / "port_registry.json").read_text())
    summary_path = OUTPUT_DIR / "analysis_summary.json"
    roles = {}
    if summary_path.exists():
        roles = json.loads(summary_path.read_text()).get("module_roles", {})

    readme_cache = {}
    doc_cache = {}

    profiles = {}
    for (plugin, model), instance_count in sorted(
        target_modules.items(), key=lambda x: -x[1]
    ):
        manifest = load_manifest_metadata(plugin, model)

        if plugin not in readme_cache:
            readme_cache[plugin] = extract_readme_sections(plugin)
            doc_cache[plugin] = extract_doc_files(plugin)

        readme_doc = match_module_to_doc(
            model, readme_cache[plugin], doc_cache[plugin]
        )

        model_reg = registry.get(plugin, {}).get(model, {})
        params = [
            p.get("name", "") for p in model_reg.get("params", [])
            if p.get("name") and not p["name"].startswith("param_")
        ]
        inputs = [
            p.get("name", "") for p in model_reg.get("inputs", [])
            if p.get("name") and not p["name"].startswith("input_")
        ]
        outputs = [
            p.get("name", "") for p in model_reg.get("outputs", [])
            if p.get("name") and not p["name"].startswith("output_")
        ]

        role_entry = roles.get(f"{plugin}:{model}", {})

        profiles[f"{plugin}:{model}"] = {
            "plugin": plugin,
            "model": model,
            "instance_count": instance_count,
            "description": manifest.get("description", ""),
            "tags": manifest.get("tags", []),
            "manual_url": manifest.get("manual_url", ""),
            "role": role_entry.get("role", ""),
            "params": params,
            "inputs": inputs,
            "outputs": outputs,
            "documentation": readme_doc,
        }

    return profiles


def main():
    print("Loading target modules from decoded patches...")
    target_modules = get_target_modules()
    print(f"  {len(target_modules)} unique modules across 137 patches")

    print("\nBuilding module profiles...")
    profiles = build_profiles(target_modules)

    with_docs = sum(1 for p in profiles.values() if p["documentation"])
    with_desc = sum(1 for p in profiles.values() if p["description"])
    with_params = sum(1 for p in profiles.values() if p["params"])

    profiles_path = OUTPUT_DIR / "module_profiles.json"
    profiles_path.write_text(json.dumps(profiles, indent=2))

    print(f"  Profiles built: {len(profiles)}")
    print(f"  With description: {with_desc}")
    print(f"  With documentation: {with_docs}")
    print(f"  With named params: {with_params}")

    print("\nFetching manual page documentation...")
    fetch_manual_docs(profiles)

    with_docs = sum(1 for p in profiles.values() if p["documentation"])
    print(f"  Total with documentation: {with_docs}/{len(profiles)}")

    profiles_path.write_text(json.dumps(profiles, indent=2))
    print(f"  Written to {profiles_path}")

    print("\nFetching patch notes from PatchStorage...")
    decoded = json.loads((OUTPUT_DIR / "decoded_patches.json").read_text())
    patch_ids = [p["id"] for p in decoded]
    notes = fetch_patch_notes(patch_ids)

    with_content = sum(1 for n in notes.values() if n.get("content"))
    print(f"  Patch notes with content: {with_content}/{len(notes)}")

    notes_path = OUTPUT_DIR / "patch_notes.json"
    notes_path.write_text(json.dumps(notes, indent=2))
    print(f"  Written to {notes_path}")

    print(f"\nSummary:")
    print(f"  Module profiles:    {len(profiles)}")
    print(f"  With documentation: {with_docs} ({with_docs/len(profiles)*100:.0f}%)")
    print(f"  Patch notes:        {with_content}/{len(notes)}")


if __name__ == "__main__":
    main()
