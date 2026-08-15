"""An enqueuer cron the agent identity does not own must be actively removed.

`reconcile_enqueuer_cron.yml` gates its create/drift path on `item.enabled`, and
each script-fed job ANDs `hermes_agent_ops_workload_enabled` into that gate. Those
two conditions collapse into one boolean, so the file cannot tell "my credential
is missing" apart from "this identity does not own this job" — and its
disable-don't-delete rule, correct for the first case, is wrong for the second.
An identity that already had the job installed keeps firing it forever.

The withdrawal task closes that. This check keeps it in place, and keeps it keyed
on ownership rather than on the collapsed gate: re-adding the active gate to the
removal path would silently restore the original hole while every task still
looks present.

ponytail: certifies BY PROXY — it recognises the shape of the tasks, not what
Ansible actually runs. A future refactor into a different shape reads as "no
withdrawal" the same way. When that happens, add the shape; never relax the
assert.

Runs bare (`python3 tests/hermes_agent/test_unowned_enqueuer_withdrawal.py`) or
under pytest.
"""
import re
from pathlib import Path
from _role_files import role_defaults_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ENQUEUER_PATH = REPO_ROOT / "roles/hermes_agent/tasks/reconcile_enqueuer_cron.yml"
DEFAULTS_PATH = REPO_ROOT / "roles" / "hermes_agent"

OWNERSHIP_GATE = "hermes_agent_ops_workload_enabled"


def _tasks(text):
    """Split the file into (name, body) per task, in file order."""
    parts = re.split(r"^- name:\s*", text, flags=re.M)[1:]
    out = []
    for p in parts:
        name, _, body = p.partition("\n")
        out.append((name.strip().strip('"'), body))
    return out


def test_a_withdrawal_task_exists_and_is_keyed_on_ownership():
    tasks = _tasks(ENQUEUER_PATH.read_text())
    removals = [(n, b) for n, b in tasks
                if "cron remove" in b and "not owned by this agent" in n]
    assert removals, (
        "reconcile_enqueuer_cron.yml has no 'not owned by this agent' removal "
        "task; an unowned enqueuer cron would stay installed and keep firing"
    )

    for name, body in removals:
        assert f"not ({OWNERSHIP_GATE} | bool)" in body, (
            f"{name!r} must gate on {OWNERSHIP_GATE} directly — gating on the "
            "collapsed item.enabled cannot distinguish an unowned job from one "
            "whose capability gate is merely off"
        )
        # The whole point is that this fires when the active gate is FALSE.
        assert "hermes_agent_enqueuer_active | bool" not in body.split("when:")[-1], (
            f"{name!r} must not require hermes_agent_enqueuer_active; that is "
            "exactly the condition that is false for a job this agent does not own"
        )


def test_the_cron_listing_runs_for_unowned_jobs_too():
    """The removal needs something to compare against."""
    tasks = _tasks(ENQUEUER_PATH.read_text())
    lookups = [(n, b) for n, b in tasks if "cron list --all" in b]
    assert lookups, "no 'cron list --all' lookup task found"

    name, body = lookups[0]
    when = body.split("when:")[-1]
    assert OWNERSHIP_GATE in when, (
        f"{name!r} must also run when the job is unowned, otherwise the "
        "withdrawal task has no listing to test against and silently no-ops"
    )
    assert "changed_when: false" in body, f"{name!r} must stay read-only"


def test_the_defaults_comment_does_not_claim_enabled_false_removes():
    """The comment previously asserted behaviour the code did not implement."""
    text = role_defaults_text(DEFAULTS_PATH)
    idx = text.find(f"{OWNERSHIP_GATE}:")
    assert idx != -1, f"{OWNERSHIP_GATE} not found in defaults"
    preamble = text[max(0, idx - 1200):idx]
    assert not re.search(r"already REMOVES a job whose `enabled` gate\s*\n?#?\s*is false",
                         preamble), (
        "the comment above " + OWNERSHIP_GATE + " claims an enabled:false gate "
        "removes the job. Removal is keyed on ownership, not on the collapsed "
        "gate; a capability-disabled job is deliberately left installed."
    )


if __name__ == "__main__":
    test_a_withdrawal_task_exists_and_is_keyed_on_ownership()
    test_the_cron_listing_runs_for_unowned_jobs_too()
    test_the_defaults_comment_does_not_claim_enabled_false_removes()
    print("ok")
