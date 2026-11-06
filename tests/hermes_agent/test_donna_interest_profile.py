from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
PROFILE = ROLE / "files" / "donna-interest-profile.txt"
CONFIG_TASKS = yaml.safe_load((ROLE / "tasks" / "config_and_content.yml").read_text())


def _task(name: str) -> dict:
    result = next((task for task in CONFIG_TASKS if task.get("name") == name), None)
    assert result is not None, f"missing task: {name}"
    return result


def test_profile_entries_are_weighted_and_cover_each_requested_category() -> None:
    text = PROFILE.read_text()
    headings = list(
        re.finditer(r"(?m)^(Topics|Apps|Models|Tools|Vendors|People)\n-+\n", text)
    )
    assert [match.group(1) for match in headings] == [
        "Topics",
        "Apps",
        "Models",
        "Tools",
        "Vendors",
        "People",
    ]
    for index, match in enumerate(headings):
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else text.find("\nRecall this profile", match.end())
        )
        body = text[match.end() : end if end >= 0 else len(text)]
        entries = [line for line in body.splitlines() if line]
        assert entries, f"{match.group(1)} has no weighted entries"
        for entry in entries:
            weight = re.match(r"^(0\.\d{2}|1\.00) \| .+", entry)
            assert weight, f"{match.group(1)} entry has no valid weight: {entry}"


def test_profile_seeds_homelab_apps_models_and_requested_tools() -> None:
    text = PROFILE.read_text()
    for product in (
        "Hatchet",
        "Home Assistant",
        "Immich",
        "Nautobot",
        "Vikunja",
        "Zammad",
        "Qwen3.8-27B",
        "Qwen3-4B",
        "MiMo-9B",
        "BGE-M3",
        "llm-models.d",
        "Proxmox",
        "Nix",
        "Splunk",
        "Cribl",
        "LiteLLM",
        "Hermes",
        "Hindsight",
        "MCP",
    ):
        assert product.lower() in text.lower(), f"profile is missing {product}"


def test_profile_is_public_safe() -> None:
    text = PROFILE.read_text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b[\w-]+\.(?:com|net|org|io|ai|local|lan|internal)\b", text)
    for value in ("password", "api_key", "api key", "secret_id"):
        assert value not in text.lower()


def test_git_source_replaces_donnas_home_copy_on_converge() -> None:
    task = _task("Deploy Donna's editable interest profile")
    action = task["ansible.builtin.copy"]
    assert action["src"] == "donna-interest-profile.txt"
    assert "{{ hermes_agent_hermes_home }}/interest-profile.txt" == action["dest"]
    assert action["force"] is True
    assert task["when"] == "hermes_agent_id == 'donna'"


def test_hindsight_sync_updates_one_pinned_document_only_when_stale() -> None:
    task = _task("Seed Donna's Hindsight interest profile")
    assert "hermes_agent_id == 'donna'" in task["when"]
    assert "hermes_agent_memory_provider == 'hindsight'" in task["when"]
    assert any("hermes_agent_hindsight_config_api_url" in item for item in task["when"])
    assert any("hermes_agent_hindsight_config_api_key" in item for item in task["when"])
    read = next(
        block
        for block in task["block"]
        if block["name"] == "Read Donna's Hindsight interest profile document"
    )
    write = next(
        block
        for block in task["block"]
        if block["name"] == "Retain Donna's pinned interest profile in Hindsight"
    )
    assert read["ansible.builtin.uri"]["method"] == "GET"
    assert (
        read["ansible.builtin.uri"]["url"]
        == "{{ hermes_agent_donna_interest_document_url }}"
    )
    assert (
        "/documents/donna-interest-profile"
        in task["vars"]["hermes_agent_donna_interest_document_url"]
    )
    assert (
        "hermes_agent_hindsight_config_api_url"
        in task["vars"]["hermes_agent_donna_interest_document_url"]
    )
    assert write["ansible.builtin.uri"]["method"] == "POST"
    assert (
        write["ansible.builtin.uri"]["url"]
        == "{{ hermes_agent_donna_interest_memory_url }}"
    )
    assert (
        "/files/donna-interest-profile.txt"
        in task["vars"]["hermes_agent_donna_interest_profile_content"]
    )
    assert (
        write["ansible.builtin.uri"]["body"]["items"][0]["document_id"]
        == "donna-interest-profile"
    )
    assert (
        "hermes_agent_hindsight_config_api_key"
        in write["ansible.builtin.uri"]["headers"]["Authorization"]
    )
    assert "pinned" in write["ansible.builtin.uri"]["body"]["items"][0]["tags"]
    assert write["no_log"] is True
    assert "not ansible_check_mode" in write["when"]
    assert "original_text" in " ".join(write["when"])
