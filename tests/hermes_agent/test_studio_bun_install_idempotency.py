"""Exercise the hermes-studio Bun install tasks with Ansible and a Bun stub."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/hermes_agent/tasks/companion_uis.yml"
INSTALL = "Install the pinned hermes-studio package with bun"
VERSION_CHECK = "Check the installed hermes-studio package version"
PACKAGE_READ = "Read the installed hermes-studio package metadata"


def _studio_tasks():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    block = next(task["block"] for task in tasks if task.get("name") == "Install and run hermes-studio")
    return {task.get("name"): task for task in block}


@pytest.mark.parametrize(
    ("current", "expected_calls", "expected_changed"),
    [(None, 1, True), ("0.1.0", 1, True), ("0.6.47", 0, False)],
    ids=("absent", "upgrade", "equal"),
)
def test_studio_install_runs_only_when_the_pin_differs(
    tmp_path, current, expected_calls, expected_changed
):
    tasks = _studio_tasks()
    assert INSTALL in tasks, f"{INSTALL!r} must exist"
    selected = [tasks[name] for name in (VERSION_CHECK, PACKAGE_READ, INSTALL) if name in tasks]
    root = tmp_path / "bun"
    package_json = root / "install/global/node_modules/hermes-web-ui/package.json"
    package_json.parent.mkdir(parents=True)
    if current is not None:
        package_json.write_text(json.dumps({"name": "hermes-web-ui", "version": current}))

    stub = tmp_path / "path-first/bun"
    stub.parent.mkdir()
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "\n"
        "spec = sys.argv[-1]\n"
        "package, version = spec.rsplit('@', 1)\n"
        "global_dir = pathlib.Path(os.environ['BUN_INSTALL_GLOBAL_DIR'])\n"
        "manifest = global_dir / 'node_modules' / package / 'package.json'\n"
        "calls = pathlib.Path(os.environ['BUN_INSTALL']) / 'bun-calls.txt'\n"
        "calls.parent.mkdir(parents=True, exist_ok=True)\n"
        "with calls.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(spec + '\\n')\n"
        "print('bun add v1.3.13')\n"
        "print()\n"
        "if manifest.exists() and json.loads(manifest.read_text())['version'] == version:\n"
        "    print('Checked 1 install across 2 packages (no changes)')\n"
        "else:\n"
        "    manifest.parent.mkdir(parents=True, exist_ok=True)\n"
        "    manifest.write_text(json.dumps({'name': package, 'version': version}))\n"
        "    print(f'installed {package}@{version}')\n"
        "    print()\n"
        "    print('1 package installed')\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    (root / "bin").mkdir(parents=True)
    shutil.copy2(stub, root / "bin/bun")

    # The stub mirrors Bun's source-tested install and no-op output fragments.
    # No-op: https://github.com/oven-sh/bun/blob/main/test/cli/install/bun-add.test.ts#L2549-L2557
    # Install: https://github.com/oven-sh/bun/blob/main/test/cli/install/bun-add.test.ts#L3334-L3348
    playbook = tmp_path / "playbook.yml"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump({"all": {"hosts": {"localhost": {
            "ansible_connection": "local",
            "ansible_python_interpreter": os.sys.executable,
        }}}}),
        encoding="utf-8",
    )
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text("", encoding="utf-8")
    playbook.write_text(
        yaml.safe_dump([{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "vars": {
                "hermes_agent_bun_install_root": str(root),
                "hermes_agent_studio_package": "hermes-web-ui",
                "hermes_agent_studio_version": "0.6.47",
                "expected_changed": expected_changed,
                "handler_marker": str(tmp_path / "restart-notified"),
            },
            "tasks": [*selected,
                      {"ansible.builtin.meta": "flush_handlers"},
                      {"ansible.builtin.stat": {"path": "{{ handler_marker }}"},
                       "register": "handler_status"},
                      {"ansible.builtin.assert": {"that": [
                          "(hermes_agent_studio_install.changed | default(false)) == expected_changed",
                          "handler_status.stat.exists == expected_changed",
                      ]}},
            ],
            "handlers": [{"name": "Restart hermes-studio", "ansible.builtin.copy": {
                "content": "restarted", "dest": "{{ handler_marker }}"
            }}],
        }], sort_keys=False),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{stub.parent}{os.pathsep}{env.get('PATH', '')}"
    env["ANSIBLE_CONFIG"] = str(ansible_config)
    result = subprocess.run(
        [shutil.which("ansible-playbook"), str(playbook), "-i", str(inventory)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (root / "bun-calls.txt").read_text().splitlines() if (root / "bun-calls.txt").exists() else []
    assert len(calls) == expected_calls, f"expected {expected_calls} Bun call(s), got {calls}"
    assert json.loads(package_json.read_text())["version"] == "0.6.47"


def test_studio_unit_template_keeps_its_restart_notification():
    tasks = _studio_tasks()
    assert tasks[INSTALL]["notify"] == "Restart hermes-studio"
    assert tasks["Install the hermes-studio unit"]["notify"] == "Restart hermes-studio"
