"""Qdrant key, collection and untrusted-token contract.

Three things must hold together, and none is visible to ansible-lint:

  1. Every consumer of the Qdrant API key reads the SAME secret field.
  2. The collection set is exact: one trusted, one untrusted read-only, one
     untrusted read-write.
  3. The untrusted-tier JWT grants exactly the two untrusted collections at
     exactly those levels, and nothing on the trusted one. A token with no
     `access` claim is granted MANAGE access by Qdrant, so the shape of the
     claim is the security boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROLES = REPO_ROOT / "roles"

TRUSTED = "agent_memories"
SHARED = "agent_memories_shared"
SCRATCH = "agent_scratch_untrusted"

KEY_CONSUMERS = [
    ("qdrant_docker", "qdrant_docker_api_key"),
    ("llamaindex", "llamaindex_qdrant_api_key"),
    ("agentgateway_docker", "agentgateway_docker_qdrant_api_key"),
]


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text())
    assert isinstance(loaded, dict), f"{path} is not a mapping"
    return loaded


def _defaults(role: str) -> dict[str, Any]:
    return _load_yaml(ROLES / role / "defaults" / "main.yml")


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    """Render a defaults value against the defaults themselves."""
    if isinstance(value, str) and "{{" in value:
        return jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(value).render(**context)
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, context) for key, item in value.items()}
    return value


def _plugin() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "qdrant_jwt", ROLES / "qdrant_docker" / "filter_plugins" / "qdrant_jwt.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _untrusted_access() -> list[dict[str, str]]:
    defaults = _defaults("qdrant_docker")
    access = _resolve(defaults["qdrant_docker_untrusted_access"], defaults)
    assert isinstance(access, list)
    return access


def _decode(token: str, key: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
    header_b64, payload_b64, signature_b64 = token.split(".")

    def unb64(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    expected = hmac.new(key.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    return (
        json.loads(unb64(header_b64)),
        json.loads(unb64(payload_b64)),
        hmac.compare_digest(expected, unb64(signature_b64)),
    )


@pytest.mark.parametrize(("role", "variable"), KEY_CONSUMERS)
def test_key_consumer_reads_the_one_secret_field(role: str, variable: str) -> None:
    expression = _defaults(role)[variable]
    assert isinstance(expression, str)
    expression = expression.replace('"', "'")
    assert "bao_local_llm_secrets" in expression
    assert "['QDRANT_API_KEY']" in expression
    assert "lookup(" not in expression, f"{role}.{variable} must not read a second source"


def test_the_one_secret_field_is_fetched_exactly_once() -> None:
    domains = _load_yaml(ROLES / "openbao_secrets" / "defaults" / "main" / "10-domains.yml")
    local_llm = next(d for d in domains["openbao_secrets_domains"] if d["name"] == "local-llm")
    paths = [p if isinstance(p, str) else p["path"] for p in local_llm["paths"]]
    assert paths.count("ai/qdrant") == 1


def _render_config(enabled: bool) -> dict[str, Any]:
    template = (ROLES / "qdrant_docker" / "templates" / "config.yaml.j2").read_text()
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True)
    env.filters["bool"] = bool
    loaded = yaml.safe_load(env.from_string(template).render(qdrant_docker_jwt_rbac_enabled=enabled))
    assert isinstance(loaded, dict)
    return loaded


def _main_task(name: str) -> dict[str, Any]:
    tasks = yaml.safe_load((ROLES / "qdrant_docker" / "tasks" / "main.yml").read_text())
    return next(t for t in tasks if t.get("name") == name)


def test_jwt_rbac_is_off_by_default() -> None:
    assert _defaults("qdrant_docker")["qdrant_docker_jwt_rbac_enabled"] is False


def test_config_without_opt_in_has_no_jwt_rbac() -> None:
    assert "jwt_rbac" not in _render_config(False)["service"]


def test_config_with_opt_in_enables_jwt_rbac_and_leaves_the_key_out_of_the_file() -> None:
    service = _render_config(True)["service"]
    assert service["jwt_rbac"] is True
    assert "api_key" not in service
    compose = (ROLES / "qdrant_docker" / "templates" / "docker-compose.yml.j2").read_text()
    assert 'QDRANT__SERVICE__API_KEY: "{{ qdrant_docker_api_key }}"' in compose


@pytest.mark.parametrize(
    "name",
    ["Ensure the untrusted-tier collections exist", "Publish the untrusted-tier access token"],
)
def test_collections_and_token_require_the_opt_in(name: str) -> None:
    when = _main_task(name).get("when")
    conditions = when if isinstance(when, list) else [when]
    assert "qdrant_docker_jwt_rbac_enabled | bool" in conditions


def test_collection_names_are_exact() -> None:
    defaults = _defaults("qdrant_docker")
    resolved = _resolve({k: v for k, v in defaults.items() if "collection" in k}, defaults)
    assert isinstance(resolved, dict)
    assert resolved["qdrant_docker_trusted_collection"] == TRUSTED
    assert resolved["qdrant_docker_shared_collection"] == SHARED
    assert resolved["qdrant_docker_scratch_collection"] == SCRATCH
    # What the role creates: the untrusted pair, never the trusted collection.
    assert resolved["qdrant_docker_untrusted_collections"] == [SHARED, SCRATCH]


def test_memory_sidecar_writes_the_trusted_collection() -> None:
    assert _defaults("agentgateway_docker")["agentgateway_docker_qdrant_mcp_collection"] == TRUSTED


def test_untrusted_access_is_exactly_shared_read_and_scratch_write() -> None:
    assert _untrusted_access() == [
        {"collection": SHARED, "access": "r"},
        {"collection": SCRATCH, "access": "rw"},
    ]


def test_publishing_is_off_by_default() -> None:
    assert _defaults("qdrant_docker")["qdrant_docker_publish_untrusted_jwt"] is False


def test_minted_token_carries_exactly_the_untrusted_grants() -> None:
    key = "test-api-key"
    token = _plugin().qdrant_jwt(key, _untrusted_access())
    header, payload, signature_ok = _decode(token, key)
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert signature_ok
    assert set(payload) == {"access"}
    assert payload["access"] == [
        {"collection": SHARED, "access": "r"},
        {"collection": SCRATCH, "access": "rw"},
    ]
    # Negative: a list claim (a bare string would be global) and no grant on
    # the trusted collection.
    assert isinstance(payload["access"], list)
    assert TRUSTED not in {entry["collection"] for entry in payload["access"]}


def test_token_is_bound_to_the_key_and_deterministic() -> None:
    access = _untrusted_access()
    plugin = _plugin()
    token = plugin.qdrant_jwt("key-a", access)
    assert token == plugin.qdrant_jwt("key-a", access)
    assert not _decode(token, "key-b")[2]


@pytest.mark.parametrize(
    "access",
    [
        None,
        [],
        "r",
        [{"collection": SHARED}],
        [{"collection": SHARED, "access": "m"}],
        [{"collection": SHARED, "access": "rw", "extra": 1}],
        [{"collection": "", "access": "r"}],
    ],
)
def test_filter_refuses_a_claim_that_would_not_scope_the_token(access: Any) -> None:
    with pytest.raises(Exception, match="qdrant_jwt"):
        _plugin().qdrant_jwt("key", access)


def test_filter_refuses_an_empty_key() -> None:
    with pytest.raises(Exception, match="qdrant_jwt"):
        _plugin().qdrant_jwt("", _untrusted_access())
