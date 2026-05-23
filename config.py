import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

DATA_DIR = BASE_DIR / "data"
WHITELIST_DIR = DATA_DIR / "whitelist"
RAW_MANIFESTS_DIR = WHITELIST_DIR / "raw_manifests"
METADATA_DIR = DATA_DIR / "metadata"
METADATA_PAGES_DIR = METADATA_DIR / "pages"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_DIR = DATA_DIR / "output"

RATE_LIMIT_DELAY = 0.5
MIN_LIKES = 3
MIN_MODULES = 8
MAX_MODULES = 50
RACK_VERSION = "2"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

