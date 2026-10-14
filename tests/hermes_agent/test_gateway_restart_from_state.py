"""Restart the gateway when its input contents differ from the saved digest."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import yaml
from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
ENV = Environment()
ENV.filters["hash"] = lambda value, algorithm: hashlib.new(
    algorithm, str(value).encode()
).hexdigest()
ENV.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
PROBE = "Restart a gateway whose inputs differ from the recorded digest"
FLUSH = "Flush handlers so config/unit changes apply before verification"


def _tasks() -> list[dict]:
    return yaml.safe_load((ROLE / "tasks" / "finalize.yml").read_text())


def _task(name: str) -> dict:
    return next(task for task in _tasks() if task["name"] == name)


def _expression(template: str):
    return ENV.compile_expression(
        template.strip().removeprefix("{{").removesuffix("}}")
    )


def _input_paths() -> list[str]:
    task = next(task for task in _tasks() if task["name"] == "Stat the gateway input files")
    return [
        ENV.from_string(path).render(hermes_agent_hermes_home="/srv/hermes")
        for path in task["loop"]
    ]


def _digest(
    checksums: list[str | None],
    paths: list[str] | None = None,
    mtimes: list[float | None] | None = None,
) -> str:
    task = next(
        task for task in _tasks() if task["name"] == "Set the desired gateway input digest"
    )
    paths = paths or _input_paths()
    mtimes = mtimes or [None] * len(paths)
    results = []
    for path, checksum, mtime in zip(paths, checksums, mtimes, strict=True):
        stat = {"exists": checksum is not None, "mtime": mtime}
        if checksum is not None:
            stat["checksum"] = checksum
        results.append({"item": path, "stat": stat})
    context = {"hermes_agent_gateway_input_stats": {"results": results}}
    return _expression(
        task["ansible.builtin.set_fact"]["hermes_agent_gateway_desired_digest"]
    )(**context)


def _changed(
    marker_exists: bool,
    recorded_digest: str,
    checksums: list[str | None],
    mtimes: list[float | None] | None = None,
    *,
    check_mode: bool = False,
) -> bool:
    task = _task(PROBE)
    paths = _input_paths()
    mtimes = mtimes or [None] * len(paths)
    results = []
    for path, checksum, mtime in zip(paths, checksums, mtimes, strict=True):
        stat = {"exists": checksum is not None, "mtime": mtime}
        if checksum is not None:
            stat["checksum"] = checksum
        results.append({"item": path, "stat": stat})
    encoded = base64.b64encode(recorded_digest.encode()).decode()
    context = {
        "hermes_agent_gateway_input_stats": {"results": results},
        "hermes_agent_gateway_marker_stat": {"stat": {"exists": marker_exists}},
        "hermes_agent_gateway_marker_content": {"content": encoded},
        "hermes_agent_gateway_desired_digest": _digest(checksums),
        "ansible_check_mode": check_mode,
    }
    for name, template in task.get("vars", {}).items():
        context[name] = _expression(template)(**context)
    return bool(ENV.compile_expression(task["changed_when"])(**context))


def test_probe_notifies_the_gateway_restart_before_the_flush():
    names = [task["name"] for task in _tasks()]
    assert names.index(PROBE) < names.index(FLUSH)
    assert _task(PROBE)["notify"] == "Restart hermes-gateway"


def test_inputs_are_statted_with_sha256_and_missing_files_are_allowed():
    stat = next(task for task in _tasks() if task["name"] == "Stat the gateway input files")
    assert stat["ansible.builtin.stat"]["get_checksum"] is True
    assert stat["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
    assert "failed_when" not in stat


def test_missing_marker_restarts_the_gateway():
    assert _changed(False, "", ["env", "config", "unit"])


def test_empty_old_format_marker_restarts_the_gateway():
    assert _changed(True, "", ["env", "config", "unit"])


def test_matching_digest_does_not_restart_the_gateway():
    checksums = ["env", "config", "unit"]
    assert not _changed(True, _digest(checksums), checksums)


def test_digest_includes_each_input_path():
    checksums = ["env", "config", "unit"]
    paths = _input_paths()
    swapped_path = ["/srv/hermes/renamed.env", *paths[1:]]
    assert _digest(checksums, paths) != _digest(checksums, swapped_path)


def test_changed_input_checksum_restarts_the_gateway():
    before = ["env", "config", "unit"]
    after = ["env", "changed-config", "unit"]
    assert _changed(True, _digest(before), after)


def test_identical_content_with_newer_mtimes_does_not_restart():
    checksums = ["env", "config", "unit"]
    assert not _changed(
        True,
        _digest(checksums),
        checksums,
        mtimes=[101.0, 102.0, 103.0],
    )


def test_handler_digest_makes_the_second_pass_unchanged():
    checksums = ["env", "config", "unit"]
    digest = _digest(checksums)
    assert _changed(False, "", checksums)
    assert not _changed(True, digest, checksums, mtimes=[110.0, 111.0, 112.0])
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    stamp = next(handler for handler in handlers if handler["name"] == "Record the gateway restart")
    assert stamp["listen"] == "Restart hermes-gateway"
    assert stamp["ansible.builtin.copy"]["content"] == "{{ hermes_agent_gateway_desired_digest }}"


def test_missing_input_changes_digest_only_when_its_presence_changes():
    missing = ["env", "config", None]
    present = ["env", "config", "unit"]
    assert _digest(missing, mtimes=[10.0, 20.0, None]) == _digest(
        missing, mtimes=[110.0, 120.0, None]
    )
    assert _digest(missing) != _digest(present)
    assert not _changed(True, _digest(missing), missing, mtimes=[1.0, 2.0, None])


def test_check_mode_reports_a_pending_restart_as_changed():
    checksums = ["env", "config", "unit"]
    assert _changed(False, "", checksums, check_mode=True)
    assert not _changed(True, _digest(checksums), checksums, check_mode=True)


def test_restart_marker_is_read_only_and_handler_writes_the_digest():
    read = next(task for task in _tasks() if task["name"] == "Read the recorded gateway digest")
    assert read["failed_when"] is False
    assert read["check_mode"] is False
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    stamp = next(handler for handler in handlers if handler["name"] == "Record the gateway restart")
    copy = stamp["ansible.builtin.copy"]
    assert copy["path"] == "{{ hermes_agent_gateway_restart_marker }}"
    assert copy["owner"] == "root"
    assert copy["group"] == "root"
    assert copy["mode"] == "0600"
