"""Step 1: Fetch patch listing metadata from PatchStorage API.

Stores only listing-level fields (no detail fetching).
Stage 2 fetches /patches/{id} for files[] and downloads .vcv files.
"""

import json
import re
import sys
import time

import requests

from config import METADATA_PAGES_DIR, METADATA_DIR, RATE_LIMIT_DELAY, MIN_LIKES


API_URL = "https://patchstorage.com/api/alpha/patches/"
PLATFORM_ID = 745  # VCV Rack
PARAMS_BASE = {"platform": PLATFORM_ID, "per_page": 100}
HEADERS = {"User-Agent": "modular-mind/1.0"}


def extract_listing_fields(raw):
    return {
        "id": raw["id"],
        "title": raw["title"],
        "slug": raw["slug"],
        "like_count": raw.get("like_count", 0),
        "download_count": raw.get("download_count", 0),
        "platform_slug": raw.get("platform", {}).get("slug", ""),
        "author": raw.get("author", {}).get("name", ""),
        "created_at": raw.get("created_at", ""),
        "updated_at": raw.get("updated_at", ""),
        "categories": [c["name"] for c in raw.get("categories", [])],
        "tags": [t["name"] for t in raw.get("tags", [])],
        "detail_fetched": False,
        "status": "pending",
    }


def fetch_page(page_num):
    params = {**PARAMS_BASE, "page": page_num}
    return requests.get(API_URL, params=params, headers=HEADERS, timeout=30)


def find_resume_page():
    if not METADATA_PAGES_DIR.exists():
        return 1
    existing = [
        int(m.group(1))
        for f in METADATA_PAGES_DIR.glob("page_*.json")
        if (m := re.match(r"page_(\d+)\.json", f.name))
    ]
    if not existing:
        return 1
    return max(existing) + 1


def probe_page_one():
    print("Probing page 1 to inspect response structure...\n")
    resp = fetch_page(1)
    if resp.status_code != 200:
        print(f"ERROR: Got status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    if not data:
        print("ERROR: Empty response")
        sys.exit(1)

    print(f"Response: {len(data)} items")
    print(f"X-WP-Total: {resp.headers.get('X-WP-Total')}")
    print(f"X-WP-TotalPages: {resp.headers.get('X-WP-TotalPages')}")

    sample = None
    for raw in data:
        rec = extract_listing_fields(raw)
        if rec["platform_slug"] == "vcv-rack" and rec["like_count"] >= MIN_LIKES:
            sample = rec
            break

    if sample:
        print(f"\nSample record (VCV Rack, like_count >= {MIN_LIKES}):")
        print(json.dumps(sample, indent=2))
    else:
        print(f"\nNo VCV Rack patch with like_count >= {MIN_LIKES} on page 1")

    return data


def paginate(start_page):
    METADATA_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    page = start_page
    cumulative = 0

    if start_page > 1:
        for f in sorted(METADATA_PAGES_DIR.glob("page_*.json")):
            cumulative += len(json.loads(f.read_text()))
        print(f"Resuming from page {start_page} ({cumulative} patches cached)")

    while True:
        resp = fetch_page(page)
        if resp.status_code != 200:
            print(f"\nStopped: page {page} returned status {resp.status_code}")
            break

        data = resp.json()
        if not data:
            print(f"\nStopped: page {page} returned empty array")
            break

        records = [extract_listing_fields(raw) for raw in data]
        dest = METADATA_PAGES_DIR / f"page_{page:03d}.json"
        dest.write_text(json.dumps(records, indent=2))

        cumulative += len(records)
        if page % 10 == 0 or page == start_page:
            print(f"  Page {page}: {len(records)} patches (cumulative: {cumulative})")

        page += 1
        time.sleep(RATE_LIMIT_DELAY)

    print(f"  Final: page {page - 1}, cumulative: {cumulative}")
    return cumulative


def filter_and_consolidate():
    all_records = []
    for f in sorted(METADATA_PAGES_DIR.glob("page_*.json")):
        all_records.extend(json.loads(f.read_text()))

    total = len(all_records)
    vcv = [r for r in all_records if r["platform_slug"] == "vcv-rack"]
    filtered = [r for r in vcv if r["like_count"] >= MIN_LIKES]

    out_path = METADATA_DIR / "all_patches.json"
    out_path.write_text(json.dumps(filtered, indent=2))

    print(f"\nSummary:")
    print(f"  Total patches fetched:  {total}")
    print(f"  VCV Rack patches:       {len(vcv)}")
    print(f"  After like_count >= {MIN_LIKES}:  {len(filtered)}")
    print(f"  Rejected (non-VCV):     {total - len(vcv)}")
    print(f"  Rejected (low likes):   {len(vcv) - len(filtered)}")
    print(f"\nWritten to {out_path}")


def main():
    METADATA_PAGES_DIR.mkdir(parents=True, exist_ok=True)

    if "--probe" in sys.argv:
        probe_page_one()
        return

    start = find_resume_page()
    print(f"Fetching patch metadata from PatchStorage API...")
    cumulative = paginate(start)
    print(f"\nPagination complete: {cumulative} total patches")

    print("\nFiltering and consolidating...")
    filter_and_consolidate()


if __name__ == "__main__":
    main()
