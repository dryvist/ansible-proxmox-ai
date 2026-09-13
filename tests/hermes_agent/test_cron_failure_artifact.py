"""Self-check for the failed-run artifact prompt hash patch
(patches_cron_failure_artifact.yml). Runs bare or under pytest."""
import hashlib
import textwrap

from conftest import _apply_runtime_patch

# Re-anchored (2026-09): upstream centralized the artifact header behind a
# shared _run_doc_header(job, title, job_id, prompt) call — the failure and
# success paths are now two call sites passing different `title` text,
# rather than two separate "## Prompt...{prompt}..." template strings. The
# patch wraps the failure call site's own `prompt` argument, distinguished
# from the success call site by the `f"{job_name} (FAILED)"` title.
PINNED_CRON_FAILURE_ARTIFACT_SOURCE = (
    '                    _run_doc_header(job, f"{job_name} (FAILED)", job_id, prompt)\n'
)

NAME = "Hash the prompt in a failed cron run's artifact"
SUCCESS_ARTIFACT = '                _run_doc_header(job, job_name, job_id, prompt)\n'


def _rendered_prompt_arg(patched: str, prompt: str) -> str:
    """Exec the patched call site against a stub _run_doc_header and capture
    the argument it actually received in the `prompt` position."""
    calls: list[str] = []
    namespace = {
        "_run_doc_header": lambda job, title, job_id, prompt_arg: calls.append(prompt_arg),
        "job": {},
        "job_name": "x",
        "job_id": "j1",
        "prompt": prompt,
    }
    exec(textwrap.dedent(patched), namespace)  # noqa: S102 - test-only, self-authored source
    return calls[0]


def test_the_failure_artifact_prompt_becomes_a_hash_and_a_length():
    patched = _apply_runtime_patch(NAME, PINNED_CRON_FAILURE_ARTIFACT_SOURCE)
    assert 'sha256:{__import__("hashlib")' in patched
    prompt = "x" * 15000
    rendered = _rendered_prompt_arg(patched, prompt)
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
