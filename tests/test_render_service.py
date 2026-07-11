import importlib.util
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parent.parent / "render-service"


def _load_validation():
    spec = importlib.util.spec_from_file_location(
        "render_service_validation", SERVICE_DIR / "validation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validation = _load_validation()


def test_validate_patch_request_accepts_any_plugin():
    # plugins are downloaded on demand now, not allowlisted — structural only
    patch = {
        "modules": [
            {"id": 1, "plugin": "Core", "model": "AudioInterface"},
            {"id": 2, "plugin": "Bogaudio", "model": "VCO"},
        ],
        "cables": [],
    }
    assert validation.validate_patch_request(patch) == []


def test_validate_patch_request_rejects_non_dict_body():
    assert validation.validate_patch_request(["not", "a", "dict"]) != []
    assert validation.validate_patch_request(None) != []


def test_validate_patch_request_rejects_missing_modules():
    assert validation.validate_patch_request({"cables": []}) != []


def test_validate_patch_request_rejects_missing_cables():
    patch = {"modules": [{"id": 1, "plugin": "Core", "model": "AudioInterface"}]}
    assert validation.validate_patch_request(patch) != []


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 10),
        (0, 1),
        (61, 60),
        ("abc", 10),
        (10, 10),
        ("25", 25),
    ],
)
def test_clamp_seconds(raw, expected):
    assert validation.clamp_seconds(raw, default=10, cap=60) == expected


def test_auth_ok_accepts_matching_bearer_token():
    assert validation.auth_ok("Bearer secret123", "secret123") is True


def test_auth_ok_rejects_mismatched_token():
    assert validation.auth_ok("Bearer wrong", "secret123") is False


def test_auth_ok_rejects_missing_header():
    assert validation.auth_ok(None, "secret123") is False


def test_auth_ok_rejects_malformed_header():
    assert validation.auth_ok("secret123", "secret123") is False


def test_auth_ok_rejects_empty_configured_token():
    assert validation.auth_ok("Bearer anything", "") is False
