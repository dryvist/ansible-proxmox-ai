"""The default-off facade selector preserves legacy renders and fails closed."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from test_owned_mcp_servers import (
    GATEWAY_KEY,
    _config_context,
    _env_owner_context,
    _mcp_servers,
)
from test_profile_contract import REPO_ROOT, ROLE_ROOT, _defaults, _jinja_env
from test_profile_env_parity import _env_values, _jinja_env as _env_jinja_env


# Frozen render hashes generated from base 758e6a13 using the synthetic
# contexts constructed below. Keeping only hashes makes this test work in
# shallow PR checkouts without fetching historical objects.
BASELINE_SHA256 = {
    "config.yaml.j2": "5f328b2841cfa64ef66fa6bc30d04ec382ef58ae9696bed360bb172dfd9300b9",
    "config-profile.yaml.j2": "66ab212683331808198f42d51202cee50bea5bca2d0141c274b7959706516d13",
    "hermes-env-profile.j2": "04c8fcbe5a77509d9928afe99c4abfc0018f07aead42a63128937359c7f5254b",
    "hermes-env.j2": "bb16289ad55332b18ea80f895ecb6b9faa467ba7ee188419e3905bc37355662c",
}
# Baseline inputs are _config_context/_env_owner_context for example-agent
# with an empty MCP owner map; profile templates also receive PROFILE below.
PROFILE = {
    "name": "orchestrator-fixture",
    "description": "synthetic bounded canary",
    "mcp": ["vikunja", "docs", "grep"],
    "env": [],
}
# Canary renders use the existing caller field shape with a synthetic value.
SYNTHETIC_ID = "synthetic-caller"
SYNTHETIC_FIELD = "synthetic_caller_agentgateway_key"
SYNTHETIC_KEY = "fixture-only-gateway-key"
def _profile_config(profile: dict, *, enabled: bool = False) -> str:
    context = _config_context("example-agent", owner_map={})
    context.update(
        hermes_agent_profile=profile,
        hermes_agent_facade_canary_enabled=enabled,
        hermes_agent_facade_base_url="https://mcp.example.test",
        # The facade URL comes from its explicit base, never by rewriting this.
        hermes_agent_vikunja_mcp_url="https://other.example.test/vikunja/custom",
    )
    return _jinja_env().get_template("config-profile.yaml.j2").render(**context)


def _assert_base_render(template_name: str, rendered: str) -> None:
    assert hashlib.sha256(rendered.encode()).hexdigest() == BASELINE_SHA256[template_name]


def _without_facade_inputs(context: dict) -> dict:
    legacy_context = dict(context)
    legacy_context.pop("hermes_agent_facade_canary_enabled", None)
    legacy_context.pop("hermes_agent_facade_base_url", None)
    return legacy_context


def test_default_off_renders_match_base_for_default_and_named_profiles() -> None:
    assert _defaults()["hermes_agent_facade_canary_enabled"] is False
    assert all("facade_canary" not in profile for profile in _defaults()["hermes_agent_profiles"])

    default_context = _without_facade_inputs(
        _config_context("example-agent", owner_map={})
    )
    default_env = _jinja_env()
    _assert_base_render(
        "config.yaml.j2",
        default_env.get_template("config.yaml.j2").render(**default_context),
    )

    profile_context = _without_facade_inputs(
        _config_context("example-agent", owner_map={})
    )
    profile_context.update(
        hermes_agent_profile=PROFILE,
    )
    profile_env = _jinja_env()
    _assert_base_render(
        "config-profile.yaml.j2",
        profile_env.get_template("config-profile.yaml.j2").render(
            **profile_context
        ),
    )

    profile_secrets_context = _without_facade_inputs(
        _env_owner_context("example-agent", owner_map={})
    )
    profile_secrets_context.update(
        hermes_agent_profile=PROFILE,
    )
    profile_secrets_env = _env_jinja_env()
    _assert_base_render(
        "hermes-env-profile.j2",
        profile_secrets_env.get_template("hermes-env-profile.j2").render(
            **profile_secrets_context
        ),
    )

    default_secrets_context = _without_facade_inputs(
        _env_owner_context("example-agent", owner_map={})
    )
    default_secrets_env = _env_jinja_env()
    _assert_base_render(
        "hermes-env.j2",
        default_secrets_env.get_template("hermes-env.j2").render(
            **default_secrets_context
        ),
    )


def test_enabled_selector_replaces_default_and_profile_mcp_sets() -> None:
    default_context = _config_context("example-agent", owner_map={})
    default_context.update(
        hermes_agent_facade_canary_enabled=True,
        hermes_agent_facade_base_url="https://mcp.example.test",
        hermes_agent_vikunja_mcp_url="https://other.example.test/vikunja/custom",
    )
    default_render = _jinja_env().get_template("config.yaml.j2").render(
        **default_context
    )
    profile_render = _profile_config(PROFILE, enabled=True)

    for rendered in (default_render, profile_render):
        servers = _mcp_servers(rendered)
        assert set(servers) == {"agent-facade"}
        assert servers["agent-facade"] == {
            "timeout": 180,
            "url": "https://mcp.example.test/agents",
            "headers": {"X-Agent-Key": "${HERMES_AGENT_GATEWAY_KEY}"},
        }
        assert GATEWAY_KEY not in rendered


def test_enabled_selector_binds_default_and_profile_env_to_the_identity_key() -> None:
    default_context = _env_owner_context("example-agent", owner_map={})
    default_context["hermes_agent_facade_canary_enabled"] = True
    default_render = _env_jinja_env().get_template("hermes-env.j2").render(
        **default_context
    )
    assert _env_values(default_render)["HERMES_AGENT_GATEWAY_KEY"] == GATEWAY_KEY

    profile_context = _env_owner_context("example-agent", owner_map={})
    profile_context.update(
        hermes_agent_profile=PROFILE,
        hermes_agent_facade_canary_enabled=True,
    )
    profile_render = _env_jinja_env().get_template("hermes-env-profile.j2").render(
        **profile_context
    )
    assert _env_values(profile_render)["HERMES_AGENT_GATEWAY_KEY"] == GATEWAY_KEY


def _ansible_playbook() -> Path:
    path = shutil.which("ansible-playbook")
    assert path, "run this test inside the repository's pinned Ansible devshell"
    return Path(path)


def _native_play(
    tmp_path: Path,
    play: list[dict],
    variables: dict,
) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "ansible.cfg"
    config.write_text(
        "[defaults]\n"
        f"roles_path = {REPO_ROOT / 'roles'}\n"
        "retry_files_enabled = False\n"
        "fact_caching = memory\n"
        "host_key_checking = False\n"
    )
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(yaml.safe_dump(play, sort_keys=False))
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text(yaml.safe_dump(variables, sort_keys=False))
    local_tmp = tmp_path / "ansible-local"
    local_tmp.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": str(config),
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_ROLES_PATH": str(REPO_ROOT / "roles"),
        "ANSIBLE_HOME": str(tmp_path / ".ansible"),
        "ANSIBLE_HOST_KEY_CHECKING": "False",
        "ANSIBLE_NOCOLOR": "1",
    }
    return subprocess.run(
        [
            str(_ansible_playbook()),
            "-i",
            "localhost,",
            "-c",
            "local",
            "-e",
            f"@{vars_file}",
            str(playbook),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _assertion_only_play() -> list[dict]:
    return [
        {
            "name": "Validate a synthetic facade caller binding",
            "hosts": "localhost",
            "gather_facts": False,
            "connection": "local",
            "tasks": [
                {
                    "name": "Run the role's facade assertion only",
                    "ansible.builtin.import_role": {
                        "name": "hermes_agent",
                        "tasks_from": "assert_facade_canary.yml",
                    },
                }
            ],
        }
    ]


def _role_play() -> list[dict]:
    return [
        {
            "name": "Run Hermes role with a synthetic caller binding",
            "hosts": "localhost",
            "gather_facts": False,
            "connection": "local",
            "roles": [{"name": "hermes_agent"}],
        }
    ]


def _canary_vars(tmp_path: Path, **overrides: object) -> dict:
    targets = tmp_path / "targets"
    targets.mkdir(exist_ok=True)
    variables = {
        "hermes_agent_id": SYNTHETIC_ID,
        "hermes_agent_home": str(targets / "home"),
        "hermes_agent_hermes_home": str(targets / "home" / ".hermes"),
        "hermes_agent_cron_drain_restart_script": str(
            targets / "hermes-cron-drain-restart"
        ),
        "hermes_agent_facade_canary_enabled": True,
        "hermes_agent_facade_base_url": "https://mcp.example.test",
        "hermes_agent_vikunja_mcp_enabled": True,
        "ai_agent_gateway_key_fields": {SYNTHETIC_ID: SYNTHETIC_FIELD},
        "openbao_secrets_by_domain": {
            SYNTHETIC_ID: {SYNTHETIC_FIELD: SYNTHETIC_KEY}
        },
    }
    for name, value in _defaults().items():
        path_variable = name.endswith(
            ("_path", "_file", "_dir", "_home", "_script", "_destination")
        )
        if name.startswith("hermes_agent_") and path_variable:
            variables[name] = str(targets / name)
    variables.update(
        {
            "hermes_agent_home": str(targets / "home"),
            "hermes_agent_hermes_home": str(targets / "home" / ".hermes"),
            "hermes_agent_cron_drain_restart_script": str(
                targets / "hermes-cron-drain-restart"
            ),
        }
    )
    variables.update(overrides)
    return variables


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"ai_agent_gateway_key_fields": {}, "openbao_secrets_by_domain": {}},
            id="missing-registry-binding",
        ),
        pytest.param(
            {"openbao_secrets_by_domain": {SYNTHETIC_ID: {}}},
            id="missing-key",
        ),
        pytest.param(
            {
                "openbao_secrets_by_domain": {
                    SYNTHETIC_ID: {SYNTHETIC_FIELD: None}
                }
            },
            id="null-key",
        ),
        pytest.param(
            {
                "openbao_secrets_by_domain": {
                    SYNTHETIC_ID: {SYNTHETIC_FIELD: 123}
                }
            },
            id="non-string-key",
        ),
        pytest.param(
            {
                "openbao_secrets_by_domain": {
                    SYNTHETIC_ID: {SYNTHETIC_FIELD: " \t\n"}
                }
            },
            id="whitespace-key",
        ),
        pytest.param(
            {
                "hermes_agent_gateway_key_field": "unregistered_field",
                "openbao_secrets_by_domain": {
                    SYNTHETIC_ID: {"unregistered_field": SYNTHETIC_KEY}
                },
            },
            id="incorrect-registry-field",
        ),
        pytest.param(
            {"hermes_agent_facade_base_url": "https://mcp.example.test/vikunja"},
            id="base-url-with-path",
        ),
        pytest.param(
            {"hermes_agent_facade_base_url": "http://mcp.example.test"},
            id="base-url-scheme",
        ),
    ],
)
def test_full_role_fails_closed_before_any_target_write(
    tmp_path: Path, overrides: dict
) -> None:
    result = _native_play(tmp_path, _role_play(), _canary_vars(tmp_path, **overrides))
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "The Vikunja facade canary requires" in output
    assert not (tmp_path / "targets" / "hermes-cron-drain-restart").exists()
    assert not (tmp_path / "targets" / "home" / ".hermes").exists()


def test_native_assertion_accepts_a_synthetic_identity_bound_key(
    tmp_path: Path,
) -> None:
    result = _native_play(tmp_path, _assertion_only_play(), _canary_vars(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
