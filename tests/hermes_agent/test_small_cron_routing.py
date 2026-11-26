"""Only the light direct crons select the small lane."""

from __future__ import annotations

from jinja2 import Environment

from _registry import load_registry
from _role_files import direct_plan_var, role_defaults
from test_context_limits import ROLE_ROOT

EXPECTED_SMALL_CRONS = {
    "service-pulse",
}


def test_only_selected_direct_crons_render_the_small_lane() -> None:
    defaults = role_defaults(ROLE_ROOT)
    model_expression = direct_plan_var(ROLE_ROOT, "hermes_agent_direct_model")
    env = Environment()
    env.filters["bool"] = bool
    render_model = env.from_string(model_expression)
    rendered_models = []

    for job in defaults["hermes_agent_direct_cron_jobs"]:
        name = env.from_string(job["name"]).render(**defaults)
        model_value = job.get("model")
        if isinstance(model_value, str) and "{{" in model_value:
            model_value = env.from_string(model_value).render(**defaults)
        model = render_model.render(
            item={**job, **({"model": model_value} if model_value is not None else {})},
            hermes_agent_direct_no_agent=bool(job.get("no_agent", False)),
            hermes_agent_cron_model="medium",
        )
        rendered_models.append((name, model))

    # The lane is read from the cron's own declaration, never re-typed: the
    # registry single-source scan forbids registry values as test literals.
    small_lane = dict(rendered_models)[next(iter(EXPECTED_SMALL_CRONS))]
    assert small_lane in {entry.get("route_group") for entry in load_registry()}
    small_crons = [name for name, model in rendered_models if model == small_lane]
    assert len(small_crons) == len(EXPECTED_SMALL_CRONS)
    assert set(small_crons) == EXPECTED_SMALL_CRONS
