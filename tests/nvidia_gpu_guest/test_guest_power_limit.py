from pathlib import Path

from jinja2 import Environment
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/nvidia_gpu_guest"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_power_limit_is_opted_in():
    defaults = _load(ROLE / "defaults/main/00-core.yml")
    main = _load(ROLE / "tasks/main.yml")
    include = next(task for task in main if task.get("ansible.builtin.include_tasks") == "install-power-limit.yml")
    assert defaults["nvidia_gpu_guest_power_limit_w"] == 0
    assert include["when"] == "nvidia_gpu_guest_power_limit_w | int > 0"
    assert not Environment().compile_expression(include["when"])(**defaults)
    assert main.index(include) > next(i for i, task in enumerate(main) if task.get("ansible.builtin.include_tasks") == "install-fan-control.yml")


def test_host_power_limit_renders_in_unit():
    host = _load(ROOT / "inventory/host_vars/llm-blackwell-7-96GB-medium.yml")
    unit = (ROLE / "templates/nvidia-gpu-power-limit.service.j2").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/nvidia-smi -pl 325" in Environment().from_string(unit).render(**host)


def test_power_limit_is_checked_against_each_gpu_range():
    tasks = _load(ROLE / "tasks/install-power-limit.yml")
    assertion = next(task for task in tasks if "ansible.builtin.assert" in task)
    assert assertion["loop"] == "{{ nvidia_gpu_guest_power_limits.stdout_lines }}"
    assert len(assertion["ansible.builtin.assert"]["that"]) == 5
    assert "[3]" in assertion["ansible.builtin.assert"]["that"][-2]
    assert "[2]" in assertion["ansible.builtin.assert"]["that"][-1]
