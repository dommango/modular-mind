"""Step 2: Fetch patch details and download .vcv files.

For each patch in all_patches.json:
  1. GET /api/alpha/patches/{id} to find files[] with .vcv download URL
  2. Download the .vcv file to data/raw/<id>.vcv
  3. Update manifest.json after every single operation (crash-safe)
"""

import json
import sys
import time

import requests

from config import METADATA_DIR, RAW_DIR, RATE_LIMIT_DELAY


DETAIL_URL = "https://patchstorage.com/api/alpha/patches/{id}"
HEADERS = {"User-Agent": "vcv-corpus/1.0"}
MANIFEST_PATH = RAW_DIR / "manifest.json"


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def init_manifest(patches):
    manifest = load_manifest()
    added = 0
    for p in patches:
        pid = str(p["id"])
        if pid not in manifest:
            manifest[pid] = {
                "detail_fetched": False,
                "status": "pending",
                "filename": None,
                "reason": None,
            }
            added += 1
    if added > 0:
        save_manifest(manifest)
        print(f"  Added {added} new entries to manifest")
    return manifest


def fetch_detail(patch_id):
    url = DETAIL_URL.format(id=patch_id)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp


def find_vcv_url(files):
    for f in files:
        filename = f.get("filename", "")
        if filename.endswith(".vcv"):
            return f.get("url"), filename
    return None, None


def download_file(url, dest):
    resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)
    return resp.status_code


def process_patch(patch_id, manifest):
    pid = str(patch_id)
    entry = manifest[pid]

    if not entry["detail_fetched"]:
        try:
            resp = fetch_detail(patch_id)
            time.sleep(RATE_LIMIT_DELAY)
        except requests.RequestException as e:
            entry["status"] = "failed"
            entry["reason"] = f"detail request error: {e}"
            save_manifest(manifest)
            return

        if resp.status_code != 200:
            entry["status"] = "failed"
            entry["reason"] = f"detail returned {resp.status_code}"
            entry["detail_fetched"] = True
            save_manifest(manifest)
            return

        detail = resp.json()
        files = detail.get("files", [])
        download_url, filename = find_vcv_url(files)

        entry["detail_fetched"] = True

        if download_url is None:
            if not files:
                entry["status"] = "skipped"
                entry["reason"] = "no files in detail response"
            else:
                extensions = [f.get("filename", "?") for f in files]
                entry["status"] = "skipped"
                entry["reason"] = f"no .vcv file found: {extensions}"
            save_manifest(manifest)
            return

        entry["_download_url"] = download_url
        entry["filename"] = filename
        save_manifest(manifest)
    else:
        download_url = entry.get("_download_url")
        if download_url is None:
            return

    try:
        dest = RAW_DIR / f"{patch_id}.vcv"
        download_file(download_url, dest)
        time.sleep(RATE_LIMIT_DELAY)
        entry["status"] = "downloaded"
        entry.pop("_download_url", None)
    except requests.RequestException as e:
        entry["status"] = "failed"
        entry["reason"] = f"download error: {e}"

    save_manifest(manifest)


def print_summary(manifest):
    counts = {"pending": 0, "downloaded": 0, "failed": 0, "skipped": 0}
    for entry in manifest.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    print(f"\nSummary:")
    print(f"  Total patches:  {len(manifest)}")
    print(f"  Downloaded:     {counts['downloaded']}")
    print(f"  Skipped:        {counts['skipped']}")
    print(f"  Failed:         {counts['failed']}")
    print(f"  Pending:        {counts['pending']}")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    patches_path = METADATA_DIR / "all_patches.json"
    patches = json.loads(patches_path.read_text())
    print(f"Loaded {len(patches)} patches from all_patches.json")

    manifest = init_manifest(patches)

    pending = [
        p for p in patches
        if manifest[str(p["id"])]["status"] == "pending"
    ]
    print(f"Pending: {len(pending)} patches to process")

    if not pending:
        print("Nothing to do.")
        print_summary(manifest)
        return

    for i, p in enumerate(pending, 1):
        process_patch(p["id"], manifest)

        if i % 50 == 0 or i == len(pending):
            m = manifest
            dl = sum(1 for e in m.values() if e["status"] == "downloaded")
            sk = sum(1 for e in m.values() if e["status"] == "skipped")
            fa = sum(1 for e in m.values() if e["status"] == "failed")
            print(f"  [{i}/{len(pending)}] downloaded={dl} skipped={sk} failed={fa}")

    print_summary(manifest)


if __name__ == "__main__":
    main()
