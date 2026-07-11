"""LLM driver for patch generation — wraps the `claude` CLI as a subprocess.

Loop 3 (LLM-driven patch generation) talks to the model exclusively
through this module: a prompt goes in over stdin, JSON comes back on
stdout via `claude -p --output-format json`, and the "result" field is
returned as plain text. Kept separate from llm_prompts.py so prompt
construction stays pure and testable without ever shelling out.

Usage:
  from llm_driver import get_driver
  driver = get_driver("claude-cli", model="claude-opus-4-5")
  reply = driver.complete(prompt_text)
"""

import json
import subprocess

DEFAULT_TIMEOUT = 300
DEFAULT_RETRIES = 1
STDERR_TAIL_CHARS = 300


class DriverError(Exception):
    pass


class ClaudeCLIDriver:
    """Drives the `claude` CLI as a one-shot subprocess per prompt.

    The prompt goes in via stdin only — prompts here run past 40KB and
    argv has OS-level size limits that don't surface until a launch
    silently fails, so it's stdin or nothing.
    """

    def __init__(self, model=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def _command(self):
        command = ["claude", "-p", "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]
        return command

    def _attempt(self, prompt):
        try:
            proc = subprocess.run(
                self._command(),
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise DriverError(f"claude CLI timed out after {self.timeout}s") from e
        except OSError as e:
            raise DriverError(f"failed to launch claude CLI: {e}") from e

        if proc.returncode != 0:
            raise DriverError(
                f"claude CLI exited {proc.returncode}: {_tail(proc.stderr)}"
            )
        try:
            payload = json.loads(proc.stdout)
            return payload["result"]
        except (json.JSONDecodeError, KeyError) as e:
            raise DriverError(
                f"claude CLI returned unparseable output ({e}): {_tail(proc.stdout)}"
            ) from e

    def complete(self, prompt):
        """Send `prompt` to the CLI over stdin and return its text reply.

        Retries up to `self.retries` extra times on a nonzero exit,
        timeout, or malformed stdout before raising DriverError.
        """
        last_error = None
        for _ in range(self.retries + 1):
            try:
                return self._attempt(prompt)
            except DriverError as e:
                last_error = e
        raise last_error


def _tail(text, limit=STDERR_TAIL_CHARS):
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


DRIVER_REGISTRY = {"claude-cli": ClaudeCLIDriver}


def get_driver(name="claude-cli", **kwargs):
    """Return a driver instance for `name`.

    Raises NotImplementedError for the placeholder "anthropic-api" driver
    and ValueError for any unregistered name.
    """
    if name == "anthropic-api":
        raise NotImplementedError(
            "anthropic-api driver not implemented — install the anthropic SDK and add it here"
        )
    if name not in DRIVER_REGISTRY:
        raise ValueError(f"unknown driver: {name}")
    return DRIVER_REGISTRY[name](**kwargs)
