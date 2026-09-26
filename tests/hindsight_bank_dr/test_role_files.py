"""Render the hindsight_bank_dr scripts/units and check what ansible-lint and
molecule-free syntax-check do NOT: that the shell scripts are actually valid
bash, and that the drill script's pass/fail gate is structurally sound (every
error path exits before the final RESULT="pass").
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parent.parent.parent / "roles" / "hindsight_bank_dr"

# Defaults whose YAML value is itself unrendered Jinja (env lookups, a
# cross-role default) are dropped here -- plain jinja2.Environment has neither
# Ansible's `lookup()` global nor its recursive default-templating pass, so
# those need a literal stand-in from _EXTRA_CONTEXT below instead, or the
# script templates would render the raw Jinja source as their own output (a
# test-harness artifact, not a role bug).
DEFAULTS = {
    key: value
    for key, value in yaml.safe_load((ROLE / "defaults" / "main.yml").read_text()).items()
    if not (isinstance(value, str) and "{{" in value)
}

# Literal stand-ins for every key dropped from DEFAULTS above, plus
# cross-role vars (hindsight_docker's own defaults) and ansible_managed.
_EXTRA_CONTEXT = {
    "hindsight_docker_container_name": "hindsight",
    "hindsight_docker_api_port": 8888,
    "ansible_managed": "Ansible managed",
    "hindsight_bank_dr_container_name": "hindsight",
    "hindsight_bank_dr_api_port": 8888,
    "hindsight_bank_dr_s3_endpoint": "http://127.0.0.1:9000",
    "hindsight_bank_dr_s3_region": "us-east-1",
    "hindsight_bank_dr_s3_access_key_id": "AKIAEXAMPLE",
    "hindsight_bank_dr_s3_secret_access_key": "secretexample",  # noqa: S105 -- test fixture, not a real credential
}


def _render(name: str) -> str:
    template = (ROLE / "templates" / name).read_text()
    context = {**DEFAULTS, **_EXTRA_CONTEXT}
    return jinja2.Environment().from_string(template).render(**context)


def _bash_syntax_ok(script: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["bash", "-n", "/dev/stdin"],
        input=script,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stderr


def test_export_script_renders_valid_bash():
    script = _render("hindsight-bank-export.sh.j2")
    assert "{{" not in script and "{%" not in script, "unrendered Jinja left in the export script"
    assert "set -euo pipefail" in script
    ok, stderr = _bash_syntax_ok(script)
    assert ok, f"export script fails bash -n:\n{stderr}"


def test_drill_script_renders_valid_bash():
    script = _render("hindsight-bank-dr-drill.sh.j2")
    assert "{{" not in script and "{%" not in script, "unrendered Jinja left in the drill script"
    assert "set -euo pipefail" in script
    ok, stderr = _bash_syntax_ok(script)
    assert ok, f"drill script fails bash -n:\n{stderr}"


def test_drill_script_never_reports_pass_after_an_error_path():
    """Every `exit 1` in the drill body must precede the single terminal
    RESULT="pass" line -- otherwise a failed step could still report success.
    """
    script = _render("hindsight-bank-dr-drill.sh.j2")
    lines = script.splitlines()
    pass_lines = [i for i, line in enumerate(lines) if line.strip() == 'RESULT="pass"']
    assert len(pass_lines) == 1, "expected exactly one terminal RESULT=\"pass\" line"
    pass_at = pass_lines[0]
    exit1_lines = [i for i, line in enumerate(lines) if line.strip() == "exit 1"]
    assert exit1_lines, "expected at least one error exit path in the drill script"
    assert all(i < pass_at for i in exit1_lines), (
        "an `exit 1` appears after the terminal RESULT=\"pass\" line -- "
        "a failure path could still be reported as a pass"
    )


def test_export_script_prunes_before_reporting_result():
    script = _render("hindsight-bank-export.sh.j2")
    assert "s3 rm --recursive" in script
    assert script.index("s3 rm --recursive") < script.index('RESULT="pass"')


def test_export_and_drill_units_declare_their_schedule():
    export_timer = (ROLE / "templates" / "hindsight-bank-export.timer.j2").read_text()
    drill_timer = (ROLE / "templates" / "hindsight-bank-dr-drill.timer.j2").read_text()
    for unit in (export_timer, drill_timer):
        assert "OnCalendar={{" in unit
        assert "Persistent=true" in unit

    export_service = (ROLE / "templates" / "hindsight-bank-export.service.j2").read_text()
    drill_service = (ROLE / "templates" / "hindsight-bank-dr-drill.service.j2").read_text()
    for service in (export_service, drill_service):
        assert "Type=oneshot" in service
        assert "SyslogIdentifier={{ hindsight_bank_dr_syslog_identifier }}" in service


def test_defaults_never_hardcode_the_hindsight_image_or_a_literal_version():
    text = (ROLE / "defaults" / "main.yml").read_text()
    assert "ghcr.io/vectorize-io/hindsight" not in text, (
        "this role must never pin its own copy of the hindsight image/tag -- "
        "it shells into the container roles/hindsight_docker already deployed"
    )
