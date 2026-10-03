"""Self-checks for the repo-crawl monitor-mode pre-check script
(templates/hermes-repo-crawl.py.j2): the wakeAgent gate, hash-based dedup
across runs, and the open-PR-queue cap.
"""
from __future__ import annotations

import json

from _repo_crawl_shared import load_module


def _last_gate_line(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "the script must always print a final JSON gate line"
    return json.loads(out[-1])


def test_no_repos_configured_is_a_noop(tmp_path, monkeypatch, capsys):
    mod = load_module(hermes_agent_hermes_home=str(tmp_path), hermes_agent_repo_crawl_repos=[])

    def boom(*_args, **_kwargs):
        raise AssertionError("must not touch gh/detect.py with an empty repo list")

    monkeypatch.setattr(mod, "checkout", boom)
    monkeypatch.setattr(mod, "run_detect", boom)
    assert mod.main() == 0
    assert _last_gate_line(capsys) == {"wakeAgent": False}


def test_finding_hash_prefers_explicit_id():
    mod = load_module()
    assert mod.finding_hash({"id": "abc"}) == "abc"
    h1 = mod.finding_hash({"check": "D6", "msg": "x"})
    h2 = mod.finding_hash({"check": "D6", "msg": "x"})
    h3 = mod.finding_hash({"check": "D6", "msg": "y"})
    assert h1 == h2
    assert h1 != h3


def test_new_finding_wakes_then_is_suppressed_on_repeat(tmp_path, monkeypatch, capsys):
    mod = load_module(hermes_agent_hermes_home=str(tmp_path))
    monkeypatch.setattr(mod, "checkout", lambda _repo, _boundary, _dest: None)
    monkeypatch.setattr(mod, "run_detect", lambda _dest, _repo: [{"check": "D6", "msg": "missing linter"}])
    monkeypatch.setattr(mod, "open_pr_count", lambda: 0)

    assert mod.main() == 0
    first = _last_gate_line(capsys)
    assert first["wakeAgent"] is True
    assert first["context"]["new_findings"] == 1
    with open(mod.FINDINGS_PATH) as f:
        assert len(json.load(f)) == 1

    assert mod.main() == 0
    second = _last_gate_line(capsys)
    assert second == {"wakeAgent": False}


def test_pr_queue_full_suppresses_the_wake(tmp_path, monkeypatch, capsys):
    mod = load_module(hermes_agent_hermes_home=str(tmp_path), hermes_agent_repo_crawl_max_open_prs=3)
    monkeypatch.setattr(mod, "checkout", lambda _repo, _boundary, _dest: None)
    monkeypatch.setattr(mod, "run_detect", lambda _dest, _repo: [{"check": "D6", "msg": "x"}])
    monkeypatch.setattr(mod, "open_pr_count", lambda: 3)

    assert mod.main() == 0
    gate = _last_gate_line(capsys)
    assert gate["wakeAgent"] is False
    assert gate["context"]["reason"] == "pr-queue-full"


def test_detect_failure_is_loud_not_silent(tmp_path, monkeypatch, capsys):
    """A crawl failure (e.g. detect.py missing markdownlint-cli2) must exit
    non-zero and still gate the agent off — never report zero findings as if
    the crawl had actually run cleanly."""
    mod = load_module(hermes_agent_hermes_home=str(tmp_path))
    monkeypatch.setattr(mod, "checkout", lambda _repo, _boundary, _dest: None)

    def failing_detect(_dest, _repo):
        raise mod.CrawlError("detect.py exited 1: markdownlint-cli2: command not found")

    monkeypatch.setattr(mod, "run_detect", failing_detect)
    assert mod.main() == 1
    captured = capsys.readouterr()
    assert "markdownlint-cli2" in captured.err
    assert json.loads(captured.out.strip().splitlines()[-1]) == {"wakeAgent": False}


def test_open_pr_count_is_zero_without_an_app_slug():
    mod = load_module(hermes_agent_github_app_slug="")
    assert mod.open_pr_count() == 0


def test_detect_argv_matches_the_detector_cli():
    mod = load_module()
    argv = mod.detect_argv("/tmp/checkout", "dryvist/example")
    assert argv[1].endswith("skills/dryvist/repo-crawl/scripts/detect.py")
    assert argv[2:] == ["--repo", "/tmp/checkout", "--repo-name", "dryvist/example"]


def test_queue_full_keeps_findings_for_the_next_run(tmp_path, monkeypatch, capsys):
    mod = load_module(hermes_agent_hermes_home=str(tmp_path), hermes_agent_repo_crawl_max_open_prs=3)
    monkeypatch.setattr(mod, "checkout", lambda _repo, _boundary, _dest: None)
    monkeypatch.setattr(mod, "run_detect", lambda _dest, _repo: [{"check": "D6", "msg": "x"}])
    monkeypatch.setattr(mod, "open_pr_count", lambda: 3)
    assert mod.main() == 0
    assert _last_gate_line(capsys)["wakeAgent"] is False

    monkeypatch.setattr(mod, "open_pr_count", lambda: 0)
    assert mod.main() == 0
    assert _last_gate_line(capsys)["wakeAgent"] is True


def test_open_pr_count_failure_fails_closed(tmp_path, monkeypatch, capsys):
    mod = load_module(hermes_agent_hermes_home=str(tmp_path))
    monkeypatch.setattr(mod, "checkout", lambda _repo, _boundary, _dest: None)
    monkeypatch.setattr(mod, "run_detect", lambda _dest, _repo: [{"check": "D6", "msg": "x"}])

    def failing_count():
        raise mod.CrawlError("gh search failed")

    monkeypatch.setattr(mod, "open_pr_count", failing_count)
    assert mod.main() == 1
    captured = capsys.readouterr()
    assert "gh search failed" in captured.err
    assert json.loads(captured.out.strip().splitlines()[-1]) == {"wakeAgent": False}
