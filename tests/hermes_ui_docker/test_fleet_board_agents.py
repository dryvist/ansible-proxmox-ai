"""Static contract for the fleet board's first administrator and agent registration."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_ui_docker"
DEFAULTS = ROLE / "defaults" / "main.yml"
MAIN = ROLE / "tasks" / "main.yml"
ADMIN = ROLE / "tasks" / "paperclip_admin.yml"
AGENTS = ROLE / "tasks" / "paperclip_agents.yml"
COMPOSE = ROLE / "templates" / "docker-compose.yml.j2"
EXPECTED = [
    ("vikunja-clerk", "Vikunja Clerk"),
    ("vikunja-triage", "Vikunja Triage"),
    ("vikunja-nightly", "Vikunja Nightly"),
    ("vikunja-windows", "Vikunja Windows"),
    ("youtrack-clerk", "YouTrack Clerk"),
    ("zammad-clerk", "Zammad Clerk"),
]
SECRET_WORDS = ("password", "cookie", "apiKey", "pcp_bootstrap", "bootstrap_argv")


def _flat(tasks):
    for task in tasks or []:
        if "block" in task:
            yield from _flat(task["block"])
        else:
            yield task


def _tasks(path):
    return list(_flat(yaml.safe_load(path.read_text(encoding="utf-8"))))


def test_six_agents_are_declared_once_with_three_fields():
    agents = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))["hermes_ui_docker_paperclip_agents"]
    assert [(a["name"], a["title"]) for a in agents] == EXPECTED
    assert all(set(a) == {"name", "profile_path", "title"} for a in agents)
    assert [a["profile_path"] for a in agents] == [f"/p/{name}" for name, _ in EXPECTED]


def test_switches_default_off_and_sign_up_is_closed():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert defaults["hermes_ui_docker_paperclip_agents_enabled"] is False
    assert defaults["hermes_ui_docker_paperclip_sign_up_open"] is False
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "PAPERCLIP_AUTH_DISABLE_SIGN_UP" in compose
    assert "hermes_ui_docker_paperclip_sign_up_open" in compose


def test_registration_is_included_behind_both_switches_and_never_in_check_mode():
    text = MAIN.read_text(encoding="utf-8")
    assert "paperclip_admin.yml" in text and "paperclip_agents.yml" in text
    assert "hermes_ui_docker_paperclip_agents_enabled" in text
    assert text.index("Verify paperclip health endpoint") < text.index("paperclip_admin.yml")
    assert text.index("paperclip_admin.yml") < text.index("paperclip_agents.yml")


def test_no_task_changes_or_deletes_a_board_row():
    for path in (ADMIN, AGENTS):
        for task in _tasks(path):
            method = str(task.get("ansible.builtin.uri", {}).get("method", "GET")).upper()
            assert method in {"GET", "POST"}, task["name"]


def test_tasks_that_carry_a_secret_hide_their_output():
    for path in (ADMIN, AGENTS):
        for task in _tasks(path):
            body = yaml.safe_dump({k: v for k, v in task.items() if k != "name"})
            if any(word in body for word in SECRET_WORDS):
                assert task.get("no_log") is True, task["name"]


def test_first_administrator_needs_the_open_switch_and_the_container_command():
    text = ADMIN.read_text(encoding="utf-8")
    for needle in (
        "/api/auth/sign-in/email",
        "/api/auth/sign-up/email",
        "hermes_ui_docker_paperclip_sign_up_open",
        "hermes_ui_docker_paperclip_bootstrap_argv",
        "pcp_bootstrap_[0-9a-f]+",
        "/accept",
        "requestType",
    ):
        assert needle in text, needle
    assert text.index("/api/auth/sign-in/email") < text.index("/api/auth/sign-up/email")


def test_existing_rows_are_compared_before_any_agent_is_created():
    tasks = _tasks(AGENTS)
    names = [task["name"] for task in tasks]
    posts = [
        i for i, task in enumerate(tasks)
        if str(task.get("ansible.builtin.uri", {}).get("method", "GET")).upper() == "POST"
        and "/agents" in str(task["ansible.builtin.uri"].get("url", ""))
    ]
    guards = [
        i for i, task in enumerate(tasks)
        if "ansible.builtin.assert" in task or "ansible.builtin.fail" in task
    ]
    assert posts and guards, names
    assert min(guards) < min(posts), names
    text = AGENTS.read_text(encoding="utf-8")
    for needle in ("adapterType", "hermes_gateway", "apiBaseUrl", "profile_path", "title"):
        assert needle in text, needle
    assert "REDACTED" not in text


def test_reads_never_report_a_change():
    for path in (ADMIN, AGENTS):
        for task in _tasks(path):
            uri = task.get("ansible.builtin.uri")
            if uri and str(uri.get("method", "GET")).upper() == "GET":
                assert task.get("changed_when") is False, task["name"]
