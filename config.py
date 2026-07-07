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

# Headless audio rendering (see CLAUDE.md "Headless rendering").
# Uses the Windows-side VCV Rack install via WSL interop; the scratch user dir
# isolates settings/autosave and holds a slim plugin set (Fundamental + VCV-Recorder).
AUDIO_DIR = DATA_DIR / "audio"
RACK_BINARY = Path(
    os.environ.get("RACK_BINARY", "/mnt/c/Program Files/VCV/Rack2Free/Rack.exe")
)
RACK_HEADLESS_DIR = Path(
    os.environ.get(
        "RACK_HEADLESS_DIR", "/mnt/c/Users/domma/AppData/Local/Temp/rack-headless"
    )
)
RACK_HEADLESS_DIR_WIN = os.environ.get(
    "RACK_HEADLESS_DIR_WIN", "C:/Users/domma/AppData/Local/Temp/rack-headless"
)
# VCV Library arch string / plugin dir for the platform Rack actually runs on
# (a .exe binary means the Windows build driven over WSL interop).
RACK_ARCH = "win-x64" if RACK_BINARY.suffix.lower() == ".exe" else "lin-x64"
VCV_TOKEN = os.environ.get("VCV_TOKEN")  # VCV account token for library downloads
RENDER_SECONDS = 10
RENDER_SAMPLE_RATE = 44100
RENDER_STARTUP_TIMEOUT = 90  # slack for Rack startup + WAV finalization

# Remote render backend (render-service/ on Railway). Unset RACK_RENDER_URL
# keeps rendering local via render_patch.render() (Windows/WSL interop).
RACK_RENDER_URL = os.environ.get("RACK_RENDER_URL", "").rstrip("/")
RENDER_TOKEN = os.environ.get("RENDER_TOKEN", "")
RENDER_MAX_SECONDS = 60
RENDER_MAX_PATCH_BYTES = 2_000_000

