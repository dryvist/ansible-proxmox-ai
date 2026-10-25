"""The Browser Use gate's page markers survive upstream copy edits."""

from pathlib import Path

import pytest
import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles/hermes_agent/tasks/verify_browser_use.yml"
PROBE = "Gate — Browser Use reads Cribl global-secrets documentation"

OLD_PAGE = "Global secrets\nReference a secret as <secretFolderId>/<globalSecretId> in fields."
NEW_PAGE = "Add, Use, and Deploy Global Secrets\nReference a secret as secretFolderId/globalSecretId."


def _probe_assertions():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    script = next(t for t in tasks if t.get("name") == PROBE)["ansible.builtin.shell"]
    body = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    return [line.strip() for line in body.splitlines() if line.strip().startswith("assert ")]


@pytest.mark.parametrize("text", [OLD_PAGE, NEW_PAGE], ids=["old-copy", "new-copy"])
def test_probe_assertions_accept_both_page_wordings(text):
    assertions = _probe_assertions()
    assert len(assertions) == 2
    for line in assertions:
        exec(line, {"text": text})  # noqa: S102 - evaluates the gate's own assert lines


def test_probe_rejects_an_unrelated_page():
    with pytest.raises(AssertionError):
        for line in _probe_assertions():
            exec(line, {"text": "Page not found"})  # noqa: S102
