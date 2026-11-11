"""Laya's CUDA torch backend follows the host driver, never the CPU, and the device assertion names the torch build."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROLE = Path(__file__).resolve().parents[2] / "roles/laya_systemone"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/10-torch-backend.yml").read_text())
BACKENDS = DEFAULTS["laya_systemone_torch_backends"]
BACKEND_EXPRESSION = DEFAULTS["laya_systemone_torch_backend"]
MAIN = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
ASSERT_GPU = yaml.safe_load((ROLE / "tasks/assert-gpu.yml").read_text())
READ_TORCH = yaml.safe_load((ROLE / "tasks/read-torch-build.yml").read_text())

# cuNNN values the guest's pinned uv (0.13.6) accepts for --torch-backend, cu118 and newer.
UV_ACCEPTED = {
    "cu118", "cu120", "cu121", "cu122", "cu123", "cu124", "cu125", "cu126", "cu128", "cu129", "cu130", "cu132",
}


def _named(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _block(tasks: list[dict]) -> list[dict]:
    return next(task for task in tasks if "block" in task)["block"]


def _render(template: str, **variables) -> str:
    templar = Templar(loader=DataLoader())
    templar.available_variables = variables
    return str(templar.template(trust_as_template(template)))


def _backend_for(driver: str) -> str:
    return _render(
        BACKEND_EXPRESSION,
        laya_systemone_torch_backends=BACKENDS,
        nvidia_gpu_guest_nvidia_userspace_version=driver,
    )


def test_backend_table_is_unique_cu118_or_newer_and_newest_first() -> None:
    names = [row["backend"] for row in BACKENDS]
    cuda = [int(name.removeprefix("cu")) for name in names]
    drivers = [tuple(int(part) for part in row["min_driver"].split(".")) for row in BACKENDS]

    assert len(set(names)) == len(names)
    assert set(names) <= UV_ACCEPTED
    assert min(cuda) >= 118
    assert cuda == sorted(cuda, reverse=True)
    assert drivers == sorted(drivers, reverse=True)


@pytest.mark.parametrize(
    ("driver", "backend"),
    [
        ("595.45.04", "cu132"),  # equal to the newest floor
        ("590.48.01", "cu130"),
        ("580.65.06", "cu129"),  # below CUDA 13.0 Update 2's floor
        ("570.26.05", "cu126"),  # numeric compare: 570.26 is below 570.124, so not a string compare
        ("570.124.06", "cu128"),
        ("560.35.05", "cu126"),
        ("550.54.15", "cu124"),
        ("530.30.02", "cu121"),
        ("520.61.05", "cu118"),
        ("515.48.07", ""),  # no backend qualifies
    ],
)
def test_newest_backend_the_driver_supports_is_selected(driver: str, backend: str) -> None:
    assert _backend_for(driver) == backend


def test_old_driver_fails_the_converge_with_the_driver_version_and_never_cpu() -> None:
    task = _named(_block(MAIN), "Require a CUDA torch backend that the host driver supports")
    backend = _backend_for("515.48.07")

    assert backend == ""
    assert _render("{{ " + task["ansible.builtin.assert"]["that"][0] + " }}", laya_systemone_torch_backend=backend) == "False"
    message = _render(task["ansible.builtin.assert"]["fail_msg"], nvidia_gpu_guest_nvidia_userspace_version="515.48.07")
    assert "515.48.07" in message
    assert "CPU torch build" in message
    assert _render(
        "{{ " + task["ansible.builtin.assert"]["that"][0] + " }}",
        laya_systemone_torch_backend=_backend_for("580.65.06"),
    ) == "True"


@pytest.mark.parametrize(
    ("cuda", "backend", "expected"),
    [
        (None, "cu128", True),  # CPU build, or torch missing
        ("12.6", "cu128", True),  # wrong CUDA minor for the chosen backend
        ("12.8", "cu128", False),
        ("13.0", "cu130", False),
        ("13.0", "cu128", True),
    ],
)
def test_reinstall_triggers_on_a_cpu_or_mismatched_torch_build(cuda: str | None, backend: str, expected: bool) -> None:
    expression = _named(
        _block(MAIN), "Reinstall torch when the venv build is CPU-only or not the chosen backend"
    )["ansible.builtin.set_fact"]["laya_systemone_torch_reinstall"]
    build = {"torch": "2.9.1", "cuda": cuda, "available": cuda is not None}

    result = _render(expression, laya_systemone_torch_build=build, laya_systemone_torch_backend=backend)

    assert result == str(expected)


@pytest.mark.parametrize("reinstall", [True, False])
def test_install_adds_the_torch_reinstall_only_when_the_build_mismatches(reinstall: bool) -> None:
    argv = _named(
        _block(MAIN), "Install the pinned Laya HTTP server with a driver-compatible CUDA torch wheel"
    )["ansible.builtin.command"]["argv"]

    rendered = _render(
        argv,
        nvidia_gpu_guest_uv_bin="/usr/local/bin/uv",
        laya_systemone_venv="/opt/laya/venv",
        laya_systemone_torch_backend="cu128",
        laya_systemone_version="0.4.19",
        laya_systemone_torch_reinstall=reinstall,
    )

    assert "'--torch-backend=cu128'" in rendered
    assert "'/opt/laya/venv/bin/python'" in rendered
    assert "'laya[serve]==0.4.19'" in rendered
    assert ("'--reinstall-package=torch'" in rendered) is reinstall
    assert "auto" not in rendered and "cpu'" not in rendered


def test_converge_reads_the_build_then_decides_then_installs_and_gates_on_the_backend() -> None:
    names = [task.get("name") for task in _block(MAIN)]

    assert names.index("Require a CUDA torch backend that the host driver supports") < names.index(
        "Create the Laya virtual environment"
    )
    assert (
        names.index("Read the venv torch build before the install")
        < names.index("Reinstall torch when the venv build is CPU-only or not the chosen backend")
        < names.index("Install the pinned Laya HTTP server with a driver-compatible CUDA torch wheel")
    )


PROBE = READ_TORCH[0]["ansible.builtin.command"]["argv"][2]

CPU_TORCH = (
    '__version__ = "2.9.1+cpu"\n'
    "class version:\n    cuda = None\n"
    "class cuda:\n    @staticmethod\n    def is_available():\n        return False\n"
)
CUDA_TORCH = (
    '__version__ = "2.9.1+cu128"\n'
    'class version:\n    cuda = "12.8"\n'
    "class cuda:\n    @staticmethod\n    def is_available():\n        return True\n"
)


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (CPU_TORCH, {"torch": "2.9.1+cpu", "cuda": None, "available": False}),
        (CUDA_TORCH, {"torch": "2.9.1+cu128", "cuda": "12.8", "available": True}),
        (None, {"torch": None, "cuda": None, "available": False, "error": "ModuleNotFoundError"}),
    ],
)
def test_probe_reports_the_venv_torch_build_and_tolerates_a_missing_torch(
    tmp_path: Path, module: str | None, expected: dict
) -> None:
    if module is not None:
        (tmp_path / "torch.py").write_text(module, encoding="utf-8")

    # -S keeps site-packages out, so a real torch on this machine cannot shadow the fixture or the missing case.
    result = subprocess.run(
        [sys.executable, "-S", "-c", PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == expected


def test_device_assertion_message_names_the_torch_build() -> None:
    task = _named(ASSERT_GPU, "Assert Laya computes on the card")

    message = _render(
        task["ansible.builtin.assert"]["fail_msg"],
        laya_systemone_device_read={"json": {"device": "cpu"}},
        laya_systemone_torch_build={"torch": "2.9.1+cu128", "cuda": "12.8", "available": False},
    )

    assert "Laya reports device cpu." in message
    for field in ("torch.__version__ 2.9.1+cu128", "torch.version.cuda 12.8", "torch.cuda.is_available() False"):
        assert field in message


def test_device_assertion_reads_the_venv_torch_build_before_it_asserts() -> None:
    names = [task.get("name") for task in ASSERT_GPU]

    assert names.index("Read the venv torch build for the failure report") < names.index(
        "Assert Laya computes on the card"
    )
