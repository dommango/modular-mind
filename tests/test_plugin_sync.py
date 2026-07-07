import pytest

import plugin_sync
from plugin_sync import PluginSyncError, ensure_plugins, is_installed

# live API shape: arches is a dict of arch -> bool (membership-checked)
MANIFESTS = {
    "Valley": {"version": "2.4.0", "arches": {"lin-x64": True, "win-x64": True}},
    "WinOnly": {"version": "1.0.0", "arches": {"win-x64": True}},
}


@pytest.fixture
def fake_library(monkeypatch):
    monkeypatch.setattr(plugin_sync, "library_manifests", lambda: MANIFESTS)
    downloads = []

    def fake_download(slug, version, arch, dest_dir):
        downloads.append((slug, version, arch))
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{slug}-{version}-{arch}.vcvplugin"
        dest.write_bytes(b"pkg")
        return dest

    monkeypatch.setattr(plugin_sync, "download_plugin", fake_download)
    monkeypatch.setattr(plugin_sync.time, "sleep", lambda s: None)
    return downloads


def test_is_installed_extracted_dir(tmp_path):
    (tmp_path / "Valley").mkdir()
    assert is_installed("Valley", tmp_path)


def test_is_installed_package(tmp_path):
    (tmp_path / "Valley-2.4.0-lin-x64.vcvplugin").write_bytes(b"pkg")
    assert is_installed("Valley", tmp_path)
    assert not is_installed("Befaco", tmp_path)


def test_ensure_plugins_downloads_available(tmp_path, fake_library):
    result = ensure_plugins(["Valley"], dest_dir=tmp_path, arch="lin-x64")
    assert result["installed"] == ["Valley"]
    assert result["missing"] == []
    assert fake_library == [("Valley", "2.4.0", "lin-x64")]
    assert is_installed("Valley", tmp_path)


def test_ensure_plugins_skips_preinstalled(tmp_path, fake_library):
    result = ensure_plugins(
        ["Core", "Fundamental", "VCV-Recorder"], dest_dir=tmp_path, arch="lin-x64"
    )
    assert result == {"installed": [], "missing": [], "reasons": {}}
    assert fake_library == []


def test_ensure_plugins_reports_missing(tmp_path, fake_library):
    result = ensure_plugins(
        ["NotInLibrary", "WinOnly"], dest_dir=tmp_path, arch="lin-x64"
    )
    assert result["installed"] == []
    assert result["missing"] == ["NotInLibrary", "WinOnly"]
    assert "not in VCV Library" in result["reasons"]["NotInLibrary"]
    assert "no lin-x64 build" in result["reasons"]["WinOnly"]


def test_ensure_plugins_already_installed_skips_download(tmp_path, fake_library):
    (tmp_path / "Valley").mkdir()
    result = ensure_plugins(["Valley"], dest_dir=tmp_path, arch="lin-x64")
    assert result["installed"] == ["Valley"]
    assert fake_library == []


def test_ensure_plugins_download_failure_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_sync, "library_manifests", lambda: MANIFESTS)

    def boom(slug, version, arch, dest_dir):
        raise PluginSyncError("403 downloading Valley 2.4.0: token rejected")

    monkeypatch.setattr(plugin_sync, "download_plugin", boom)
    result = ensure_plugins(["Valley"], dest_dir=tmp_path, arch="lin-x64")
    assert result["missing"] == ["Valley"]
    assert "403" in result["reasons"]["Valley"]


def test_plugins_dir_naming():
    assert plugin_sync.plugins_dir("lin-x64").name == "plugins-lin-x64"
    assert plugin_sync.plugins_dir("win-x64").name == "plugins-win-x64"
