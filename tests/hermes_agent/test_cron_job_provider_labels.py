"""Each model-backed direct cron uses a metadata-only Hermes provider."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment
from jinja2.nativetypes import NativeEnvironment

from _role_files import role_defaults
from test_context_limits import ROLE_ROOT, _render, _sample_limits


def _projection():
    defaults = role_defaults(ROLE_ROOT)
    env = NativeEnvironment()
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    env.filters["bool"] = bool  # the cron table's no_agent values are YAML booleans
    context = dict(defaults)
    jobs = []
    name_env = Environment()
    for entry in defaults["hermes_agent_direct_cron_jobs"]:
        name = name_env.from_string(entry["name"]).render(**defaults)
        jobs.append({"name": name, "no_agent": bool(entry.get("no_agent", False))})
    context["hermes_agent_direct_cron_jobs"] = jobs
    telemetry = env.from_string(defaults["hermes_agent_direct_cron_telemetry"]).render(**context)
    return defaults, jobs, telemetry, env


def _provider_expression() -> tuple[str, str]:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/reconcile_direct_cron.yml").read_text())
    resolver = next(task for task in tasks if task["name"].startswith("Direct cron | Resolve deliver target"))
    facts = resolver["ansible.builtin.set_fact"]
    return facts["hermes_agent_direct_model_provider"], facts["hermes_agent_direct_model"]


def test_every_model_backed_direct_cron_gets_a_metadata_only_provider():
    defaults, jobs, telemetry, _ = _projection()
    _, config = _render(
        "config.yaml.j2",
        _sample_limits(),
        hermes_agent_direct_cron_jobs=jobs,
        hermes_agent_direct_cron_telemetry=telemetry,
    )

    providers = {entry["name"]: entry for entry in config["custom_providers"]}
    assert providers["hermes-shared"] == {
        "name": "hermes-shared",
        "base_url": "https://llm.example.com/v1",
        "api_key": "${HERMES_AGENT_MODEL_API_KEY}",
    }
    agentic_jobs = [job for job in jobs if not job["no_agent"]]
    assert len(agentic_jobs) == len(telemetry)
    for job in agentic_jobs:
        provider_name = telemetry[job["name"]]["provider"]
        assert provider_name in providers, job["name"]
        provider = providers[provider_name]
        assert set(provider) == {"name", "base_url", "api_key", "extra_body"}
        assert provider["base_url"] == config["model"]["base_url"] == "https://llm.example.com/v1"
        assert provider["api_key"] == config["model"]["api_key"] == "${HERMES_AGENT_MODEL_API_KEY}"
        assert provider["extra_body"] == {"metadata": {"hermes_job": job["name"]}}

    provider_expression, model_expression = _provider_expression()
    task_env = Environment()
    task_env.filters["bool"] = bool
    render = task_env.from_string(provider_expression)
    render_model = task_env.from_string(model_expression)
    for job in agentic_jobs:
        selected = render.render(
            item=job,
            hermes_agent_direct_no_agent=False,
            hermes_agent_cron_model_provider="custom",
            hermes_agent_model_provider="custom",
            hermes_agent_direct_cron_telemetry=telemetry,
        )
        assert selected == f"custom:{telemetry[job['name']]['provider']}"
    assert render_model.render(
        item={"model": "job-selected-model"},
        hermes_agent_direct_no_agent=False,
        hermes_agent_cron_model="cron-default-model",
    ) == "job-selected-model"
    assert render_model.render(
        item={},
        hermes_agent_direct_no_agent=False,
        hermes_agent_cron_model="cron-default-model",
    ) == "cron-default-model"


def test_job_name_normalization_is_deterministic_and_prometheus_safe():
    defaults = role_defaults(ROLE_ROOT)
    env = NativeEnvironment()
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    env.filters["bool"] = bool
    mapping = env.from_string(defaults["hermes_agent_direct_cron_telemetry"]).render(
        hermes_agent_direct_cron_jobs=[{"name": "Mixed.job/Name"}, {"name": "script-only", "no_agent": True}]
    )
    assert "script-only" not in mapping

    assert mapping["Mixed.job/Name"] == {
        "provider": "hermes-cron-mixed_job_name",
        "tag": "telemetry_job_mixed_job_name",
    }
    assert re.fullmatch(r"telemetry_job_[a-z0-9_]+", mapping["Mixed.job/Name"]["tag"])


def test_openai_compatible_model_provider_is_declared_once() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_model_provider"] == "custom"

    for name in ("config.yaml.j2", "config-profile.yaml.j2", "config-public-gateway.yaml.j2"):
        template = (ROLE_ROOT / "templates" / name).read_text()
        assert "provider: {{ hermes_agent_model_shared_provider }}" in template
