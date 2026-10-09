"""Execute shipped shell templates against recording stub network tools.

Shared by the hermes_agent tests that run a rendered script instead of matching
its text. A template is rendered with a role's defaults plus the test's own
values, using Ansible's filter plugins. The script then runs with a PATH whose
curl, python3 and logger are recording stubs, so nothing reaches the network or
the system log and every outbound call can be read back afterwards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from ansible.plugins.filter.core import FilterModule
from jinja2 import ChainableUndefined, Environment, StrictUndefined

ROLES = Path(__file__).resolve().parents[2] / "roles"

# A health probe asks for -w and is answered with the next status the test
# queued. An ntfy publish carries -d and is recorded with its URL and headers.
_CURL = r"""#!@PYTHON@
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["STUB_LOG_DIR"])
argv = sys.argv[1:]


def record(name, entry):
    with (log / name).open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


if "-w" in argv:
    queue = log / "probe-codes"
    codes = queue.read_text().splitlines() if queue.exists() else []
    if not codes:
        sys.exit(7)  # nothing queued: the endpoint is unreachable
    queue.write_text("".join(f"{code}\n" for code in codes[1:]))
    record("probes.jsonl", {"url": argv[-1]})
    sys.stdout.write(codes[0])
elif "-d" in argv:
    record("posts.jsonl", {
        "url": argv[-1],
        "headers": [argv[i + 1] for i, arg in enumerate(argv) if arg == "-H"],
        "body": argv[argv.index("-d") + 1],
    })
else:
    record("unexpected.jsonl", {"argv": argv})
"""

_RECORDER = """\
#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG_DIR/{name}"
"""


def defaults_of(role: str) -> dict[str, Any]:
    """A role's declared defaults as data, from defaults/main.yml or main/*.yml.

    Read, never rendered: a value that needs a lookup or hostvars stays a Jinja
    string, and a test that depends on it overrides it explicitly.
    """
    base = ROLES / role / "defaults"
    if (base / "main.yml").exists():
        return yaml.safe_load((base / "main.yml").read_text()) or {}
    merged: dict[str, Any] = {}
    for part in sorted((base / "main").glob("*.yml")):
        merged.update(yaml.safe_load(part.read_text()) or {})
    return merged


def render(text: str, context: dict[str, Any], *, lenient: bool = False) -> str:
    """Render a Jinja template with Ansible's filter plugins.

    Strict by default, so a template that reads a variable the test did not
    supply fails here instead of rendering as an empty string. Lenient is for
    whole-file consumers, where unrelated variables are not what is under test.
    """
    env = Environment(
        autoescape=False,
        keep_trailing_newline=True,
        undefined=ChainableUndefined if lenient else StrictUndefined,
    )
    env.filters.update(FilterModule().filters())
    return env.from_string(text).render(**context)


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def install_stubs(root: Path) -> dict[str, str]:
    """Put recording stubs for curl, python3 and logger first on PATH.

    Returns the environment a rendered script runs under. Only the stubs and the
    base system bins are on PATH, and every outbound call lands in a file under
    root/stub-log.
    """
    bin_dir = root / "stub-bin"
    log_dir = root / "stub-log"
    bin_dir.mkdir(parents=True)
    log_dir.mkdir()
    _executable(bin_dir / "curl", _CURL.replace("@PYTHON@", sys.executable))
    _executable(bin_dir / "python3", _RECORDER.format(name="python3.log"))
    _executable(bin_dir / "logger", _RECORDER.format(name="logger.log"))
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_LOG_DIR": str(log_dir)}


def queue_probe_codes(env: dict[str, str], codes: list[str]) -> None:
    """Queue HTTP statuses for the next health probes, in order."""
    with (Path(env["STUB_LOG_DIR"]) / "probe-codes").open("a") as handle:
        handle.writelines(f"{code}\n" for code in codes)


def records(env: dict[str, str], name: str) -> list[dict[str, Any]]:
    """The JSON records the curl stub wrote to one log, oldest first."""
    path = Path(env["STUB_LOG_DIR"]) / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def lines(env: dict[str, str], name: str) -> list[str]:
    """Lines a plain-text stub (logger, python3) recorded."""
    path = Path(env["STUB_LOG_DIR"]) / name
    return path.read_text().splitlines() if path.exists() else []


def headers_of(post: dict[str, Any]) -> dict[str, str]:
    """An ntfy publish's headers, split from the recorded 'Name: value' form."""
    return dict(header.split(": ", 1) for header in post["headers"])


def run_script(script: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute a rendered script the way systemd does: its shebang, its argv."""
    return subprocess.run(
        [str(script), *args], env=env, capture_output=True, text=True, timeout=60, check=False
    )
