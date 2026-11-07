"""Laya must start with the card present, and a converge must fail if it computes on the CPU."""

from __future__ import annotations

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles/laya_systemone"
TASKS = yaml.safe_load((ROLE / "tasks/assert-gpu.yml").read_text())
READ = yaml.safe_load((ROLE / "tasks/read-device.yml").read_text())[0]


def _named(name: str) -> dict:
    return next(task for task in TASKS if task["name"] == name)


def test_unit_refuses_to_start_until_the_card_is_visible() -> None:
    unit = (ROLE / "templates/laya-systemone.service.j2").read_text()
    assert "Environment=LAYA_DEVICE=cuda" in unit
    assert "ExecStartPre=/usr/bin/nvidia-smi -L" in unit
    assert unit.index("ExecStartPre=") < unit.index("ExecStart={{")
    assert "Restart=on-failure" in unit


def test_device_read_is_authenticated_and_does_not_log_the_key() -> None:
    assert READ["ansible.builtin.uri"]["headers"]["Authorization"] == "Bearer {{ laya_systemone_api_key }}"
    assert READ["no_log"] is True


def test_cpu_device_restarts_once_then_fails_closed() -> None:
    restart = _named("Restart Laya once when it started on the CPU")
    assert "is match('cuda')" in restart["when"]
    assert [task["name"] for task in restart["block"]] == [
        "Restart the Laya service so it can load on the card",
        "Wait for Laya health after the restart",
        "Read where Laya computes after the restart",
    ]
    verdict = _named("Assert Laya computes on the card")["ansible.builtin.assert"]
    assert verdict["that"] == ["laya_systemone_device_read.json.device | default('') is match('cuda')"]


def test_serving_converge_includes_the_device_check_after_health() -> None:
    main = (ROLE / "tasks/main.yml").read_text()
    assert main.index("file: health.yml") < main.index("file: assert-gpu.yml")
