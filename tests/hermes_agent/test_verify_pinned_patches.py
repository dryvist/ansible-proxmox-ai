"""Self-check for scripts/verify-pinned-patches.py: readback asserts run in role
order against the source their slurp read at that point. Runs bare or under pytest."""

import runpy
import tempfile
from pathlib import Path

from jinja2 import DebugUndefined, Environment

VERIFIER = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts" / "verify-pinned-patches.py")
)

MAIN_TASKS = """\
- name: Pinned-source patches for the readback self-check
  ansible.builtin.include_tasks: patches_readback.yml
"""

PATCH_TASKS = """\
- name: Read scheduler sources before patching
  ansible.builtin.slurp:
    src: "{{ install_dir }}/cron/scheduler.py"
  register: before_readback
- name: Check the pre-patch text
  vars:
    pinned_source: "{{ before_readback.content | b64decode }}"
  ansible.builtin.assert:
    that:
      - "'original' in pinned_source"
- name: Rename the scheduler text
  ansible.builtin.replace:
    path: "{{ install_dir }}/cron/scheduler.py"
    regexp: "original"
    replace: "patched"
- name: Read scheduler sources after patching
  ansible.builtin.slurp:
    src: "{{ item }}"
  loop:
    - "{{ install_dir }}/cron/other.py"
    - "{{ install_dir }}/cron/scheduler.py"
  register: after_readback
- name: Check the post-patch text
  vars:
    pinned_source: "{{ after_readback.results[1].content | b64decode }}"
    other_source: "{{ after_readback.results[0].content | b64decode }}"
  ansible.builtin.assert:
    that:
      - "'patched' in pinned_source"
      - "'original' in pinned_source"
      - "'original' not in pinned_source"
      - "'absent' not in other_source"
      - "'patched' not in pinned_source"
      - pinned_source == expected_source
"""


def _role(tmp_path: Path) -> Path:
    role_root = tmp_path / "repo" / "roles" / "hermes_agent"
    tasks = role_root / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(MAIN_TASKS)
    (tasks / "patches_readback.yml").write_text(PATCH_TASKS)
    return role_root


def test_readback_asserts_run_in_place_against_the_source_they_read(tmp_path):
    sources = {
        "cron/scheduler.py": "def run():\n    original()\n",
        "cron/other.py": "other\n",
    }
    environment = Environment(autoescape=False, undefined=DebugUndefined)
    unexpected, report = VERIFIER["verify_role"](_role(tmp_path), sources, environment)
    where = "roles/hermes_agent/tasks/patches_readback.yml"
    assert unexpected == 0
    assert report.checked == 6
    assert report.failures == [
        (f"{where}:30", "'original' in pinned_source"),
        (f"{where}:33", "'patched' not in pinned_source"),
    ]
    assert [location for location, _ in report.skipped] == [f"{where}:34"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_readback_asserts_run_in_place_against_the_source_they_read(Path(directory))
    print("all checks passed")
