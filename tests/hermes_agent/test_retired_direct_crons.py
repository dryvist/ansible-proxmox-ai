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


def test_a_retirement_that_names_a_card_leaves_that_card_able_to_run():
    """`replaced_by_card` must name a card that is enabled and NOT paused.

    A paused job is skipped whole by reconcile_enqueuer_cron.yml AND rendered
    out of kanban-enqueue-recurring.sh.j2, so its enqueuer cron — which keeps
    running, because a paused job is never removed either — falls through to the
    script's `*)` arm and logs "unknown selector". The cron fires, nothing is
    created, and the board looks idle rather than broken.

    Caught for real: `homelab-ai-fabric-status` was in that state (paused, last
    card created 2026-07-24T12) while its `-v2` cron was being retired in
    favour of it. Retiring the cron without unpausing the card would have
    taken the topic to zero coverage with nothing in the repo saying so.
    """
    defaults = load_defaults()
    paused = set(defaults["hermes_agent_kanban_paused_jobs"])
    cards = {card["job"]: card for card in defaults["hermes_agent_kanban_cards"]}

    for job in defaults["hermes_agent_direct_cron_jobs"]:
        card_ref = job.get("replaced_by_card")
        if not card_ref:
            continue
        assert not job.get("enabled"), (
            f"{job['name']} names a replacement card but is still enabled")
        assert card_ref in cards, (
            f"{job['name']} is replaced by {card_ref}, which is not a kanban card")
        assert card_ref not in paused, (
            f"{job['name']} is retired in favour of the {card_ref} card, but that "
            "card is on hermes_agent_kanban_paused_jobs — its enqueuer would fire "
            "into the script's unknown-selector arm and create nothing, leaving "
            "the topic unreported by either path.")
        assert cards[card_ref].get("enabled"), (
            f"{card_ref} replaces {job['name']} but the card itself is disabled")


def test_the_card_footer_asks_for_a_full_report():
    """The precondition for every cadence retirement.

    A `-v2` cron posts a full report; its card posts whatever the shared footer
    asks for. While the footer asked for a one-line summary, switching a cron off
    silently downgraded that topic from a report to a sentence — so the footer
    wording is load-bearing, not cosmetic, and is checked here rather than
    remembered.
    """
    body = (REPO_ROOT / "roles/hermes_agent/templates"
            / "kanban-card-body.md.j2").read_text()
    assert "deliver a FULL REPORT to Slack" in body
    # kanban_complete's summary still has to be one line — the board digest
    # (kanban-digest.py) renders it as a single Slack bullet.
    assert "ONE-LINE summary" in body
    assert "post once, never twice" in body, (
        "a card whose own prompt posts a report must not also post the footer's")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
