"""model-routing-review belongs to the donna identity only."""

from __future__ import annotations

import ast
from pathlib import Path

from jinja2 import Environment
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"


def _owned_job_names(identity: str) -> set[str]:
    defaults = role_defaults(ROLE)
    env = Environment(autoescape=False)  # noqa: S701
    env.filters["bool"] = bool
    env.tests["contains"] = lambda seq, item: item in seq
    ctx = dict(defaults)
    ctx["hermes_agent_id"] = identity
    ctx["hermes_agent_ops_workload_enabled"] = (
        identity in defaults["hermes_agent_job_identities_default"]
    )
    owned = env.from_string(defaults["hermes_agent_direct_cron_jobs_owned"]).render(
        ctx
    )
    return {
        env.from_string(job["name"]).render(defaults)
        for job in ast.literal_eval(owned)
    }


def test_model_routing_review_is_owned_by_donna_not_hermes() -> None:
    assert "model-routing-review" in _owned_job_names("donna")
    assert "model-routing-review" not in _owned_job_names("hermes")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
