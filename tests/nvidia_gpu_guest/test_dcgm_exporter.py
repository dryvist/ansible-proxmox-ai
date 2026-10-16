from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/nvidia_gpu_guest"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_exporter_uses_the_release_compatible_package_pair():
    defaults = _load(ROLE / "defaults/main/20-dcgm-exporter.yml")
    assert defaults["nvidia_gpu_guest_dcgm_version"] == "4.6.1"
    assert defaults["nvidia_gpu_guest_dcgm_exporter_version"] == "4.8.4"
    assert "renovate: datasource=github-releases depName=NVIDIA/DCGM" in (
        ROLE / "defaults/main/20-dcgm-exporter.yml"
    ).read_text(encoding="utf-8")
    assert "renovate: datasource=github-releases depName=NVIDIA/dcgm-exporter" in (
        ROLE / "defaults/main/20-dcgm-exporter.yml"
    ).read_text(encoding="utf-8")
    core_package = defaults["nvidia_gpu_guest_dcgm_packages"][0]
    assert "datacenter-gpu-manager-4-cuda" in core_package
    assert "nvidia_gpu_guest_cuda_version.split('.')[0]" in core_package
    assert "nvidia_gpu_guest_dcgm_version" in core_package
    assert defaults["nvidia_gpu_guest_dcgm_packages"][1] == (
        "datacenter-gpu-manager-exporter={{ nvidia_gpu_guest_dcgm_exporter_version }}*"
    )


def test_gpu_guest_installs_and_starts_the_package_services():
    main = _load(ROLE / "tasks/main.yml")
    includes = [task.get("ansible.builtin.include_tasks") for task in main]
    assert includes.index("install-dcgm-exporter.yml") > includes.index(
        "install-nvidia-userspace.yml"
    )
    assert includes.index("install-dcgm-exporter.yml") < includes.index(
        "install-cuda-toolkit.yml"
    )

    tasks = _load(ROLE / "tasks/install-dcgm-exporter.yml")
    apt = next(task["ansible.builtin.apt"] for task in tasks if "ansible.builtin.apt" in task)
    assert apt["name"] == "{{ nvidia_gpu_guest_dcgm_packages }}"
    assert apt["policy_rc_d"] == 101

    services = {
        task["ansible.builtin.systemd"]["name"]
        for task in tasks
        if "ansible.builtin.systemd" in task
    }
    assert services == {"nvidia-dcgm.service", "nvidia-dcgm-exporter.service"}

    dropin = next(
        task["ansible.builtin.copy"]
        for task in tasks
        if task.get("name") == "Install the DCGM exporter collector override"
    )
    assert "ExecStart=" in dropin["content"]
    assert "--collectors {{ nvidia_gpu_guest_dcgm_collector_csv_path }}" in dropin[
        "content"
    ]


def test_exporter_collectors_keep_upstream_defaults_and_add_fan_speed():
    csv = (ROLE / "files/dcgm-exporter-counters.csv").read_text(encoding="utf-8")
    fields = [
        line.split(",", 1)[0].strip()
        for line in csv.splitlines()
        if line and not line.lstrip().startswith("#")
    ]
    assert len(fields) == len(set(fields))
    assert {
        "DCGM_FI_DEV_FAN_SPEED",
        "DCGM_FI_DEV_POWER_USAGE",
        "DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION",
        "DCGM_FI_DEV_SM_CLOCK",
        "DCGM_FI_DEV_MEM_CLOCK",
        "DCGM_FI_DEV_MEMORY_TEMP",
        "DCGM_FI_DEV_GPU_TEMP",
        "DCGM_FI_PROF_PCIE_TX_BYTES",
        "DCGM_FI_PROF_PCIE_RX_BYTES",
        "DCGM_FI_DEV_FB_USED",
    }.issubset(fields)
