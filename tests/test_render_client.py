from pathlib import Path

import pytest

import render_client as rc
from render_patch import RenderError


def test_select_backend_local_when_unset():
    assert rc.select_backend("") == "local"


def test_select_backend_remote_when_set():
    assert rc.select_backend("https://render.example.com") == "remote"


def test_remote_timeout_scales_with_seconds():
    assert rc.remote_timeout(10) == (10, 200)
    assert rc.remote_timeout(60) == (10, 300)


@pytest.mark.parametrize(
    "status,expected_substr",
    [
        (401, "auth token"),
        (413, "too large"),
        (422, "rejected the request"),
        (503, "busy"),
        (500, "render failed"),
        (404, "returned 404"),
    ],
)
def test_map_remote_error(status, expected_substr):
    assert expected_substr in rc.map_remote_error(status, "detail")


class FakeResponse:
    def __init__(self, status_code, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text or content.decode(errors="ignore")


def test_remote_render_raises_on_non_200(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "parse_vcv", lambda p: {"modules": [], "cables": []})
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "https://render.example.com")
    monkeypatch.setattr(rc, "RENDER_TOKEN", "secret")
    monkeypatch.setattr(rc.requests, "post", lambda *a, **k: FakeResponse(401, text="bad token"))

    with pytest.raises(RenderError, match="auth token"):
        rc.remote_render(tmp_path / "x.vcv")


def test_remote_render_writes_wav_on_200(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "parse_vcv", lambda p: {"modules": [], "cables": []})
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "https://render.example.com")
    monkeypatch.setattr(rc, "RENDER_TOKEN", "secret")
    monkeypatch.setattr(rc.requests, "post", lambda *a, **k: FakeResponse(200, content=b"RIFF..."))

    out_path = tmp_path / "out.wav"
    result = rc.remote_render(tmp_path / "x.vcv", out_path=out_path)

    assert result == out_path
    assert out_path.read_bytes() == b"RIFF..."


def test_remote_render_sends_auth_header_and_seconds_param(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "parse_vcv", lambda p: {"modules": [], "cables": []})
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "https://render.example.com")
    monkeypatch.setattr(rc, "RENDER_TOKEN", "secret")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(200, content=b"wav")

    monkeypatch.setattr(rc.requests, "post", fake_post)

    rc.remote_render(tmp_path / "x.vcv", out_path=tmp_path / "out.wav", seconds=15)

    assert captured["url"] == "https://render.example.com/render"
    assert captured["params"] == {"seconds": 15}
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == (10, 210)


def test_render_dispatches_to_local_when_url_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "")
    called = {}

    def fake_local_render(vcv_path, out_path=None, seconds=10):
        called["args"] = (vcv_path, out_path, seconds)
        return Path("/tmp/x.wav")

    monkeypatch.setattr(rc, "local_render", fake_local_render)

    result = rc.render(tmp_path / "x.vcv", seconds=5)

    assert called["args"] == (tmp_path / "x.vcv", None, 5)
    assert result == Path("/tmp/x.wav")


def test_render_dispatches_to_remote_when_url_set(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "https://render.example.com")
    called = {}

    def fake_remote_render(vcv_path, out_path=None, seconds=10):
        called["args"] = (vcv_path, out_path, seconds)
        return Path("/tmp/y.wav")

    monkeypatch.setattr(rc, "remote_render", fake_remote_render)

    result = rc.render(tmp_path / "x.vcv", seconds=5)

    assert called["args"] == (tmp_path / "x.vcv", None, 5)
    assert result == Path("/tmp/y.wav")
