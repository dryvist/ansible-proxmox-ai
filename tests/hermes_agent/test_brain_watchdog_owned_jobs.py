"""The brain watchdog watches only the crons its own identity runs.

The watchdog reads every listed name back from the guest's cron board and
counts a missing one as a failure, so another identity's job in its list would
report a failed pause or resume on every transition. This drives the real
expressions (hermes_agent_direct_cron_jobs_owned and
hermes_agent_brain_dependent_cron_names) through Jinja.
"""

from __future__ import annotations

import ast
from pathlib import Path

from jinja2 import Environment
from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
DIGEST = DEFAULTS["hermes_agent_splunk_status_digest_cron_name"]


def _render(env: Environment, text: str, ctx: dict):
    return ast.literal_eval(env.from_string(text).render(ctx))


def _watched(identity: str, owners: dict | None = None) -> set[str]:
    env = Environment(autoescape=False)  # noqa: S701
    env.filters["bool"] = bool
    env.tests["contains"] = lambda seq, item: item in seq
    ctx = dict(DEFAULTS, hermes_agent_id=identity, ai_agent_owner=owners or {})
    ctx["hermes_agent_direct_cron_jobs_owned"] = _render(env, DEFAULTS["hermes_agent_direct_cron_jobs_owned"], ctx)
    ctx["hermes_agent_seeded_cron_names"] = [DIGEST]
    names = _render(env, DEFAULTS["hermes_agent_brain_dependent_cron_names"], ctx)
    return {env.from_string(name).render(DEFAULTS) for name in names}


def _owners_of(name: str) -> set[str]:
    env = Environment(autoescape=False)  # noqa: S701
    return {
        identity
        for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
        if env.from_string(job["name"]).render(DEFAULTS) == name
        for identity in job.get("identities", DEFAULTS["hermes_agent_job_identities_default"])
    }


def test_each_identity_watches_only_its_own_jobs() -> None:
    for identity in ("donna", "hermes-private", "pepper", "hermes"):
        watched = _watched(identity) - {DIGEST}
        assert watched, identity
        for name in watched:
            assert identity in _owners_of(name), (identity, name)


def test_other_identities_jobs_stay_off_the_assistant_list() -> None:
    donna = _watched("donna")
    assert {"private-inbox-groom", "private-evening-wrap", "ai-news-hourly"}.isdisjoint(donna)
    assert "private-inbox-groom" in _watched("hermes-private")


def test_seeded_digest_follows_its_owner() -> None:
    assert DIGEST in _watched("hermes")
    assert DIGEST not in _watched("donna")
    owners = {"splunk": "splunk-heimdall"}
    assert DIGEST in _watched("splunk-heimdall", owners)
    assert DIGEST not in _watched("hermes", owners)
