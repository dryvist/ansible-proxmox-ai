"""Model campaign scope rules for the contract scope selector."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_PYTEST_TARGET = "tests/llm_model_campaign/"
CAMPAIGN_SUITE_DIR = REPO_ROOT / CAMPAIGN_PYTEST_TARGET

# Paths the model campaign owns. The campaign test suite and the template and
# vars fragments are campaign scope whatever their extension; the top-level
# campaign playbooks and the task fragments are campaign scope as Ansible
# YAML.
_CAMPAIGN_PREFIXES = (
    "playbooks/templates/llm-model-campaign",
    "playbooks/vars/llm-model-campaign",
    CAMPAIGN_PYTEST_TARGET,
)
_CAMPAIGN_YAML_PREFIXES = (
    "playbooks/llm-model-campaign",
    "playbooks/llm-model-paired-campaign",
    "playbooks/tasks/llm-model-campaign",
)


def is_campaign_path(path: str) -> bool:
    """True for a model campaign playbook, fragment or test file."""
    if path.startswith(_CAMPAIGN_PREFIXES):
        return True
    return path.endswith((".yml", ".yaml")) and path.startswith(_CAMPAIGN_YAML_PREFIXES)


def _named_outside_the_campaign_suite(names: set[str]) -> bool:
    """True when a test outside the campaign suite names one of the files.

    Router playbooks import campaign task files and the GPU contract tests
    load campaign playbooks, so a named campaign file keeps the selection it
    has today instead of the narrowed campaign scope. The campaign suite's
    own references are what the campaign target already covers, and the
    selector's own tests name campaign paths without reading them.
    """
    tests = REPO_ROOT / "tests"
    if not tests.is_dir():
        return False
    return any(
        name in reader.read_text(errors="ignore")
        for reader in tests.rglob("*")
        if reader.is_file()
        and not reader.name.startswith("test_contract_scope")
        and not reader.is_relative_to(CAMPAIGN_SUITE_DIR)
        for name in names
    )


def campaign_scope_applies(paths: list[str]) -> bool:
    """True when every changed path is a campaign file no other test reads."""
    changed = [path.removeprefix("./") for path in paths]
    if not changed or not all(is_campaign_path(path) for path in changed):
        return False
    return not _named_outside_the_campaign_suite({Path(path).name for path in changed})


def campaign_selection() -> dict[str, object]:
    """The focused selection for a change set of campaign files only."""
    return {
        "pytest_targets": [CAMPAIGN_PYTEST_TARGET],
        "ansible_tests": [],
        "llm_router_playbooks": [],
        "run_inventory": False,
        "run_selector_checks": False,
    }
