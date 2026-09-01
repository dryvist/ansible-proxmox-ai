"""The Hindsight dependency-gate probe, and command targets generally.

A dependency gate that rejects the installed client disables memory while every
other control reports health: the service probe watches a service that is up,
`hermes memory status` returns true without contacting anything, and the recall
gate exercises a client that is importable. The deciding fact sits upstream of
all three.

The property these tests pin down is therefore not "the probe runs" but "the
probe distinguishes the broken state from the fixed one". A probe returning 0
for both would be green through exactly the condition it was written to catch,
which is the failure mode the three controls above already demonstrate.

Lives under tests/hermes_agent/ for the same reason as its siblings:
fabric_watchdog runs on the Hermes guest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "fabric_watchdog"
PROBE_TEMPLATE = (ROLE / "templates" / "hindsight-dep-gate.py.j2").read_text()
SHELL_TEMPLATE = (ROLE / "templates" / "fabric-watchdog.sh.j2").read_text()
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
TASKS = (ROLE / "tasks" / "main.yml").read_text()

# Verbatim shape of the upstream table. The == form is the state that caused
# the outage; the >= form is what apa#627 patches it to.
GATE_EXACT_PIN = (
    "LAZY_DEPS = {\n"
    '    "memory.honcho": ("honcho-ai==1.2.3",),\n'
    '    "memory.hindsight": ("hindsight-client==0.6.1",),\n'
    "}\n"
)
GATE_MINIMUM = GATE_EXACT_PIN.replace("hindsight-client==", "hindsight-client>=")


def _env() -> Environment:
    env = Environment()
    # `comment` is an Ansible filter, not a stock Jinja2 one.
    env.filters["comment"] = lambda text: "# " + str(text)
    return env


def _load_probe(tmp_path: Path, gate_source: str | None, installed: str | None):
    """Render the probe against a gate file and a pretended installed version.

    Returns the loaded module. Loaded under a name other than __main__ so the
    module's own sys.exit() guard does not fire during import.
    """
    gate_path = tmp_path / "lazy_deps.py"
    if gate_source is not None:
        gate_path.write_text(gate_source)

    rendered = _env().from_string(PROBE_TEMPLATE).render(
        ansible_managed="ansible managed",
        fabric_watchdog_lazy_deps_path=str(gate_path),
        fabric_watchdog_hindsight_feature_key="memory.hindsight",
        fabric_watchdog_hindsight_distribution="hindsight-client",
    )
    module_path = tmp_path / "dep_gate.py"
    module_path.write_text(rendered)

    spec = importlib.util.spec_from_file_location("dep_gate_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class PackageNotFound(Exception):
        pass

    def version(_name: str) -> str:
        if installed is None:
            raise PackageNotFound(_name)
        return installed

    setattr(
        module,
        "importlib_metadata",
        SimpleNamespace(version=version, PackageNotFoundError=PackageNotFound),
    )
    return module


# --- the discriminator ------------------------------------------------------


def test_the_exact_pin_against_a_newer_client_is_rejected(tmp_path, capsys) -> None:
    """The live 2026-08-31 fault. This is the whole point of the probe."""
    probe = _load_probe(tmp_path, GATE_EXACT_PIN, installed="0.9.2")
    assert probe.main() == 1
    assert "REJECT" in capsys.readouterr().out


def test_the_minimum_gate_against_the_same_client_is_accepted(tmp_path) -> None:
    """The post-apa#627 state. Together with the test above this is the
    discriminator: a probe that could not tell these two apart would have been
    green straight through the outage."""
    probe = _load_probe(tmp_path, GATE_MINIMUM, installed="0.9.2")
    assert probe.main() == 0


# --- it must not hardcode a version of its own ------------------------------


def test_the_requirement_is_read_from_the_gate_not_baked_in(tmp_path) -> None:
    """An upstream version bump must not silently turn the probe into a
    constant. Both sides move; the verdict must still be computed."""
    bumped_gate = GATE_MINIMUM.replace("0.6.1", "3.0.0")
    # Both sides of the boundary move to versions that appear nowhere in the
    # probe. If it carried a baked-in version, one of these two would misjudge.
    assert _load_probe(tmp_path, bumped_gate, installed="3.1.0").main() == 0
    assert _load_probe(tmp_path, bumped_gate, installed="2.9.9").main() == 1


# --- cannot-determine must alarm, not pass ----------------------------------


def test_an_unreadable_gate_is_cannot_determine(tmp_path) -> None:
    probe = _load_probe(tmp_path, None, installed="0.9.2")
    assert probe.main() == 2


def test_a_restructured_gate_is_cannot_determine(tmp_path) -> None:
    """If upstream reshapes the table the probe must say it cannot see, rather
    than match nothing and report health — the failure mode this whole role
    exists to stop."""
    probe = _load_probe(tmp_path, 'LAZY_DEPS = {"memory.hindsight": []}\n', installed="0.9.2")
    assert probe.main() == 2


def test_cannot_determine_is_not_an_accepted_code() -> None:
    """Guards the wiring, not the probe: ok_codes must admit 0 alone, so both
    exit 1 and exit 2 alarm."""
    target = next(t for t in DEFAULTS["fabric_watchdog_targets"] if t["name"] == "hindsight-dep-gate")
    assert target["ok_codes"] == "0"


def test_a_missing_distribution_is_rejected_not_unknown(tmp_path) -> None:
    probe = _load_probe(tmp_path, GATE_MINIMUM, installed=None)
    assert probe.main() == 1


@pytest.mark.parametrize(
    ("installed", "expected"),
    [("1.0.0", 0), ("1.0.1", 0), ("0.9.9", 1), ("1.0.0rc1", 1)],
)
def test_version_ordering_including_prereleases(tmp_path, installed, expected) -> None:
    """A prerelease must not satisfy a minimum it has not reached."""
    gate = GATE_MINIMUM.replace("0.6.1", "1.0.0")
    assert _load_probe(tmp_path, gate, installed=installed).main() == expected


# --- the command-target mechanism in the shell template ---------------------


def _render_shell(targets: list[dict]) -> str:
    return _env().from_string(SHELL_TEMPLATE).render(
        ansible_managed="ansible managed",
        fabric_watchdog_state_dir="/var/lib/fabric-watchdog",
        fabric_watchdog_probe_timeout=8,
        fabric_watchdog_down_after=3,
        fabric_watchdog_up_after=2,
        fabric_watchdog_targets=targets,
    )


def test_a_command_target_runs_its_command_and_never_curls() -> None:
    rendered = _render_shell([{"name": "probe", "cmd": "/bin/true", "ok_codes": "0"}])
    assert "/bin/true" in rendered
    assert "curl -sS -o /dev/null" not in rendered


def test_a_url_target_is_unchanged_by_the_command_branch() -> None:
    """The existing three targets must keep probing exactly as before."""
    rendered = _render_shell([{"name": "svc", "url": "https://x/health", "ok_codes": "200"}])
    assert "curl -sS -o /dev/null" in rendered
    assert "returned HTTP" in rendered


def test_both_target_kinds_share_the_debounce() -> None:
    """A command target must not bypass the hysteresis that exists because one
    blip must not be able to speak."""
    rendered = _render_shell([{"name": "probe", "cmd": "/bin/true", "ok_codes": "0"}])
    for required in (".streak", ".obs", "streak >= needed"):
        assert required in rendered


def test_the_command_targets_verdict_reaches_the_alert() -> None:
    """An alarm that says only that something happened forces a guest login to
    learn what. The probe already computes the verdict; carry it."""
    rendered = _render_shell([{"name": "probe", "cmd": "/bin/true", "ok_codes": "0"}])
    assert "${detail}" in rendered
    # ...and it must be JSON-safe, since it is interpolated into a Slack payload.
    assert "tr -d" in rendered


def test_the_probe_is_deployed_and_runs_under_the_hermes_venv() -> None:
    assert "hindsight-dep-gate.py.j2" in TASKS
    assert "/usr/local/bin/hindsight-dep-gate.py" in TASKS
    target = next(t for t in DEFAULTS["fabric_watchdog_targets"] if t["name"] == "hindsight-dep-gate")
    assert "fabric_watchdog_hermes_venv_python" in target["cmd"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
