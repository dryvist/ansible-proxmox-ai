from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from test_profile_contract import ROLE_ROOT, _defaults, _jinja_env, _profiles


def test_profile_soul_addendum_sources_are_declared_in_profile_data() -> None:
    profiles = {profile["name"]: profile for profile in _profiles()}
    bundle_names = {"github-maint", "homelab-admin", "splunk-admin"}

    assert {
        name for name, profile in profiles.items() if profile.get("soul_addendum_bundle")
    } == bundle_names
    assert all("soul_addendum_file" not in profiles[name] for name in bundle_names)
    assert profiles["orchestrator"]["soul_addendum_file"] == "soul-orchestrator.md.j2"
    assert profiles["orchestrator"]["soul_addendum_bundle_extra_file"] == "kanban-lifecycle.md"
    assert all(
        "soul_addendum_bundle_extra_file" not in profiles[name]
        for name in bundle_names
    )


def test_bundle_profile_soul_is_bundle_base_plus_bundle_addendum(tmp_path: Path) -> None:
    profile = next(profile for profile in _profiles() if profile["name"] == "github-maint")
    bundle_path = _fake_soul_bundle(tmp_path)

    rendered = _render_profile_soul(profile, bundle_path)

    assert rendered == "Shared bundle SOUL.\n\nGitHub maintenance bundle addendum."


def test_orchestrator_soul_includes_local_addendum_and_bundle_lifecycle(tmp_path: Path) -> None:
    profile = next(profile for profile in _profiles() if profile["name"] == "orchestrator")
    bundle_path = _fake_soul_bundle(tmp_path)

    rendered = _render_profile_soul(profile, bundle_path)
    orchestrator_addendum = _jinja_env().from_string(
        (ROLE_ROOT / "templates" / profile["soul_addendum_file"]).read_text()
    ).render(
        hermes_agent_kanban_eligible_label=_defaults()[
            "hermes_agent_kanban_eligible_label"
        ]
    )

    assert rendered == (
        "Shared bundle SOUL.\n\n"
        f"{orchestrator_addendum}\n\n"
        "Kanban lifecycle bundle addendum."
    )
    assert "Only pull Vikunja tasks labelled `hermes-eligible`" in rendered
    assert "Kanban lifecycle bundle addendum." in rendered


def test_only_orchestrator_soul_contains_bundle_lifecycle(tmp_path: Path) -> None:
    bundle_path = _fake_soul_bundle(tmp_path)
    rendered = {
        profile["name"]: _render_profile_soul(profile, bundle_path)
        for profile in _profiles()
    }

    assert "Kanban lifecycle bundle addendum." in rendered["orchestrator"]
    assert all(
        "Kanban lifecycle bundle addendum." not in soul
        for name, soul in rendered.items()
        if name != "orchestrator"
    )


def _fake_soul_bundle(bundle_path: Path) -> Path:
    profiles_path = bundle_path / "soul" / "profiles"
    profiles_path.mkdir(parents=True)
    (bundle_path / "SOUL.md").write_text("Shared bundle SOUL.")
    (profiles_path / "github-maint.md").write_text("GitHub maintenance bundle addendum.")
    (profiles_path / "homelab-admin.md").write_text("Homelab admin bundle addendum.")
    (profiles_path / "splunk-admin.md").write_text("Splunk admin bundle addendum.")
    (profiles_path / "kanban-lifecycle.md").write_text("Kanban lifecycle bundle addendum.")
    return bundle_path


def _render_profile_soul(profile: dict[str, Any], bundle_path: Path) -> str:
    env = _jinja_env()
    env.trim_blocks = True
    context: dict[str, Any] = {
        **_defaults(),
        "item": profile,
        "role_path": str(ROLE_ROOT),
        "hermes_agent_bundle_path": str(bundle_path),
        "hermes_agent_kanban_eligible_label": _defaults()[
            "hermes_agent_kanban_eligible_label"
        ],
    }

    def lookup(plugin: str, source: str) -> str:
        path = Path(source)
        if plugin == "ansible.builtin.file":
            return path.read_text().rstrip()
        if plugin == "ansible.builtin.template":
            return env.from_string(path.read_text()).render(**context)
        raise AssertionError(f"unexpected lookup plugin: {plugin}")

    env.globals["lookup"] = lookup
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "profiles.yml").read_text())
    task = next(task for task in tasks if task["name"].startswith("Deploy composed SOUL.md"))
    content = task["ansible.builtin.copy"]["content"]
    return env.from_string(content).render(**context)
