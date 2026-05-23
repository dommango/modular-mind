"""Step 0: Build whitelist of free VCV Rack plugins from the official library."""

import json
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from config import GITHUB_TOKEN, RATE_LIMIT_DELAY, RAW_MANIFESTS_DIR, WHITELIST_DIR


RAW_BASE = "https://raw.githubusercontent.com/VCVRack/library/v2/manifests"
REPO_URL = "https://github.com/VCVRack/library.git"
PAID_LICENSE = "https://vcvrack.com/eula"
PROPRIETARY_LICENSE = "proprietary"


def get_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def fetch_manifest_listing():
    headers = get_headers()
    try:
        resp = requests.get(
            "https://api.github.com/repos/VCVRack/library/git/trees/v2",
            headers=headers, params={"recursive": "1"}, timeout=30,
        )
        resp.raise_for_status()
        return [
            {"name": item["path"].split("/")[-1]}
            for item in resp.json()["tree"]
            if item["path"].startswith("manifests/") and item["path"].endswith(".json")
        ]
    except requests.exceptions.HTTPError:
        print("  API rate-limited, falling back to shallow clone...")
        return _listing_via_clone()


def _listing_via_clone():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "v2",
             "--filter=blob:none", "--sparse", REPO_URL, tmp],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmp, "sparse-checkout", "set", "manifests"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmp, "checkout"],
            check=True, capture_output=True,
        )
        manifests_dir = Path(tmp) / "manifests"
        return [
            {"name": f.name}
            for f in sorted(manifests_dir.glob("*.json"))
        ]


def download_manifests(entries):
    downloaded, skipped = 0, 0
    for entry in entries:
        slug = entry["name"].removesuffix(".json")
        dest = RAW_MANIFESTS_DIR / f"{slug}.json"
        if dest.exists():
            skipped += 1
            continue
        raw_url = f"{RAW_BASE}/{entry['name']}"
        resp = requests.get(raw_url, timeout=30)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        downloaded += 1
        time.sleep(RATE_LIMIT_DELAY)
    print(f"Downloaded {downloaded} manifests, skipped {skipped} already cached")


def load_freeware_overrides():
    path = WHITELIST_DIR / "freeware_overrides.json"
    if path.exists():
        data = json.loads(path.read_text())
        return set(data.get("include", [])), set(data.get("exclude", []))
    return set(), set()


def build_free_plugins():
    free_plugins = {}
    freeware_plugins = {}
    total = 0
    paid = 0

    force_include, force_exclude = load_freeware_overrides()

    for manifest_path in sorted(RAW_MANIFESTS_DIR.glob("*.json")):
        total += 1
        data = json.loads(manifest_path.read_text())
        license_val = data.get("license", "")
        slug = manifest_path.stem
        module_slugs = [m["slug"] for m in data.get("modules", [])]

        if slug in force_exclude:
            paid += 1
            continue

        if slug in force_include:
            freeware_plugins[slug] = module_slugs
            continue

        if license_val == PAID_LICENSE:
            paid += 1
        elif license_val == PROPRIETARY_LICENSE:
            freeware_plugins[slug] = module_slugs
        else:
            free_plugins[slug] = module_slugs

    empty_manifests = sorted(
        slug for slug, modules in free_plugins.items() if len(modules) == 0
    )

    free_path = WHITELIST_DIR / "free_plugins.json"
    free_path.write_text(json.dumps(free_plugins, indent=2))

    freeware_path = WHITELIST_DIR / "freeware_plugins.json"
    freeware_path.write_text(json.dumps(freeware_plugins, indent=2))

    empty_path = WHITELIST_DIR / "empty_manifests.json"
    empty_path.write_text(json.dumps(empty_manifests, indent=2))

    print(f"\nSummary:")
    print(f"  Total plugins found: {total}")
    print(f"  Open-source:         {len(free_plugins)}")
    print(f"  Freeware:            {len(freeware_plugins)}")
    print(f"  Paid:                {paid}")
    print(f"  Empty manifests:     {len(empty_manifests)}")
    print(f"  Open-source modules: {sum(len(v) for v in free_plugins.values())}")
    print(f"  Freeware modules:    {sum(len(v) for v in freeware_plugins.values())}")
    print(f"\nWritten to {free_path}")
    print(f"Written to {freeware_path}")
    print(f"Written to {empty_path}")


def main():
    RAW_MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching manifest listing from GitHub...")
    entries = fetch_manifest_listing()
    print(f"Found {len(entries)} manifest files")
    print("Downloading manifests...")
    download_manifests(entries)
    print("Building free plugin whitelist...")
    build_free_plugins()


if __name__ == "__main__":
    main()
