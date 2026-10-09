"""Both engine health checks inherit shared retry bounds and expose diagnostics."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
ENGINE_ROOTS = {
    "vllm_serving": REPO_ROOT / "roles/vllm_serving",
    "llamacpp_serving": REPO_ROOT / "roles/llamacpp_serving",
}
HEALTH_TASK = "Verify the active GPU serving profile is healthy"


def _defaults() -> dict:
    return yaml.safe_load((SHARED_ROOT / "defaults/main/00-core.yml").read_text(encoding="utf-8"))


def _health_block(role_root: Path) -> dict:
    tasks = yaml.safe_load((role_root / "tasks/health-check.yml").read_text(encoding="utf-8"))
    return next(task for task in tasks if task.get("name") == HEALTH_TASK)


def test_retry_defaults_cover_a_first_start() -> None:
    defaults = _defaults()
    assert defaults["nvidia_gpu_guest_health_retries"] * defaults["nvidia_gpu_guest_health_delay"] >= 900
    assert defaults["nvidia_gpu_guest_health_journal_lines"] >= 80


def test_each_probe_uses_the_shared_retry_defaults() -> None:
    for engine, role_root in ENGINE_ROOTS.items():
        block = _health_block(role_root)
        probe = block["block"][0]
        assert probe["retries"] == "{{ nvidia_gpu_guest_health_retries }}"
        assert probe["delay"] == "{{ nvidia_gpu_guest_health_delay }}"
        assert f"{engine}_active_profile" in yaml.safe_dump(probe)


def test_failure_paths_show_status_and_journal_then_fail() -> None:
    for role_root in ENGINE_ROOTS.values():
        block = _health_block(role_root)
        rescue = block["rescue"]
        read = yaml.safe_dump(rescue[0])
        assert "systemctl" in read and "journalctl" in read
        assert "nvidia_gpu_guest_health_journal_lines" in read
        assert "nvidia_gpu_guest_health_redact_regex" in yaml.safe_dump(rescue[1])
        assert "ansible.builtin.debug" in rescue[1]
        assert "ansible.builtin.fail" in rescue[-1]
