"""Exercise inactive-tier and missing-URL behavior through the real Ansible tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_fabric_watchdog_registry_reachability import run_task_contract


@pytest.mark.parametrize(
    ("cpu_urls", "vllm_enabled"),
    [([], True), (["https://cpu.example.invalid/v1"], False)],
    ids=["cpu-9b-inactive", "vllm-switched-off"],
)
def test_inactive_tier_is_not_required_or_probed(
    tmp_path: Path, cpu_urls: list[str], vllm_enabled: bool
) -> None:
    result = run_task_contract(
        tmp_path, cpu_urls=cpu_urls, vllm_enabled=vllm_enabled
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "TASK [Assert every active servable tier has a projected API base]" in output
    assert "TASK [Assert the projected target URLs]" in output


def test_active_servable_tier_without_projected_url_fails_with_its_name(
    tmp_path: Path,
) -> None:
    result = run_task_contract(tmp_path, cpu_urls=[""])
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "cpu-9b" in output, output
    assert "no derivable api_base" in output, output
