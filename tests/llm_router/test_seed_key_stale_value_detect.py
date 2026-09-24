"""seed-keys.yml's auto-detected stale-value rotation.

llm_router_rotate_key_aliases (test_seed_key_rotation_switch.py) covers an
operator who KNOWS a value changed. This covers the case nobody remembered
to list it there: a live alias whose CURRENT OpenBao value 404s against
/key/info means the stored value no longer matches whatever the proxy
actually minted, and without this the key is orphaned forever (the create
step never touches an existing alias; the reconcile steps look the key up
BY VALUE, which no longer matches anything).

Pins the shape rather than mocking a live proxy, matching
test_seed_key_rotation_switch.py's source-assertion style for this file, plus
a Python reimplementation of the three-case merge logic (live+matching,
live+stale, absent) since that logic is pure list filtering with no Jinja
runtime worth invoking for it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "llm_router"

SEED_KEYS_SOURCE = (ROLE_ROOT / "tasks" / "seed-keys.yml").read_text()


def test_probe_reads_key_info_with_the_current_value() -> None:
    assert "/key/info?key={{ item.value }}" in SEED_KEYS_SOURCE
    assert "status_code: [200, 404]" in SEED_KEYS_SOURCE


def test_probe_only_targets_aliases_already_live() -> None:
    """An alias that was never seeded at all is not this mechanism's job —
    the unconditional create step below already handles it as a plain
    mint, and probing it here would just be a guaranteed 404 for no
    reason."""
    assert "selectattr('alias', 'in', _llm_router_live_key_aliases) | list }}" in SEED_KEYS_SOURCE


def test_probe_result_is_no_log() -> None:
    """The URL embeds the raw OpenBao value in a query string."""
    probe_idx = SEED_KEYS_SOURCE.index("Probe each live alias")
    next_task_idx = SEED_KEYS_SOURCE.index("Compute which live aliases hold a stale")
    probe_block = SEED_KEYS_SOURCE[probe_idx:next_task_idx]
    assert "no_log: true" in probe_block


def test_stale_aliases_are_computed_from_a_404_status() -> None:
    assert "selectattr('status', 'equalto', 404)" in SEED_KEYS_SOURCE
    assert "map(attribute='item.alias') | list }}" in SEED_KEYS_SOURCE


def test_stale_aliases_are_folded_into_the_existing_rotate_list() -> None:
    """One mechanism, two feeds — this must not become a second, parallel
    delete-then-mint path; it has to land in the same
    llm_router_rotate_key_aliases the existing delete task already
    consumes."""
    fold_idx = SEED_KEYS_SOURCE.index("Fold auto-detected stale aliases")
    delete_idx = SEED_KEYS_SOURCE.index("Delete the live key for every alias flagged for rotation")
    assert fold_idx < delete_idx
    fold_block = SEED_KEYS_SOURCE[fold_idx:delete_idx]
    assert "llm_router_rotate_key_aliases:" in fold_block
    assert "_llm_router_stale_key_aliases" in fold_block


def test_detection_runs_before_the_existing_delete_and_reread() -> None:
    read_idx = SEED_KEYS_SOURCE.index("Read the virtual keys the proxy already carries")
    probe_idx = SEED_KEYS_SOURCE.index("Probe each live alias")
    fold_idx = SEED_KEYS_SOURCE.index("Fold auto-detected stale aliases")
    delete_idx = SEED_KEYS_SOURCE.index("Delete the live key for every alias flagged for rotation")
    reread_idx = SEED_KEYS_SOURCE.index("Re-read the live keys when a rotation delete ran")
    assert read_idx < probe_idx < fold_idx < delete_idx < reread_idx


def test_report_task_never_names_a_value() -> None:
    report_idx = SEED_KEYS_SOURCE.index("Report auto-detected stale key aliases")
    fold_idx = SEED_KEYS_SOURCE.index("Fold auto-detected stale aliases")
    report_block = SEED_KEYS_SOURCE[report_idx:fold_idx]
    assert "item.value" not in report_block
    assert "_llm_router_stale_key_aliases | join(', ') }}" in report_block


# --- Three-case merge logic, reimplemented in plain Python -----------------
#
# Jinja's selectattr/map chain over the probe results is pure list
# filtering; no Ansible runtime is worth spinning up to prove it, so this
# mirrors it directly against the same three cases the fix has to get right.


def _stale_aliases(probe_results: list[dict]) -> list[str]:
    """Mirrors: selectattr('status', 'equalto', 404) | map(attribute='item.alias')"""
    return [r["item"]["alias"] for r in probe_results if r["status"] == 404]


def _rotate_list(existing: list[str], stale: list[str]) -> list[str]:
    """Mirrors: (llm_router_rotate_key_aliases | default([]) + stale) | unique"""
    merged = list(existing)
    for alias in stale:
        if alias not in merged:
            merged.append(alias)
    return merged


def test_live_and_matching_value_is_left_untouched() -> None:
    probe_results = [{"item": {"alias": "prometheus-scrape"}, "status": 200}]
    assert _stale_aliases(probe_results) == []
    assert _rotate_list([], _stale_aliases(probe_results)) == []


def test_live_and_stale_value_is_flagged_for_delete_and_regenerate() -> None:
    probe_results = [{"item": {"alias": "prometheus-scrape"}, "status": 404}]
    assert _stale_aliases(probe_results) == ["prometheus-scrape"]
    assert _rotate_list([], _stale_aliases(probe_results)) == ["prometheus-scrape"]


def test_an_alias_not_live_at_all_is_never_probed_and_never_flagged() -> None:
    """The loop's own selectattr already excludes it, so an absent alias
    can't appear in probe_results at all — it reaches the unconditional
    create step untouched, as a plain first mint."""
    probe_results: list[dict] = []
    assert _stale_aliases(probe_results) == []
    assert _rotate_list([], _stale_aliases(probe_results)) == []


def test_operator_named_and_auto_detected_aliases_merge_without_duplication() -> None:
    probe_results = [
        {"item": {"alias": "prometheus-scrape"}, "status": 404},
        {"item": {"alias": "github-actions"}, "status": 200},
    ]
    operator_named = ["hindsight"]
    result = _rotate_list(operator_named, _stale_aliases(probe_results))
    assert result == ["hindsight", "prometheus-scrape"]
