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
import re
from pathlib import Path
from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = REPO_ROOT / "roles" / "hermes_agent"
TASKS_PATH = REPO_ROOT / "roles" / "hermes_agent"


def load_defaults():
    import yaml
    return role_defaults(DEFAULTS_PATH)


def test_every_disabled_direct_cron_has_a_recognised_pause_task():
    defaults = load_defaults()
    tasks = role_tasks_text(TASKS_PATH)

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
    # A third shape (native-cron reframe, "Pause the *-v2 crons the reframe
    # replaces 1-for-1"): `cron pause {{ item }}` looped over a literal list
    # of job-name strings, not a var or a dict field. Recognised the same
    # proxy way — a bare `cron pause {{ item }}` line followed by its own
    # `loop:` block of literal names.
    bare_loop = re.search(
        r"cron pause \{\{ item \}\}.*?\n\s*loop:\n((?:\s*-\s*\S+\n)+)", tasks, re.S)
    if bare_loop:
        paused |= {line.strip().lstrip("- ").strip() for line in bare_loop.group(1).splitlines()}

    missing = sorted(set(disabled) - paused)
    assert not missing, (
        f"direct cron(s) disabled in defaults with no `cron pause` task: {missing}. "
        "enabled:false only stops reconcile from re-creating the job — the cron "
        "already on the guest keeps firing until something pauses it.")


# test_a_retirement_that_names_a_card_leaves_that_card_able_to_run DELETED
# (native-cron reframe): `replaced_by_card` no longer exists anywhere in
# hermes_agent_direct_cron_jobs — confirmed by grepping defaults/main.yml for
# it (zero matches). Every retirement now that used to name a replacement
# kanban card instead replaces one -v2 direct-cron entry with a bare-named
# direct-cron entry of its own (see "Pause the *-v2 crons the reframe
# replaces 1-for-1" in tasks/main.yml); only docs-sync is still an actual
# Kanban card, and nothing retires in its favour. There is no
# machine-checkable "names a card" pairing left to pin.


# test_the_kanban_card_body_asks_for_a_full_report was removed:
# kanban-card-body.md.j2 no longer exists (18/18 native-cron reframe) and
# there is no shared wrapper/footer for direct-cron jobs at all —
# reconcile_direct_cron.yml passes each job's prompt_var/prompt_file content
# straight through unmodified. Whether a converted job's own prompt text still
# asks for a full report is therefore only checkable by reading that prompt,
# which for catalog-sourced (prompt_file) jobs lives in the external
# dryvist/ai-llm-prompts repo, not here.


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
