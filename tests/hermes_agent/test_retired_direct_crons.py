"""Disabling a direct cron in defaults does not stop it on the host.

`reconcile_direct_cron.yml` gates every one of its tasks on `item.enabled`, so an
`enabled: false` entry is skipped whole — no create, and no remove or pause
either. The declaration stops reconcile from RE-creating the job; the cron that
already exists on the guest keeps firing on its old schedule, forever, with
nothing in the repo hinting that it is still live.

Every retirement so far has needed an explicit `cron pause` task alongside the
flag. This check makes that pairing mandatory instead of remembered.

Runs bare (`python3 tests/hermes_agent/test_retired_direct_crons.py`) or under
pytest.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = REPO_ROOT / "roles/hermes_agent/defaults/main.yml"
TASKS_PATH = REPO_ROOT / "roles/hermes_agent/tasks/main.yml"


def load_defaults():
    import yaml
    return yaml.safe_load(DEFAULTS_PATH.read_text())


def test_every_disabled_direct_cron_has_a_recognised_pause_task():
    defaults = load_defaults()
    tasks = TASKS_PATH.read_text()

    disabled = [job["name"] for job in defaults["hermes_agent_direct_cron_jobs"]
                if not job.get("enabled")]
    assert disabled, "no disabled direct crons — this check has nothing to guard"

    # The pause tasks name the job through a var, so resolve every *_cron_name
    # that a `cron pause` line references and compare on real names.
    #
    # ponytail: this certifies its property BY PROXY — it recognises syntactic
    # shapes, so it really means "a pause exists IN A SHAPE LISTED HERE". The
    # loop form below had to be added for exactly that reason after #188 moved
    # to it, and a THIRD shape will read as no pause at all the same way. When
    # that happens the fix is to add the shape, never to relax the assert.
    # (The proxy-free version — resolving what the play actually runs — means
    # executing Ansible, which is not worth it for a config-pairing check.)
    paused = {value for key, value in defaults.items()
              if isinstance(value, str)
              and f"cron pause {{{{ {key} }}}}" in tasks}
    # A pause can also be a loop over a list of jobs — `cron pause {{ item.X }}`
    # with `loop: {{ some_list }}`. Without this the check fails CLOSED on a
    # correct retirement, which is the worst direction for a guard meant to
    # catch the ones people forget.
    paused |= {job[field]
               for jobs in defaults.values() if isinstance(jobs, list)
               for job in jobs if isinstance(job, dict)
               for field in job
               if f"cron pause {{{{ item.{field} }}}}" in tasks}

    missing = sorted(set(disabled) - paused)
    assert not missing, (
        f"direct cron(s) disabled in defaults with no `cron pause` task: {missing}. "
        "enabled:false only stops reconcile from re-creating the job — the cron "
        "already on the guest keeps firing until something pauses it.")


if __name__ == "__main__":
    test_every_disabled_direct_cron_has_a_recognised_pause_task()
    print("ok  test_every_disabled_direct_cron_has_a_recognised_pause_task")
