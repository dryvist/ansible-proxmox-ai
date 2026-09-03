"""Self-check for the failed-run artifact prompt hash patch
(patches_cron_failure_artifact.yml). Runs bare or under pytest."""
import hashlib
import runpy
import tempfile
from pathlib import Path

from _pinned_sources import PINNED_CRON_FAILURE_ARTIFACT_SOURCE
from conftest import _apply_runtime_patch

NAME = "Hash the prompt in a failed cron run's artifact"
SUCCESS_ARTIFACT = "## Prompt\n\n{prompt}\n\n## Response\n"


def _render(patched: str, prompt: str) -> str:
    """Render the patched section under an f-string, as upstream's writer does."""
    path = Path(tempfile.mkdtemp(prefix="cron-artifact-selfcheck-")) / "render.py"
    path.write_text("ARTIFACT = f'''" + patched + "'''\n")
    return runpy.run_path(str(path), init_globals={"prompt": prompt}, run_name="render")["ARTIFACT"]


def test_the_failure_artifact_prompt_becomes_a_hash_and_a_length():
    patched = _apply_runtime_patch(NAME, PINNED_CRON_FAILURE_ARTIFACT_SOURCE)
    assert "## Prompt\n\nsha256:{__import__(\"hashlib\")" in patched
    assert "## Error" in patched and "{prompt}\n\n## Error" not in patched
    prompt = "x" * 15000
    rendered = _render(patched, prompt)
    assert hashlib.sha256(prompt.encode()).hexdigest() in rendered
    assert "15000 chars" in rendered and prompt not in rendered


def test_the_success_artifact_keeps_its_prompt():
    try:
        _apply_runtime_patch(NAME, SUCCESS_ARTIFACT)
    except AssertionError:
        return
    raise AssertionError("the success artifact must not match the failure anchor")


if __name__ == "__main__":
    test_the_failure_artifact_prompt_becomes_a_hash_and_a_length()
    test_the_success_artifact_keeps_its_prompt()
    print("all checks passed")
