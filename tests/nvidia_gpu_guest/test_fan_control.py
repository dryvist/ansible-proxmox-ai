from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/nvidia_gpu_guest"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_fan_control_is_opted_in_only_for_gpu_serving_guests():
    defaults = _load(ROLE / "defaults/main/00-core.yml")
    gpu_group = _load(ROOT / "inventory/group_vars/llm_gpu_group.yml")

    assert defaults["nvidia_gpu_guest_fan_control_enabled"] is False
    assert gpu_group["nvidia_gpu_guest_fan_control_enabled"] is True
    assert "renovate: datasource=pypi depName=nvidia-ml-py" in (
        ROLE / "defaults/main/00-core.yml"
    ).read_text(encoding="utf-8")


def test_nvml_control_is_installed_enabled_and_run_during_converge():
    main = _load(ROLE / "tasks/main.yml")
    includes = [task.get("ansible.builtin.include_tasks") for task in main]
    assert includes.index("install-nvidia-userspace.yml") < includes.index(
        "install-fan-control.yml"
    )

    tasks = _load(ROLE / "tasks/install-fan-control.yml")
    install = next(
        task["ansible.builtin.command"]
        for task in tasks
        if task.get("name") == "Install the pinned NVML Python bindings"
    )
    assert install["argv"][-1] == "nvidia-ml-py=={{ nvidia_gpu_guest_nvml_version }}"

    unit = next(
        task["ansible.builtin.systemd_service"]
        for task in tasks
        if task.get("name") == "Enable the GPU fan-control unit for boot"
    )
    assert unit["enabled"] is True
    assert any(
        task.get("name") == "Set every NVIDIA GPU fan to 100 percent now"
        for task in tasks
    )


def test_fan_control_uses_nvml_for_every_fan_and_preserves_firmware_errors():
    script = (ROLE / "files/set-fan-speed.py").read_text(encoding="utf-8")

    assert "pynvml.nvmlDeviceGetNumFans(handle)" in script
    assert "for fan_index in range(fan_count)" in script
    assert "pynvml.nvmlDeviceSetFanSpeed_v2(handle, fan_index, 100)" in script
    assert "type(error).__name__}: {error}" in script
    assert "failed = True" in script


def test_fan_control_unit_runs_after_dcgm_at_boot():
    unit = (ROLE / "templates/nvidia-gpu-fan-control.service.j2").read_text(
        encoding="utf-8"
    )

    assert "After=nvidia-dcgm.service" in unit
    assert "Requires=nvidia-dcgm.service" in unit
    assert "ExecStart={{ nvidia_gpu_guest_nvml_python }}" in unit
    assert "WantedBy=multi-user.target" in unit
