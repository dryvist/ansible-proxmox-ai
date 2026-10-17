from pathlib import Path

import pytest
import yaml

ROLES = Path(__file__).resolve().parents[2] / "roles"
SERVING_ROLES = ["llm_gpu_serving", "vllm_serving", "sglang_serving", "llamacpp_serving"]


@pytest.mark.parametrize("role", SERVING_ROLES)
def test_profile_switch_stops_every_installed_unit_not_only_this_map(role):
    tasks = yaml.safe_load((ROLES / role / "tasks/render-units.yml").read_text(encoding="utf-8"))
    find = next(task for task in tasks if "ansible.builtin.find" in task)
    assert find["register"] == f"{role}_installed_units"
    assert find["ansible.builtin.find"]["patterns"].endswith("-*.service")

    handlers = yaml.safe_load((ROLES / role / "handlers/main.yml").read_text(encoding="utf-8"))
    stop = next(h for h in handlers if h["name"] == "Stop every other GPU serving profile before switching")
    assert f"{role}_installed_units.files" in stop["loop"]
    # The map's own profiles stay covered when the handler fires from a flow that skipped the find.
    assert f"union({role}_profiles | list)" in stop["loop"]
    assert stop["ansible.builtin.systemd"]["name"].endswith("-{{ item }}.service")
