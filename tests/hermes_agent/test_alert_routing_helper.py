"""Self-check for the runtime cron-route failure-routing helper, executed as
shipped.

Split from test_alert_routing.py to stay under the token budget — see
_alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_channels.py for the four-way channel contract, and
test_alert_routing_jobs.py for the per-job direct-cron routing contract this
leaves behind.

The block below is patched into upstream's scheduler at converge. Rendering
and running it here means these assertions exercise the real function, not a
restatement of it.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_helper.py`) or under
pytest.
"""

from __future__ import annotations

import re

from _alert_routing_shared import ROLE, _ENV
from _role_files import role_tasks


def _load_route_helper(issues_target: str, marker: str = "[ISSUES]"):
    import logging
    import types

    tasks = role_tasks(ROLE)
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in tasks
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    rendered = _ENV.from_string(block).render(
        hermes_agent_cron_failure_deliver=issues_target,
        hermes_agent_cron_issues_marker=marker,
    )
    mod = types.ModuleType("cron_guard")
    mod.__dict__.update(re=re, logger=logging.getLogger("test"))
    exec(compile(rendered, "cron-guard", "exec"), mod.__dict__)  # noqa: S102
    return mod


def test_a_successful_run_keeps_its_own_channel() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, content = mod._cron_route(job, True, "12 indexes healthy")
    assert routed["deliver"] == "slack:C_SPLUNK"
    assert content == "12 indexes healthy"


def test_a_successful_run_reporting_an_observed_error_is_not_rerouted() -> None:
    """The regression a keyword rule would cause. A Splunk-observed litellm
    error on the llm-routers is a finding, produced by a job that worked — the
    single most valuable message shape in the audited corpus."""
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, _ = mod._cron_route(
        job, True, "litellm.RateLimitError on llm-router-2 — 52 events, up from 18")
    assert routed["deliver"] == "slack:C_SPLUNK", "an observed error is a finding"


def test_a_failed_run_is_rerouted_to_the_issues_channel() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, _ = mod._cron_route(job, False, "Cron failed: litellm.BadRequestError")
    assert routed["deliver"] == "slack:C_ISSUES"


def test_a_script_that_declares_its_own_failure_is_rerouted_and_unmarked() -> None:
    """Script-fed crons exit 0 and print their failure, so `success` is True."""
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "kanban-digest", "deliver": "slack:C_ALL"}
    routed, content = mod._cron_route(
        job, True, "[ISSUES] :warning: Splunk digest FAILED: 401 Unauthorized")
    assert routed["deliver"] == "slack:C_ISSUES"
    assert content.startswith(":warning:"), "the marker must never reach Slack"
    assert "[ISSUES]" not in content


def test_routing_never_mutates_the_callers_job() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "j", "deliver": "slack:C_ALL"}
    mod._cron_route(job, False, "boom")
    assert job["deliver"] == "slack:C_ALL", "the scheduler reuses this dict"


def test_an_unset_issues_target_leaves_every_result_where_it_was() -> None:
    mod = _load_route_helper("")
    job = {"id": "j", "deliver": "slack:C_ALL"}
    routed, content = mod._cron_route(job, False, "[ISSUES] boom")
    assert routed["deliver"] == "slack:C_ALL"
    assert content == "boom", "the marker is stripped even when routing is off"


def test_the_marker_has_one_definition_shared_by_producer_and_consumer() -> None:
    """Two hard-coded copies would drift and silently stop routing."""
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in role_tasks(ROLE)
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    assert "{{ hermes_agent_cron_issues_marker }}" in block
    for tpl in ("kanban-digest.py.j2", "splunk-digest.py.j2",
                "splunk-triage.py.j2", "zammad-auto-close.py.j2"):
        src = (ROLE / "templates" / tpl).read_text()
        assert 'ISSUES_MARKER = "{{ hermes_agent_cron_issues_marker }}"' in src, tpl
        assert "{ISSUES_MARKER}" in src, f"{tpl} declares the marker but never emits it"


# --- escalate-then-quiet ladder for repeated runner failures (Vikunja 1858/1853)


def _failed(mod, streak_before: int):
    job = {"id": "zammad-review", "deliver": "slack:C_ALL", "failure_streak": streak_before}
    return mod._cron_route(job, False, ":warning: Cron 'zammad-review' failed: HTTP 502")


def test_the_first_failure_posts_to_the_issues_channel() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    routed, _ = _failed(mod, 0)
    assert routed["deliver"] == "slack:C_ISSUES"


def test_a_failure_off_the_ladder_is_recorded_but_not_posted() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    for streak_before in (1, 3, 4, 8, 10, 30, 48):
        routed, content = _failed(mod, streak_before)
        assert routed["deliver"] == "local", streak_before
        assert "HTTP 502" in content


def test_the_ladder_rungs_and_every_fiftieth_failure_post() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    for streak_before in (2, 9, 49, 99):  # this run is the 3rd, 10th, 50th, 100th
        routed, _ = _failed(mod, streak_before)
        assert routed["deliver"] == "slack:C_ISSUES", streak_before


def test_a_missing_or_garbage_streak_counts_as_the_first_failure() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    for job in (
        {"id": "j", "deliver": "slack:C"},
        {"id": "j", "deliver": "slack:C", "failure_streak": "n/a"},
    ):
        routed, _ = mod._cron_route(job, False, "failed")
        assert routed["deliver"] == "slack:C_ISSUES"


def test_a_script_declared_failure_is_never_ladder_gated() -> None:
    # A script-fed cron ran successfully and REPORTED its own failure; it has
    # no runner streak, so every declaration reaches the issues channel.
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "wired-trajectory-watch", "deliver": "slack:C_SPLUNK", "failure_streak": 7}
    routed, content = mod._cron_route(job, True, "[ISSUES] splunk 401")
    assert routed["deliver"] == "slack:C_ISSUES"
    assert content == "splunk 401"


def test_the_cron_delivery_wrapper_is_off_in_the_rendered_config() -> None:
    # The header/footer upstream wraps around every delivered run is a native
    # knob, not a patch; the template must keep it off.
    from _role_files import template_text

    text = template_text(ROLE, "config.yaml.j2")
    assert re.search(r"^cron:\n(?:  .*\n)*?  wrap_response: false$", text, re.MULTILINE)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
