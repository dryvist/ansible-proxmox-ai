"""Guard the managed Hindsight dependency and agent-bank boundaries.

Hindsight's plugin can try a lazy pip install when its client is absent, but
Hermes's uv venv has neither pip nor ensurepip. The role, not a guest-side
operator command, owns that dependency. These tests also pin the intended
topology: agents share one external Hindsight service while each identity has
its own bank; profiles inside an agent deliberately share that bank unless a
profile entry sets memory_bank_id.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
from jinja2 import Environment, StrictUndefined, Undefined
import yaml

from _role_files import role_defaults, role_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def _task(name: str) -> dict:
    return next(task for task in role_tasks(ROLE_ROOT) if task.get("name") == name)


def _verify_task(name: str) -> dict:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "verify.yml").read_text())
    return next(task for task in tasks[0]["block"] if task.get("name") == name)


def _render_hindsight_config_text(
    agent_id: str,
    *,
    mode: str = "local_external",
    include_api_key: bool = True,
    gateway_key: str = "",
    trust_tier: str = "trusted",
    profile_bank_id: str | None = None,
    locked: bool = False,
    template_source: str | None = None,
) -> str:
    source = template_source or (
        ROLE_ROOT / "templates" / "hindsight-config.json.j2"
    ).read_text()
    defaults = role_defaults(ROLE_ROOT)
    env = Environment(autoescape=False, undefined=StrictUndefined)
    env.filters["to_json"] = json.dumps
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, value
    )

    def mandatory(value, message):
        if isinstance(value, Undefined) or not str(value).strip():
            raise ValueError(message)
        return value

    env.filters["mandatory"] = mandatory
    env.filters["bool"] = bool
    gateway_key_field = agent_id.replace("-", "_") + "_agentgateway_key"
    context = {
        "ansible_managed": "managed",
        "hermes_agent_id": agent_id,
        "hermes_agent_is_isolated": agent_id == "pepper",
        "hermes_agent_memory_mode": mode,
        "hermes_agent_memory_api_url": "https://hindsight.example.test",
        "hermes_agent_memory_api_key": "fixture-hindsight-api-key",
        "hermes_agent_docs_mcp_url": "https://mcp.example.test/docs",
        "hermes_agent_bao": {gateway_key_field: gateway_key} if gateway_key else {},
        "ai_agent_gateway_key_fields": {agent_id: gateway_key_field},
        "ai_callers": {agent_id: {"trust_tier": trust_tier}},
        "hermes_agent_model_base_url": "https://llm.example.test/v1",
        "hermes_agent_memory_llm_model": "fixture-model",
        "hermes_agent_profile_locked": locked,
        "hermes_agent_locked_memory_recall_max_tokens": defaults[
            "hermes_agent_locked_memory_recall_max_tokens"
        ],
    }
    context["hermes_agent_memory_bank_id"] = env.from_string(
        str(defaults["hermes_agent_memory_bank_id"])
    ).render(**context)
    if not include_api_key:
        context.pop("hermes_agent_memory_api_key")
    if profile_bank_id is not None:
        context["hermes_agent_profile_memory_bank_id"] = profile_bank_id

    for name in (
        "hermes_agent_memory_gateway_key_field",
        "hermes_agent_memory_gateway_key",
        "hermes_agent_memory_gateway_tier",
        "hermes_agent_memory_gateway_api_url",
        "hermes_agent_memory_gateway_bank_id",
        "hermes_agent_hindsight_config_api_url",
        "hermes_agent_hindsight_config_api_key",
        "hermes_agent_hindsight_config_bank_id",
    ):
        context[name] = env.from_string(str(defaults[name])).render(**context)

    rendered = env.from_string(source).render(**context)
    return rendered


def _render_hindsight_config(
    agent_id: str,
    *,
    mode: str = "local_external",
    include_api_key: bool = True,
    gateway_key: str = "",
    trust_tier: str = "trusted",
    profile_bank_id: str | None = None,
    locked: bool = False,
) -> dict:
    return json.loads(
        _render_hindsight_config_text(
            agent_id,
            mode=mode,
            include_api_key=include_api_key,
            gateway_key=gateway_key,
            trust_tier=trust_tier,
            profile_bank_id=profile_bank_id,
            locked=locked,
        )
    )


def test_hindsight_client_is_managed_in_the_hermes_uv_venv() -> None:
    hindsight = next(
        plugin for plugin in role_defaults(ROLE_ROOT)["hermes_agent_plugins_core"] if plugin["name"] == "hindsight"
    )
    assert "'hindsight-client' ~ hermes_agent_hindsight_client_pin" in str(hindsight["requirements"])

    assert_tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "assert_plugins.yml").read_text())
    read_version = next(t for t in assert_tasks if t.get("name") == "Read the installed Hindsight client version from the Hermes venv")
    assert_pin = next(t for t in assert_tasks if t.get("name") == "Assert the Hermes venv has the pinned Hindsight client")
    assert read_version["ansible.builtin.command"]["argv"][0] == "{{ hermes_agent_venv_python }}"
    assert "version('hindsight-client')" in read_version["ansible.builtin.command"]["argv"][2]
    assert "hermes_agent_hindsight_client_version" in str(assert_pin)


def test_service_containment_selects_builtin_memory_without_disabling_core_files() -> None:
    defaults = role_defaults(ROLE_ROOT)
    provider_template = Environment().from_string(
        str(defaults["hermes_agent_memory_provider"])
    )
    provider_template.environment.filters["bool"] = lambda value: str(value).lower() in {"true", "1", "yes", "on"}
    assert provider_template.render().strip() == "builtin"
    assert provider_template.render(hindsight_service_enabled=False).strip() == "builtin"
    assert provider_template.render(hindsight_service_enabled=True).strip() == "hindsight"

    for template_name in ("config.yaml.j2", "config-profile.yaml.j2"):
        source = (ROLE_ROOT / "templates" / template_name).read_text()
        memory = source.split("memory:\n", 1)[1].split("\nlogging:", 1)[0]
        if template_name == "config.yaml.j2":
            assert "provider: {{ hermes_agent_memory_provider }}" in memory
            assert "memory_enabled: false" in memory
            assert "user_profile_enabled: false" in memory
        else:
            # Built-in memory is off for every profile; only a profile that
            # declares its own memory_bank_id (with the Hindsight service on)
            # renders the external provider, so the hindsight config
            # profiles.yml deploys for it is in use.
            assert "{% set _external_memory_on = _profile_memory_bank_id | length > 0 and hermes_agent_memory_provider == 'hindsight' %}" in source
            assert "{% set _profile_memory_bank_id = hermes_agent_profile.memory_bank_id | default('') | trim %}" in source
            assert "provider: {{ hermes_agent_memory_provider if _external_memory_on else 'builtin' }}" in memory
            assert "memory_enabled: false" in memory
            assert "user_profile_enabled: false" in memory


def test_builtin_provider_skips_every_hindsight_http_and_readiness_task() -> None:
    config_tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "config_and_content.yml").read_text())
    splunk_config = next(
        task
        for task in config_tasks
        if task.get("name") == "Manage Splunk memory bank extraction mode"
    )
    assert "hermes_agent_memory_provider == 'hindsight'" in splunk_config["when"]

    config_task_names = (
        "Ensure the hindsight config dir exists",
        "Deploy the hindsight provider config (mode + embedded-daemon LLM)",
    )
    for task_name in config_task_names:
        task = next(task for task in config_tasks if task.get("name") == task_name)
        assert task["when"] == "hermes_agent_memory_provider == 'hindsight'"

    verify = yaml.safe_load((ROLE_ROOT / "tasks" / "verify.yml").read_text())[0]["block"]
    recall = next(
        task
        for task in verify
        if task.get("name") == "Gate — Hindsight performs a read-only recall from this agent bank"
    )
    assert recall["when"] == "hermes_agent_memory_provider == 'hindsight'"


def test_provider_change_restarts_full_gateway_process() -> None:
    finalize = yaml.safe_load((ROLE_ROOT / "tasks" / "finalize.yml").read_text())
    stats = next(task for task in finalize if task.get("name") == "Stat the gateway input files")
    assert "config.yaml" in str(stats)
    restart = next(
        task
        for task in finalize
        if task.get("name") == "Restart a gateway whose inputs differ from the recorded digest"
    )
    assert restart["notify"] == "Restart hermes-gateway"
    handler = yaml.safe_load((ROLE_ROOT / "handlers" / "main.yml").read_text())
    gateway_restart = next(task for task in handler if task.get("name") == "Restart hermes-gateway")
    assert gateway_restart["ansible.builtin.command"] == "{{ hermes_agent_cron_drain_restart_script }}"


def test_hindsight_readiness_probe_is_read_only_and_uses_rendered_agent_config() -> None:
    probe = _verify_task("Gate — Hindsight performs a read-only recall from this agent bank")
    argv = probe["ansible.builtin.command"]["argv"]
    defaults = role_defaults(ROLE_ROOT)
    assert argv[:2] == ["timeout", "{{ hermes_agent_verify_hindsight_timeout }}"]
    assert defaults["hermes_agent_verify_hindsight_timeout"] == "60s"
    assert argv[2] == "{{ hermes_agent_venv_python }}"
    assert argv[3] == "-c"
    assert probe["retries"] == "{{ hermes_agent_verify_probe_retries }}"
    assert probe["delay"] == "{{ hermes_agent_verify_probe_delay }}"
    assert probe["until"] == "hermes_agent_hindsight_recall_probe.rc == 0"
    assert probe["check_mode"] is False
    assert probe["when"] == "hermes_agent_memory_provider == 'hindsight'"

    source = argv[4]

    assert "from hindsight_client import Hindsight" in source
    assert "hindsight/config.json" in source
    assert "client.arecall(" in source
    assert 'api_key=config["api_key"]' in source
    assert "bank_id=config[\"bank_id\"]" in source
    assert 'tags=["ansible-readiness-probe"]' in source
    assert 'tags_match="any_strict"' in source
    assert "retain" not in source.lower()


def test_agents_share_hindsight_service_but_not_memory_banks() -> None:
    defaults = role_defaults(ROLE_ROOT)
    hermes = _render_hindsight_config("hermes")
    donna = _render_hindsight_config("donna")

    bank_env = Environment()
    bank_env.filters["bool"] = bool
    bank_id = bank_env.from_string(defaults["hermes_agent_memory_bank_id"])
    for identity in ['hermes', 'hermes-private', 'donna']:
        assert bank_id.render(hermes_agent_id=identity, hermes_agent_is_isolated=False) == identity
    assert bank_id.render(hermes_agent_id='pepper', hermes_agent_is_isolated=True) == ''
    assert bank_id.render(hermes_agent_id='tracker-x', hermes_agent_is_isolated=True) == ''
    assert hermes["mode"] == donna["mode"] == "local_external"
    assert hermes["api_url"] == donna["api_url"]
    assert hermes["api_key"] == donna["api_key"] == "fixture-hindsight-api-key"
    assert hermes["bank_id"] == "hermes"
    assert donna["bank_id"] == "donna"
    assert hermes["bank_id"] != donna["bank_id"]


def test_an_unseeded_gateway_key_keeps_the_direct_config_values() -> None:
    config = _render_hindsight_config("hermes")

    assert config["api_url"] == "https://hindsight.example.test"
    assert config["api_key"] == "fixture-hindsight-api-key"
    assert config["bank_id"] == "hermes"


def test_gateway_config_uses_the_identity_key_and_tier_bank() -> None:
    config = _render_hindsight_config(
        "hermes-private",
        gateway_key="fixture-hermes-private-gateway-key",
        profile_bank_id="hermes-private-splunk",
    )

    assert config["api_url"] == "https://mcp.example.test/hindsight"
    assert config["api_key"] == "fixture-hermes-private-gateway-key"
    assert config["bank_id"] == "hermes-private-trusted"


def test_gateway_config_never_uses_the_hindsight_tenant_key() -> None:
    config = _render_hindsight_config(
        "hermes", gateway_key="fixture-hermes-gateway-key"
    )

    assert config["api_key"] == "fixture-hermes-gateway-key"
    assert config["api_key"] != "fixture-hindsight-api-key"


def test_a_profile_bank_overrides_the_agent_bank_only_when_set() -> None:
    assert _render_hindsight_config("hermes")["bank_id"] == "hermes"
    assert _render_hindsight_config("hermes", profile_bank_id="hermes-splunk")["bank_id"] == "hermes-splunk"


def test_locked_profile_uses_its_own_bank_with_context_auto_retain() -> None:
    common = _render_hindsight_config("hermes")
    config = _render_hindsight_config(
        "hermes", profile_bank_id="hermes-vikunja-clerk", locked=True
    )
    locked_values = {
        "auto_retain": True,
        "auto_recall": True,
        "memory_mode": "context",
        "recall_max_tokens": 1024,
        "recall_indicator": False,
        "retain_indicator": False,
    }

    assert config["bank_id"] == "hermes-vikunja-clerk"
    assert {key: config[key] for key in locked_values} == locked_values
    assert set(config) - set(common) == set(locked_values)

    deployment = _task("Deploy the hindsight provider config for profile {{ item.name }}")
    expected_when = "not (item.locked | default(false) | bool) or item.memory_bank_id is defined"
    assert deployment["vars"]["hermes_agent_profile_locked"] == (
        "{{ item.locked | default(false) | bool }}"
    )
    assert expected_when in deployment["when"]
    assert "hermes_agent_memory_provider == 'hindsight'" in deployment["when"]
    assert "item.memory_enabled | default(true) | bool" in deployment["when"]


def test_nonlocked_local_external_config_matches_develop_render_byte_for_byte() -> None:
    expected = (
        Path(__file__).parent / "fixtures" / "hindsight-config-nonlocked-develop.json"
    ).read_text()
    assert _render_hindsight_config_text("hermes") == expected


def test_a_gateway_key_refuses_a_profile_bank_instead_of_sharing_the_gateway_bank() -> None:
    check = _task("Refuse a profile memory bank that the agentgateway key would override")["ansible.builtin.assert"]["that"][0]
    env = Environment(undefined=StrictUndefined)

    def passes(item: dict, gateway_key: str) -> bool:
        return env.from_string("{{ " + check + " }}").render(
            item=item, hermes_agent_memory_gateway_key=gateway_key
        ) == "True"

    assert passes({"name": "homelab-admin", "memory_bank_id": "donna-homelab-admin"}, "")
    assert passes({"name": "assistant-pulse"}, "fixture-donna-gateway-key")
    assert not passes({"name": "homelab-admin", "memory_bank_id": "donna-homelab-admin"}, "fixture-donna-gateway-key")


def test_hindsight_config_enables_current_turn_recall_in_both_modes() -> None:
    assert _render_hindsight_config("hermes", mode="local_external")["recall_sync"] is True
    assert _render_hindsight_config("hermes", mode="local_embedded")["recall_sync"] is True


def test_local_external_hindsight_config_requires_api_key() -> None:
    try:
        _render_hindsight_config("hermes", include_api_key=False)
    except (jinja2.UndefinedError, ValueError):
        return
    raise AssertionError("Hermes Hindsight config rendered without an API key")
