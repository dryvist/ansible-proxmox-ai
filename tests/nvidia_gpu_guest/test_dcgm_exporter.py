from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/nvidia_gpu_guest"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _when_holds(task: dict, context: dict) -> bool:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    conditions = task.get("when", [])
    if isinstance(conditions, str):
        conditions = [conditions]
    return all(bool(env.compile_expression(item)(**context)) for item in conditions)


def _selected_tasks(major: str, check_mode: bool = False) -> list[dict]:
    context = {
        "ansible_facts": {"distribution": "Debian", "distribution_major_version": major},
        "ansible_check_mode": check_mode,
        "nvidia_gpu_guest_nvidia_repo": {"changed": True},
    }
    tasks = _load(ROLE / "tasks/install-dcgm-exporter.yml")
    return [task for task in tasks if "when" in task and _when_holds(task, context)]


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
    assert defaults["nvidia_gpu_guest_dcgm_core_package"] == (
        "datacenter-gpu-manager-4-core=1:{{ nvidia_gpu_guest_dcgm_version }}*"
    )
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

    names = [task.get("name") for task in tasks]
    dropin_dir = tasks[names.index("Create the DCGM exporter drop-in directory")]["ansible.builtin.file"]
    assert dropin_dir["path"] == "{{ nvidia_gpu_guest_dcgm_exporter_dropin_path | dirname }}"
    assert dropin_dir["state"] == "directory"
    assert names.index("Create the DCGM exporter drop-in directory") < names.index(
        "Install the DCGM exporter collector override"
    )
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


def test_legacy_vulkan_path_installs_dcgm_exporter_when_nvidia_is_exposed():
    tasks = _load(ROOT / "roles/llama_cpp/tasks/gpu_access.yml")
    install = next(
        task
        for task in tasks
        if task.get("name") == "Install the DCGM exporter on NVIDIA Vulkan guests"
    )

    assert install["ansible.builtin.include_role"] == {
        "name": "nvidia_gpu_guest",
        "tasks_from": "install-dcgm-exporter.yml",
    }
    assert "llama_cpp_gpu_backend == 'vulkan'" in install["when"]
    assert "llama_cpp_nvidia_exposure.results[0].stat.exists" in install["when"][1]
    assert len(install["when"]) == 2
    assert not any("distribution" in item for item in install["when"])


def test_dcgm_exporter_installs_on_every_distribution_from_the_role_main():
    main = _load(ROLE / "tasks/main.yml")
    install = next(
        task for task in main if task.get("ansible.builtin.include_tasks") == "install-dcgm-exporter.yml"
    )
    assert "when" not in install


def test_debian12_keeps_the_single_apt_package_path():
    selected = _selected_tasks("12")
    assert [task["name"] for task in selected] == [
        "Install the compatible pinned DCGM and exporter packages"
    ]
    assert selected[0]["ansible.builtin.apt"]["name"] == "{{ nvidia_gpu_guest_dcgm_packages }}"


def test_debian13_installs_core_from_apt_and_the_pinned_exporter_file():
    selected = _selected_tasks("13")
    assert [task["name"] for task in selected] == [
        "Install the DCGM core package on Debian 13",
        "Download the pinned Debian 12 DCGM exporter package",
        "Install the pinned Debian 12 DCGM exporter package on Debian 13",
    ]
    core = selected[0]["ansible.builtin.apt"]
    assert core["name"] == [
        "{{ nvidia_gpu_guest_dcgm_core_package }}",
        "{{ nvidia_gpu_guest_dcgm_packages[0] }}",
    ]
    assert core["allow_downgrade"] is True

    download = selected[1]["ansible.builtin.get_url"]
    assert download["url"] == "{{ nvidia_gpu_guest_dcgm_exporter_deb12_url }}"
    assert download["checksum"] == "sha256:{{ nvidia_gpu_guest_dcgm_exporter_deb12_sha256 }}"

    deb = selected[2]["ansible.builtin.apt"]
    assert deb["deb"] == "/var/tmp/{{ nvidia_gpu_guest_dcgm_exporter_deb12_filename }}"
    assert deb["install_recommends"] is False
    assert deb["policy_rc_d"] == 101


def test_debian13_does_not_fetch_or_install_the_exporter_file_in_check_mode():
    names = [task["name"] for task in _selected_tasks("13", check_mode=True)]
    assert "Download the pinned Debian 12 DCGM exporter package" not in names
    assert "Install the pinned Debian 12 DCGM exporter package on Debian 13" not in names


