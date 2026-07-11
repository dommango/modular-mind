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


def seq_post(monkeypatch, outcomes):
    """Wire requests.post to yield successive outcomes: a FakeResponse is
    returned, an Exception instance is raised."""
    calls = {"n": 0}
    it = iter(outcomes)

    def fake_post(*a, **k):
        calls["n"] += 1
        outcome = next(it)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(rc.requests, "post", fake_post)
    return calls


def _remote_setup(monkeypatch):
    monkeypatch.setattr(rc, "parse_vcv", lambda p: {"modules": [], "cables": []})
    monkeypatch.setattr(rc, "RACK_RENDER_URL", "https://render.example.com")
    monkeypatch.setattr(rc, "RENDER_TOKEN", "secret")


def test_retry_delay_backs_off_and_caps():
    assert [rc.retry_delay(a) for a in range(1, 6)] == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert rc.retry_delay(10) == rc.RETRY_MAX_DELAY  # capped


def test_remote_render_retries_connection_error_then_succeeds(monkeypatch, tmp_path):
    _remote_setup(monkeypatch)
    slept = []
    calls = seq_post(
        monkeypatch,
        [
            rc.requests.ConnectionError("name resolution failed"),
            rc.requests.ConnectionError("name resolution failed"),
            FakeResponse(200, content=b"wav"),
        ],
    )

    out = rc.remote_render(
        tmp_path / "x.vcv", out_path=tmp_path / "o.wav", sleep_fn=slept.append
    )

    assert out.read_bytes() == b"wav"
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]  # backoff between the two failed attempts


def test_remote_render_retries_503_then_succeeds(monkeypatch, tmp_path):
    _remote_setup(monkeypatch)
    slept = []
    seq_post(monkeypatch, [FakeResponse(503, text="busy"), FakeResponse(200, content=b"ok")])

    out = rc.remote_render(
        tmp_path / "x.vcv", out_path=tmp_path / "o.wav", sleep_fn=slept.append
    )

    assert out.read_bytes() == b"ok"
    assert slept == [2.0]


def test_remote_render_does_not_retry_deterministic_error(monkeypatch, tmp_path):
    _remote_setup(monkeypatch)
    slept = []
    calls = seq_post(monkeypatch, [FakeResponse(422, text="missing-plugins: X")])

    with pytest.raises(RenderError, match="rejected the request"):
        rc.remote_render(tmp_path / "x.vcv", sleep_fn=slept.append)

    assert calls["n"] == 1  # no retry
    assert slept == []


def test_remote_render_raises_after_exhausting_attempts(monkeypatch, tmp_path):
    _remote_setup(monkeypatch)
    slept = []
    calls = seq_post(
        monkeypatch,
        [rc.requests.ConnectionError("dns") for _ in range(rc.RETRY_ATTEMPTS)],
    )

    with pytest.raises(RenderError, match="unreachable after 5 attempts"):
        rc.remote_render(tmp_path / "x.vcv", sleep_fn=slept.append)

    assert calls["n"] == rc.RETRY_ATTEMPTS
    assert len(slept) == rc.RETRY_ATTEMPTS - 1  # no sleep after the final attempt


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
