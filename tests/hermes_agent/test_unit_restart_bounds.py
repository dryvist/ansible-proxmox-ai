"""Restart-loop bounds on the long-running Hermes units.

hermes-gateway, hermes-dashboard and hermes-vikunja-bridge are all
Restart=always. With no start-rate limit, a config-broken unit restarted every
5-10s indefinitely: CPU burned, state reported as "activating", nobody alerted.

The bound alone would be strictly worse — systemd gives up and the unit sits
dead, equally silently. So the contract these tests pin is the PAIR: a bound,
plus an OnFailure= that says the bound was hit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _role_files import role_defaults_text, role_tasks, role_tasks_text, template_text
from _shell_harness_shared import (
    defaults_of,
    headers_of,
    install_stubs,
    lines,
    records,
    render,
    run_script,
)

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATES = ROLE / "templates"
DEFAULTS = role_defaults_text(ROLE)
TASKS = role_tasks_text(ROLE)
NTFY_URL = "https://ntfy.test.invalid/ai"

LONG_RUNNING = [
    "hermes-gateway.service.j2",
    "hermes-dashboard.service.j2",
    "hermes-vikunja-bridge.service.j2",
]


def _unit(name: str) -> str:
    return (TEMPLATES / name).read_text()


def _int_default(name: str) -> int:
    """Read one integer role default, failing loudly if the key is gone."""
    match = re.search(rf"^{name}:\s*(\d+)", DEFAULTS, re.M)
    assert match is not None, f"could not find {name} — renamed or removed?"
    return int(match.group(1))


def _section(text: str, name: str) -> str:
    """Return the body of one systemd section.

    Line-anchored on purpose: a section header is a line that IS "[Name]". A
    naive substring split matches the same text inside a comment — these units
    legitimately mention "[Service]" in prose explaining where StartLimit* must
    not go, which silently mis-sliced the section and inverted this test.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"[{name}]"]
    assert starts, f"no [{name}] section header"
    out: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if re.fullmatch(r"\[[A-Za-z]+\]", line.strip()):
            break
        out.append(line)
    return "\n".join(out)


def test_every_restart_always_unit_is_bounded() -> None:
    for name in LONG_RUNNING:
        unit = _unit(name)
        assert "Restart=always" in unit, f"{name}: fixture assumes Restart=always"
        assert "StartLimitBurst=" in unit, f"{name}: unbounded restart loop"
        assert "StartLimitIntervalSec=" in unit, f"{name}: unbounded restart loop"


def test_start_limits_live_in_the_unit_section() -> None:
    """systemd 229+ moved StartLimit* from [Service] to [Unit].

    Left in [Service] they are ignored with only a log warning — the unit would
    look bounded in review while looping unbounded in production. This is the
    failure mode most likely to be reintroduced, because the neighbouring
    Restart=/RestartSec= keys DO belong in [Service].
    """
    for name in LONG_RUNNING:
        unit = _unit(name)
        service = _section(unit, "Service")
        assert "StartLimit" not in service, (
            f"{name}: StartLimit* in [Service] is ignored by systemd 229+; move it to [Unit]"
        )
        assert "StartLimitBurst=" in _section(unit, "Unit"), f"{name}: StartLimit* must be in [Unit]"


def _directive(section: str, key: str) -> str:
    """The value of the one `Key=value` line in a systemd section body."""
    values = re.findall(rf"^{key}=(.+)$", section, re.M)
    assert len(values) == 1, f"expected exactly one {key}= line, found {len(values)}"
    return values[0].strip()


def _deployed() -> dict[str, str]:
    """Install path -> template source, from the role's own template tasks."""
    deployed = {}
    for task in role_tasks(ROLE):
        spec = task.get("ansible.builtin.template") or task.get("template")
        if spec:
            deployed[spec["dest"]] = spec["src"]
    return deployed


def test_the_alert_unit_and_script_are_deployed_from_their_templates() -> None:
    """An OnFailure= naming a unit that was never installed is a silent no-op."""
    deployed = _deployed()
    assert deployed.get("/etc/systemd/system/hermes-unit-alert@.service") == "hermes-unit-alert@.service.j2"
    assert deployed.get("/usr/local/bin/hermes-unit-alert.sh") == "hermes-unit-alert.sh.j2"


@pytest.mark.parametrize("unit", LONG_RUNNING)
def test_hitting_the_bound_sends_one_urgent_post_naming_the_unit(unit: str, tmp_path: Path) -> None:
    """Start the alert the way systemd does for a unit that hit its bound.

    The failed unit's OnFailure= names an alert instance. The alert template's
    ExecStart= runs the deployed script with that instance as argv[1]. The script
    is rendered from its template and run under stub curl and logger, and it must
    send exactly one urgent ntfy publish that names the unit.
    """
    failed = unit.removesuffix(".j2")
    alert_unit = _directive(_section(_unit(unit), "Unit"), "OnFailure").replace("%n", failed)
    assert alert_unit.startswith("hermes-unit-alert@") and alert_unit.endswith(".service"), alert_unit
    instance = alert_unit[len("hermes-unit-alert@") : -len(".service")]
    execstart = _directive(_section(_unit("hermes-unit-alert@.service.j2"), "Service"), "ExecStart")
    script_path, *script_args = execstart.replace("%i", instance).split()
    deployed = _deployed()
    assert script_path in deployed, f"ExecStart runs {script_path}, which no task installs"

    env = install_stubs(tmp_path)
    context = {
        **defaults_of("hermes_agent"),
        "ansible_managed": "test render",
        "hermes_agent_brain_watchdog_ntfy_url": NTFY_URL,
    }
    script = tmp_path / "hermes-unit-alert.sh"
    script.write_text(render(template_text(ROLE, deployed[script_path]), context))
    script.chmod(0o755)

    proc = run_script(script, script_args, env)
    assert proc.returncode == 0, proc.stderr
    posts = records(env, "posts.jsonl")
    assert len(posts) == 1, f"{unit}: expected one ntfy publish, got {len(posts)}"
    headers = headers_of(posts[0])
    assert headers["Priority"] == "urgent"
    assert posts[0]["url"] == NTFY_URL
    assert failed in headers["Title"] and failed in posts[0]["body"]
    assert any(failed in line for line in lines(env, "logger.log")), "the journal line must name the unit"


def test_the_alert_deploy_is_not_gated_on_the_brain_watchdog() -> None:
    """The long-running units exist whether or not the brain watchdog is
    enabled, so gating the alert unit on it would leave their OnFailure=
    pointing at something never installed.
    """
    block = TASKS.split("Deploy the Hermes per-unit failure alert script", 1)[1]
    block = block.split("- name:", 1)[0]
    assert "when:" not in block, "the per-unit alert script must deploy unconditionally"


def test_the_bound_tolerates_an_ordinary_flaky_start() -> None:
    """Too tight a bound turns a slow dependency into a paged outage."""
    burst = _int_default("hermes_agent_unit_restart_burst")
    window = _int_default("hermes_agent_unit_restart_interval_sec")
    assert burst >= 3, "fewer than 3 starts pages on ordinary dependency flap"
    assert window >= 60, "a sub-minute window makes the burst count meaningless"
