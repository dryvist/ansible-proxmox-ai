"""The health check waits long enough for a first start and shows the journal on failure."""

from __future__ import annotations

from pathlib import Path

import yaml

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles/llm_gpu_serving"
HEALTH_TASK = "Verify the active GPU serving profile is healthy"


def _defaults() -> dict:
    return yaml.safe_load((ROLE_ROOT / "defaults/main/00-core.yml").read_text(encoding="utf-8"))


def _health_block() -> dict:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/health-check.yml").read_text(encoding="utf-8"))
    return next(t for t in tasks if t.get("name") == HEALTH_TASK)


def test_retry_defaults_cover_a_first_start() -> None:
    d = _defaults()
    assert d["llm_gpu_serving_health_retries"] * d["llm_gpu_serving_health_delay"] >= 900
    assert d["llm_gpu_serving_health_journal_lines"] >= 80


def test_probe_uses_the_retry_defaults() -> None:
    probe = _health_block()["block"][0]
    assert probe["retries"] == "{{ llm_gpu_serving_health_retries }}"
    assert probe["delay"] == "{{ llm_gpu_serving_health_delay }}"


def test_failure_path_shows_status_and_journal_then_fails() -> None:
    block = _health_block()
    rescue = block["rescue"]
    read = yaml.safe_dump(rescue[0])
    assert "systemctl" in read and "journalctl" in read
    assert "llm_active_profile" in yaml.safe_dump(block["vars"])
    assert "ansible.builtin.debug" in rescue[1]
    assert "ansible.builtin.fail" in rescue[-1]
