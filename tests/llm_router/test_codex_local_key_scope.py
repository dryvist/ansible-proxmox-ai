"""The codex-local target key stays local-only and disabled without its source."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_KEYS = REPO_ROOT / "roles/llm_router/defaults/main/56a-target-keys.yml"


def _render(expression, variables):
    templar = Templar(loader=DataLoader())
    templar.available_variables = variables
    return templar.template(trust_as_template(expression))


def test_codex_local_key_is_disabled_and_scoped_to_local_capabilities():
    target_keys = yaml.safe_load(TARGET_KEYS.read_text(encoding="utf-8"))[
        "_llm_router_target_virtual_keys"
    ]
    matching = [key for key in target_keys if key["alias"] == "codex-local"]
    assert len(matching) == 1
    key = matching[0]

    variables = {
        "bao_apps_secrets": {},
        "llm_router_small_model": "fixture-local-small-model",
        "llm_router_small_capability_name": "fixture-local-small-capability",
        "llm_router_primary_model": "fixture-local-medium-capability",
        "llm_router_large_model": "fixture-nonlocal-large-model",
    }
    local_models = [
        variables["llm_router_small_model"],
        variables["llm_router_small_capability_name"],
        variables["llm_router_primary_model"],
    ]
    models = _render(key["models"], variables)

    assert _render(key["enabled"], variables) is False
    assert key["allowed_classes"] == ["local"]
    assert models == local_models
    assert set(models).issubset(local_models)
    assert "object_permission" not in key