def test_no_dcgm_task_adds_or_writes_an_apt_source():
    for task in _load(ROLE / "tasks/install-dcgm-exporter.yml"):
        assert "ansible.builtin.deb822_repository" not in task
        assert "ansible.builtin.apt_repository" not in task
        for module in ("ansible.builtin.copy", "ansible.builtin.file", "ansible.builtin.get_url"):
            assert not str(task.get(module, {}).get("dest", "")).startswith("/etc/apt")


def test_exporter_pin_matches_the_debian12_index_stanza():
    stanza = dict(
        line.split(": ", 1)
        for line in (FIXTURES / "dcgm-exporter-debian12-packages-stanza.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    )
    defaults = _load(ROLE / "defaults/main/20-dcgm-exporter.yml")

    assert stanza["Package"] == "datacenter-gpu-manager-exporter"
    assert stanza["Version"] == "4.8.4.3955-1"
    assert stanza["Version"].startswith(defaults["nvidia_gpu_guest_dcgm_exporter_version"])
    assert stanza["Architecture"] == "amd64"
    assert stanza["Depends"] == "datacenter-gpu-manager-4-core"
    assert defaults["nvidia_gpu_guest_dcgm_exporter_deb12_filename"] == stanza["Filename"].removeprefix("./")
    assert defaults["nvidia_gpu_guest_dcgm_exporter_deb12_sha256"] == stanza["SHA256"]
    url = jinja2.Environment().from_string(defaults["nvidia_gpu_guest_dcgm_exporter_deb12_url"]).render(
        nvidia_gpu_guest_dcgm_exporter_deb12_filename=defaults["nvidia_gpu_guest_dcgm_exporter_deb12_filename"]
    )
    assert url == (
        "https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/"
        + stanza["Filename"].removeprefix("./")
    )


def test_legacy_gpu_serving_role_installs_the_dcgm_exporter_after_userspace():
    tasks = _load(ROOT / "roles/llm_gpu_serving/tasks/main.yml")
    includes = [
        task["ansible.builtin.include_role"]
        for task in tasks
        if task.get("ansible.builtin.include_role", {}).get("tasks_from") == "install-dcgm-exporter.yml"
    ]
    names = [task.get("name") for task in tasks]

    assert includes == [{"name": "nvidia_gpu_guest", "tasks_from": "install-dcgm-exporter.yml"}]
    assert names.index("Install the DCGM exporter for GPU telemetry") > names.index(
        "Install NVIDIA userspace from the host module version"
    )


def test_site_installs_dcgm_on_every_nvidia_guest():
    playbook = _load(ROOT / "playbooks/llm-serving-gpu-engines.yml")
    play = next(
        play
        for play in playbook
        if play.get("name") == "Configure DCGM on every NVIDIA guest"
    )

    assert play["hosts"] == "nvidia_gpu_group"
    assert play["gather_facts"] is False
    converge = play["tasks"][0]
    assert converge["name"] == "Converge the DCGM exporter on every NVIDIA guest"
    tasks = converge["block"]
    assert [task["name"] for task in tasks] == [
        "Gather facts for the DCGM exporter",
        "Load APT proxy settings for the DCGM exporter",
        "Install the host-matched NVIDIA userspace for the DCGM exporter",
        "Install the DCGM exporter",
    ]
    assert [task.get("ansible.builtin.include_role") for task in tasks[1:]] == [
        {"name": "llamacpp_release", "tasks_from": "prepare.yml"},
        {"name": "nvidia_gpu_guest", "tasks_from": "install-nvidia-userspace.yml"},
        {"name": "nvidia_gpu_guest", "tasks_from": "install-dcgm-exporter.yml"},
    ]
    # Serving-group membership does not prove a serving role ran (an empty model list skips it).
    assert "when" not in converge
    assert "vars" not in play
    assert converge["rescue"][0]["ansible.builtin.include_tasks"] == (
        "tasks/record_isolated_failure.yml"
    )
    assert not any("cuda-toolkit" in str(task) or "cache-sync.yml" in str(task) for task in tasks)

    host_projection = _load(ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml")
    add_host = next(task["ansible.builtin.add_host"] for task in host_projection)
    assert "['nvidia_gpu_group'] if 'nvidia-gpu' in" in add_host["groups"]
