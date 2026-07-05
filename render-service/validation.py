"""Stdlib-only request validation for the render service.

Kept free of third-party imports so tests can load it via
importlib.util.spec_from_file_location without fastapi installed
(render-service/ has a dash in its name, so it can't be a normal package).
"""

import hmac

ALLOWED_PLUGINS = {"Core", "Fundamental"}


def validate_patch_request(obj):
    """Return a list of error strings for a parsed patch request body;
    empty means valid. Only Core/Fundamental modules are allowed — those
    are the only plugins generated patches use, and the only ones built
    into the render image."""
    if not isinstance(obj, dict):
        return ["request body must be a JSON object"]

    modules = obj.get("modules")
    if not isinstance(modules, list) or not modules:
        return ["patch has no modules"]

    errors = []
    if not isinstance(obj.get("cables"), list):
        errors.append("patch missing cables list")

    for module in modules:
        plugin = module.get("plugin") if isinstance(module, dict) else None
        if plugin not in ALLOWED_PLUGINS:
            errors.append(f"disallowed plugin: {plugin!r}")

    return errors


def clamp_seconds(raw, default=10, cap=60):
    """Coerce a raw (possibly string, possibly absent) seconds value into
    an int in [1, cap], falling back to default when raw is missing or
    not a valid integer."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, cap))


def auth_ok(header, token):
    """Timing-safe Bearer-token check. An empty configured token always
    fails closed rather than accepting any request."""
    if not token:
        return False
    if not header or not header.startswith("Bearer "):
        return False
    provided = header[len("Bearer ") :]
    return hmac.compare_digest(provided, token)
