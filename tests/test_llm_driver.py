import json

import pytest

import llm_driver as ld
from llm_driver import ClaudeCLIDriver, DriverError, get_driver


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_complete_returns_result_field(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FakeCompleted(stdout=json.dumps({"result": "hello patch"}))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    driver = ClaudeCLIDriver()
    assert driver.complete("design me a drone") == "hello patch"
    assert len(calls) == 1


def test_prompt_passed_via_stdin_not_argv(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeCompleted(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    prompt = "x" * 40_000
    ClaudeCLIDriver().complete(prompt)

    assert captured["kwargs"]["input"] == prompt
    assert captured["kwargs"]["text"] is True
    assert all(prompt not in str(arg) for arg in captured["command"])


def test_command_includes_model_when_set(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return FakeCompleted(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    ClaudeCLIDriver(model="claude-opus-4-5").complete("prompt")

    assert "--model" in captured["command"]
    assert "claude-opus-4-5" in captured["command"]


def test_command_omits_model_when_unset(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return FakeCompleted(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    ClaudeCLIDriver().complete("prompt")

    assert "--model" not in captured["command"]


def test_nonzero_exit_retries_then_raises(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(1)
        return FakeCompleted(returncode=1, stderr="boom: something broke")

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    driver = ClaudeCLIDriver(retries=2)
    with pytest.raises(DriverError) as exc_info:
        driver.complete("prompt")

    assert len(calls) == 3  # retries=2 -> 1 initial + 2 retries
    assert "boom: something broke" in str(exc_info.value)


def test_default_retries_is_one_extra_attempt(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(1)
        return FakeCompleted(returncode=1, stderr="fail")

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    with pytest.raises(DriverError):
        ClaudeCLIDriver().complete("prompt")

    assert len(calls) == 2  # default retries=1 -> 2 total attempts


def test_stderr_tail_is_truncated(monkeypatch):
    long_stderr = "x" * 1000 + "the important tail"

    def fake_run(command, **kwargs):
        return FakeCompleted(returncode=1, stderr=long_stderr)

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    with pytest.raises(DriverError) as exc_info:
        ClaudeCLIDriver(retries=0).complete("prompt")

    message = str(exc_info.value)
    assert "the important tail" in message
    assert len(message) < len(long_stderr)


def test_timeout_raises_driver_error(monkeypatch):
    import subprocess as real_subprocess

    calls = []

    def fake_run(command, **kwargs):
        calls.append(1)
        raise real_subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    driver = ClaudeCLIDriver(retries=1)
    with pytest.raises(DriverError):
        driver.complete("prompt")

    assert len(calls) == 2


def test_malformed_json_stdout_raises_driver_error(monkeypatch):
    def fake_run(command, **kwargs):
        return FakeCompleted(stdout="not json at all")

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    with pytest.raises(DriverError):
        ClaudeCLIDriver(retries=0).complete("prompt")


def test_missing_result_key_raises_driver_error(monkeypatch):
    def fake_run(command, **kwargs):
        return FakeCompleted(stdout=json.dumps({"not_result": "oops"}))

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    with pytest.raises(DriverError):
        ClaudeCLIDriver(retries=0).complete("prompt")


def test_get_driver_returns_claude_cli_instance():
    driver = get_driver("claude-cli", model="foo", timeout=10, retries=3)
    assert isinstance(driver, ClaudeCLIDriver)
    assert driver.model == "foo"
    assert driver.timeout == 10
    assert driver.retries == 3


def test_get_driver_defaults_to_claude_cli():
    assert isinstance(get_driver(), ClaudeCLIDriver)


def test_get_driver_anthropic_api_not_implemented():
    with pytest.raises(NotImplementedError):
        get_driver("anthropic-api")


def test_get_driver_unknown_name_raises_value_error():
    with pytest.raises(ValueError):
        get_driver("some-other-driver")


def test_missing_binary_raises_driver_error(monkeypatch):
    def fake_run(command, **kwargs):
        raise FileNotFoundError("No such file or directory: 'claude'")

    monkeypatch.setattr(ld.subprocess, "run", fake_run)

    with pytest.raises(DriverError, match="failed to launch claude CLI"):
        ClaudeCLIDriver(retries=0).complete("hi")
