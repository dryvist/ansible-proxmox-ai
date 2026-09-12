"""Self-check for the runtime cron-route self-declared-failure helper,
executed as shipped.

Split from test_alert_routing.py to stay under the token budget — see
_alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_channels.py for the four-way channel contract, and
test_alert_routing_jobs.py for the per-job direct-cron routing contract this
leaves behind.

_cron_route() used to also reroute a RUNNER-reported failure/success to a
channel by success flag and content sniffing; that half is retired
(patches_cron_failure_routing.yml, 2026-09) because upstream's own
`_deliver_result(..., for_failure=not d.success)` already reads a job's
`failure_deliver` field for that case. What _cron_route still does, and all
these tests cover, is strip a script-fed job's self-declared failure marker
and apply the escalate-then-quiet ladder to whether that declaration gets
posted — it takes `(job, content)` and returns `(job, content, declared)`.

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


def _load_route_helper(marker: str = "[ISSUES]"):
    import logging
    import types

    tasks = role_tasks(ROLE)
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in tasks
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    rendered = _ENV.from_string(block).render(hermes_agent_cron_issues_marker=marker)
    mod = types.ModuleType("cron_guard")
    mod.__dict__.update(re=re, logger=logging.getLogger("test"))
    exec(compile(rendered, "cron-guard", "exec"), mod.__dict__)  # noqa: S102
    return mod


def test_undeclared_content_passes_through_unmarked() -> None:
    mod = _load_route_helper()
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, content, declared = mod._cron_route(job, "12 indexes healthy")
    assert routed is job
    assert content == "12 indexes healthy"
    assert declared is False


def test_undeclared_content_is_never_keyword_matched() -> None:
    """The regression a keyword rule would cause. A Splunk-observed litellm
    error on the llm-routers is a finding, produced by a job that worked — the
    single most valuable message shape in the audited corpus. Only the
    marker, never content sniffing, may declare a failure."""
    mod = _load_route_helper()
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    _, _, declared = mod._cron_route(
        job, "litellm.RateLimitError on llm-router-2 — 52 events, up from 18")
    assert declared is False, "an observed error is a finding, not a declaration"


def test_a_script_that_declares_its_own_failure_is_marked_and_unmarked() -> None:
    """Script-fed crons exit 0 and print their failure with the marker."""
    mod = _load_route_helper()
    job = {"id": "kanban-digest", "deliver": "slack:C_ALL"}
    routed, content, declared = mod._cron_route(
        job, "[ISSUES] :warning: Splunk digest FAILED: 401 Unauthorized")
    assert declared is True
    assert routed["deliver"] == "slack:C_ALL", "delivery target is upstream's job, not this one"
    assert content.startswith(":warning:"), "the marker must never reach Slack"
    assert "[ISSUES]" not in content


def test_routing_never_mutates_the_callers_job() -> None:
    mod = _load_route_helper()
    job = {"id": "j", "deliver": "slack:C_ALL"}
    mod._cron_route(job, "boom")
    assert job["deliver"] == "slack:C_ALL", "the scheduler reuses this dict"


def test_the_marker_has_one_definition_shared_by_producer_and_consumer() -> None:
    """Two hard-coded copies would drift and silently stop routing."""
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in role_tasks(ROLE)
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    assert "{{ hermes_agent_cron_issues_marker }}" in block
    for tpl in ("kanban-digest.py.j2", "splunk-digest.py.j2",
                "splunk-triage.py.j2"):
        src = (ROLE / "templates" / tpl).read_text()
        assert 'ISSUES_MARKER = "{{ hermes_agent_cron_issues_marker }}"' in src, tpl
        assert "{ISSUES_MARKER}" in src, f"{tpl} declares the marker but never emits it"


# --- escalate-then-quiet ladder for repeated self-declared failures (1858/1853)


def _declared(mod, streak_before: int):
    job = {"id": "zammad-review", "deliver": "slack:C_ALL", "failure_streak": streak_before}
    return mod._cron_route(job, "[ISSUES] :warning: Cron 'zammad-review' failed: HTTP 502")


def test_the_first_declared_failure_stays_on_the_ladder() -> None:
    mod = _load_route_helper()
    routed, _, declared = _declared(mod, 0)
    assert declared is True
    assert "failure_deliver" not in routed, "on-ladder: no forced local opt-out"


def test_a_declared_failure_off_the_ladder_is_recorded_but_not_posted() -> None:
    mod = _load_route_helper()
    for streak_before in (1, 3, 4, 8, 10, 30, 48):
        routed, content, declared = _declared(mod, streak_before)
        assert declared is True, streak_before
        assert routed["failure_deliver"] == "local", streak_before
        assert "HTTP 502" in content


def test_the_ladder_rungs_and_every_fiftieth_declared_failure_stay_on() -> None:
    mod = _load_route_helper()
    for streak_before in (2, 9, 49, 99):  # this run is the 3rd, 10th, 50th, 100th
        routed, _, declared = _declared(mod, streak_before)
        assert declared is True, streak_before
        assert "failure_deliver" not in routed, streak_before


def test_a_missing_or_garbage_streak_counts_as_the_first_failure() -> None:
    mod = _load_route_helper()
    for job in (
        {"id": "j", "deliver": "slack:C"},
        {"id": "j", "deliver": "slack:C", "failure_streak": "n/a"},
    ):
        routed, _, declared = mod._cron_route(job, "[ISSUES] failed")
        assert declared is True
        assert "failure_deliver" not in routed


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
