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


def test_every_disabled_direct_cron_is_also_paused_on_the_host():
    defaults = load_defaults()
    tasks = TASKS_PATH.read_text()

    disabled = [job["name"] for job in defaults["hermes_agent_direct_cron_jobs"]
                if not job.get("enabled")]
    assert disabled, "no disabled direct crons — this check has nothing to guard"

    # The pause tasks name the job through a var, so resolve every *_cron_name
    # that a `cron pause` line references and compare on real names.
    paused = {value for key, value in defaults.items()
              if isinstance(value, str)
              and f"cron pause {{{{ {key} }}}}" in tasks}

    missing = sorted(set(disabled) - paused)
    assert not missing, (
        f"direct cron(s) disabled in defaults with no `cron pause` task: {missing}. "
        "enabled:false only stops reconcile from re-creating the job — the cron "
        "already on the guest keeps firing until something pauses it.")


if __name__ == "__main__":
    test_every_disabled_direct_cron_is_also_paused_on_the_host()
    print("ok  test_every_disabled_direct_cron_is_also_paused_on_the_host")
