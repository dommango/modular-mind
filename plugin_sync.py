"""Sync VCV Library plugins into the headless Rack user dir.

Uses the same (undocumented) api.vcvrack.com endpoints Rack itself uses
(Rack v2 src/library.cpp):

  GET  /library/manifests?version=2   public — slug -> {version, arches}
  POST /token {email, password}       VCV account login -> token
  GET  /download?slug&version&arch    .vcvplugin; token sent as a cookie

Auth: set VCV_TOKEN, or VCV_EMAIL + VCV_PASSWORD (a token is fetched once
per process). Fundamental downloads tokenless (Rack's own Makefile does),
but every other plugin returns 403 without a valid account token.

A downloaded <slug>-<version>-<arch>.vcvplugin is dropped into
<RACK_HEADLESS_DIR>/plugins-<arch>/; Rack extracts it on next startup.
Plugins that ship inside Rack itself (Core, Fundamental) and VCV-Recorder
(built from source into the render image, see render-service/Dockerfile)
are never downloaded.

Usage as a script (pre-warm the plugin cache):
  python3 plugin_sync.py <slug> [slug ...]
"""

import functools
import os
import sys
import time

import requests

from config import RACK_ARCH, RACK_HEADLESS_DIR, RATE_LIMIT_DELAY, VCV_TOKEN

API_BASE = "https://api.vcvrack.com"
# Bundled with Rack (Core, Fundamental) or built into the image (VCV-Recorder).
PREINSTALLED_PLUGINS = {"Core", "Fundamental", "VCV-Recorder"}
REQUEST_TIMEOUT = 60


class PluginSyncError(Exception):
    pass


def plugins_dir(arch=RACK_ARCH):
    return RACK_HEADLESS_DIR / f"plugins-{arch}"


@functools.lru_cache(maxsize=1)
def library_manifests():
    """slug -> manifest for every plugin in the VCV Library (public API)."""
    resp = requests.get(
        f"{API_BASE}/library/manifests",
        params={"version": "2"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["manifests"]


@functools.lru_cache(maxsize=1)
def get_token():
    """VCV account token from VCV_TOKEN, or a login with VCV_EMAIL/VCV_PASSWORD."""
    if VCV_TOKEN:
        return VCV_TOKEN
    email = os.environ.get("VCV_EMAIL")
    password = os.environ.get("VCV_PASSWORD")
    if not (email and password):
        return None
    resp = requests.post(
        f"{API_BASE}/token",
        json={"email": email, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def is_installed(slug, dest_dir):
    """Installed = extracted plugin dir, or a package Rack will extract."""
    if (dest_dir / slug).is_dir():
        return True
    return any(dest_dir.glob(f"{slug}-*.vcvplugin"))


def download_plugin(slug, version, arch, dest_dir):
    """Fetch one .vcvplugin into dest_dir. Raises PluginSyncError."""
    token = get_token()
    cookies = {"token": token} if token else {}
    resp = requests.get(
        f"{API_BASE}/download",
        params={"slug": slug, "version": version, "arch": arch},
        cookies=cookies,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 403:
        hint = "set VCV_TOKEN (or VCV_EMAIL/VCV_PASSWORD)" if not token else "token rejected"
        raise PluginSyncError(f"403 downloading {slug} {version}: {hint}")
    if resp.status_code != 200:
        raise PluginSyncError(f"HTTP {resp.status_code} downloading {slug} {version}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}-{version}-{arch}.vcvplugin"
    dest.write_bytes(resp.content)
    return dest


def missing_for_arch(slugs, arch=RACK_ARCH):
    """Slugs (minus preinstalled) that have no build for `arch` in the VCV
    Library — an availability check with no downloads. Lets a remote-render
    caller screen out uncoverable patches locally, leaving the actual
    download to whichever machine runs Rack."""
    manifests = library_manifests()
    missing = []
    for slug in sorted(set(slugs) - PREINSTALLED_PLUGINS):
        manifest = manifests.get(slug)
        if manifest is None or arch not in manifest.get("arches", {}):
            missing.append(slug)
    return missing


def ensure_plugins(slugs, dest_dir=None, arch=RACK_ARCH):
    """Make sure every slug is present in the Rack plugin dir.

    Returns {"installed": [...], "missing": [...], "reasons": {slug: why}}.
    Never raises for a single plugin — a patch with one unavailable plugin
    is recorded as skipped, not a worker crash.
    """
    dest_dir = dest_dir or plugins_dir(arch)

    installed = []
    missing = []
    reasons = {}
    for slug in sorted(set(slugs) - PREINSTALLED_PLUGINS):
        if is_installed(slug, dest_dir):
            installed.append(slug)
            continue
        try:
            manifest = library_manifests().get(slug)
            if manifest is None:
                raise PluginSyncError("not in VCV Library")
            if arch not in manifest.get("arches", {}):
                raise PluginSyncError(f"no {arch} build")
            download_plugin(slug, manifest["version"], arch, dest_dir)
            time.sleep(RATE_LIMIT_DELAY)
            installed.append(slug)
        except (PluginSyncError, requests.RequestException, KeyError, ValueError) as e:
            missing.append(slug)
            reasons = {**reasons, slug: str(e)}
    return {"installed": installed, "missing": missing, "reasons": reasons}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    result = ensure_plugins(sys.argv[1:])
    for slug in result["installed"]:
        print(f"OK       {slug}")
    for slug in result["missing"]:
        print(f"MISSING  {slug}: {result['reasons'][slug]}")
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
