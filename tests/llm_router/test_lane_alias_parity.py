"""Lane-class name export and shared contract parity checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lane_alias_names import lane_alias_names


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTER = Path(__file__).with_name("lane_alias_names.py")


def test_exporter_returns_only_lane_class_names(tmp_path: Path) -> None:
    (tmp_path / "llm-models.d").mkdir()
    (tmp_path / "llm-models.d/10-model.yml").write_text(
        "models:\n"
        "  - client_model_id: physical-id\n"
        "    upstream_model_id: upstream-id\n"
        "    route_group: route-group\n"
        "    stable_aliases: [small, medium]\n"
        "    direct_route_aliases: [direct-only]\n"
        "    fallback_groups: [fallback-only]\n",
        encoding="utf-8",
    )
    roles = tmp_path / "roles/llm_router/defaults/main/55-roles.yml"
    roles.parent.mkdir(parents=True)
    roles.write_text(
        "_llm_router_role_deployments_source:\n"
        "  - role: review\n"
        "    note: !unsafe role-fixture\n",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory/group_vars/all.yml"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        "small_model: !unsafe small\n"
        "role_model: review\n"
        "physical_model: physical-id\n"
        "route_model: route-group\n"
        "fallback_model: fallback-only\n"
        "dynamic_model: '{{ registry_model }}'\n",
        encoding="utf-8",
    )

    assert lane_alias_names(tmp_path) == ["medium", "review", "small"]


def test_lane_names_match_shared_contract(tmp_path: Path) -> None:
    lanes = os.environ.get("HOMELAB_CONTRACTS_LANES")
    checker = os.environ.get("HOMELAB_CONTRACTS_PARITY_SCRIPT")
    if not lanes or not checker:
        pytest.skip(
            "Set HOMELAB_CONTRACTS_LANES and HOMELAB_CONTRACTS_PARITY_SCRIPT "
            "to run local shared lane parity; CI runs this check."
        )

    names_file = tmp_path / "ansible-proxmox-ai-lane-alias-names.json"
    exported = subprocess.run(
        [sys.executable, str(EXPORTER)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert exported.returncode == 0, exported.stderr
    names_file.write_text(exported.stdout, encoding="utf-8")

    checked = subprocess.run(
        [sys.executable, checker, "--lanes", lanes, "--names", str(names_file)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, f"{checked.stdout}{checked.stderr}"
    assert json.loads(names_file.read_text(encoding="utf-8"))["source"] == "ansible-proxmox-ai"
