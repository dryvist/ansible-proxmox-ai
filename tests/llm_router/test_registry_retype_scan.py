"""A registry value written anywhere but the registry is a build failure.

The registry rule (AGENTS.md, llm_router): every model id and alias is written
ONCE, in llm-models.d/, and the role, its tests and the playbooks DERIVE it. The
render-parity guard catches a name that is rendered but unregistered; it cannot
catch the opposite input — a REGISTERED id re-typed as a literal — because that
input renders correctly today and drifts silently the day the registry entry
changes. This scan is what fails on that input.

Two zones, because the rule has two sides:

* The PROJECTION zone — roles/llm_router, tests/llm_router, playbooks, and
  Python tests — may not carry registry model ids or aliases in model, alias,
  or fallback values. The one exception is not hand-listed:
  values selected by `inventory/group_vars` under `hermes_brain_model` are the
  consumer-selection contract. The set is computed from that selector's
  literal value or quoted fallback, so changing the selector changes the
  exemption with it.
* The CONSUMER zone — every other role's defaults/vars, inventory, the other
  tests — calls the fabric by a client-facing name. It may name a
  client_model_id or an alias; it may never name an upstream-only id, because
  that is the physical backend the router exists to hide.

Parsed YAML values and Python string constants are scanned, never raw text: a
comment or docstring naming a model is prose, not a re-type. A value counts
when a scalar IS the value, ends in
`/<value>` (a provider-prefixed form), or carries it as a quoted token inside a
Jinja expression (`selectattr(..., 'equalto', '<value>')`, `['<value>', ...]`).
Unquoted prose inside a message is not matched.

Role-name aliases (stable_aliases on a non-servable, non-long-context
entry — the seeded DB roles, 55-roles.yml) are excluded from the alias set
by the registry's own fields, not by a list here: they are role names, not
model_group aliases, and the role seed necessarily carries the name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from _registry_retype_scan import REPO_ROOT, _Permissive, _literals, registry_values, scan


def test_zdr_true_registry_entries_name_the_provider_source() -> None:
    """An explicit ZDR claim must carry its source next to the field."""
    source_comments = (
        "OpenRouter request-level `zdr: true` (provider routing)",
        "OpenCode Go 0-day retention contract",
    )
    registry_files = sorted((REPO_ROOT / "llm-models.d").glob("*.yml"))
    assert registry_files
    for path in registry_files:
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if re.match(r"^\s*zero_data_retention:\s*true\s*$", line):
                nearby = lines[max(0, index - 2) : index]
                assert any(
                    source_comment in candidate
                    for candidate in nearby
                    for source_comment in source_comments
                ), (
                    f"{path.relative_to(REPO_ROOT)}:{index + 1} has no provider source comment"
                )


def test_virtual_key_settings_reconcile_only_when_referenced() -> None:
    """Key settings seed on create and reconcile only for keys naming a settings entry."""
    seed_file = REPO_ROOT / "roles/llm_router/tasks/seed-keys.yml"
    tasks = yaml.load(seed_file.read_text(), Loader=_Permissive)
    by_name = {task.get("name"): task for task in tasks}
    create = by_name["Create the caller keys that have a seeded value (an existing alias is not a failure)"]
    create_body = create["ansible.builtin.uri"]["body"]
    assert "item.router_settings" in create_body["router_settings"]
    assert "item.tags" in create_body["metadata"]
    assert "item.sensitivity_tag" in create_body["metadata"]

    policy_file = REPO_ROOT / "roles/llm_router/tasks/reconcile-seeded-key-policy.yml"
    policy_tasks = yaml.load(policy_file.read_text(), Loader=_Permissive)
    policy_by_name = {task.get("name"): task for task in policy_tasks}
    compute = policy_by_name["Compute the key policy update each live key still needs, if any"]
    reconcile_expr = compute["ansible.builtin.set_fact"]["_llm_router_key_updates"]
    assert "item.tags" not in reconcile_expr
    assert "_sensitivity_tag" in reconcile_expr
    assert "llm_router_review_key_router_settings[_k.router_settings_ref]" in reconcile_expr
    assert "_router_settings_changed" in reconcile_expr
    assert "combine({'router_settings': _router_settings_target})" in reconcile_expr

    update = policy_by_name["Reconcile caller policy for live keys that changed"]
    assert update["ansible.builtin.uri"]["body"] == "{{ item.body }}"



def test_registry_loads_something():
    """Anti-vacuity: an empty registry would make both scans pass with nothing scanned."""
    values = registry_values(REPO_ROOT)
    assert len(values) >= 10, f"only {len(values)} registry values loaded; the loader is broken"
    assert any("alias" in kinds for kinds in values.values()), "no servable alias loaded"
    assert any(kinds == {"upstream"} for kinds in values.values()), "no upstream-only id loaded"


def test_projection_zone_carries_no_registry_literal():
    offenders, _ = scan(REPO_ROOT)
    assert not offenders, (
        "Registry values re-typed where they must be derived (llm_router role, its "
        "tests, playbooks, Python tests). Use the registry projection — "
        "llm_router_primary_model, llm_router_model_group_aliases, the tier lists — "
        "or read llm-models.d/ and select by serving_role/tier, never the literal:\n  "
        + "\n  ".join(offenders)
    )


def test_consumer_zone_names_no_upstream_only_id():
    _, offenders = scan(REPO_ROOT)
    assert not offenders, (
        "A consumer names a physical upstream id the router exists to hide. Call "
        "the client_model_id or an alias:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_can_see_a_planted_literal(tmp_path: Path):
    """Anti-vacuity: a clean tree must mean 'none found', not 'nothing matched'."""
    (tmp_path / "llm-models.d").mkdir()
    (tmp_path / "llm-models.d" / "10.yml").write_text(
        "_r:\n"
        "  - {client_model_id: org/planted-model, upstream_model_id: org/planted-model,"
        " servable: true, stable_aliases: [planted-alias]}\n"
        "  - {client_model_id: cloud-rung, upstream_model_id: vendor/physical-name}\n"
    )
    defaults = tmp_path / "roles/llm_router/defaults/main"
    defaults.mkdir(parents=True)
    (defaults / "x.yml").write_text(
        "bare: org/planted-model\n"
        "prefixed: openai/org/planted-model\n"
        "expr: \"{{ aliases['planted-alias'] }}\"\n"
        "prose_msg: planted-alias must not resolve to org/planted-model\n"
        "# comment: org/planted-model\n"
        "derived: '{{ llm_router_primary_model }}'\n"
    )
    other = tmp_path / "roles/other/defaults"
    other.mkdir(parents=True)
    (other / "main.yml").write_text("model: vendor/physical-name\nfine: cloud-rung\n")
    py = tmp_path / "tests/other"
    py.mkdir(parents=True)
    (py / "test_y.py").write_text(
        '"""Docstring naming org/planted-model is prose."""\n'
        "# a comment naming planted-alias is prose\n"
        "EXPECTED_MODEL = {'x': 'planted-alias'}\n"
        "MESSAGE = \"fallbacks=['org/planted-model']\"\n"
    )
    projection, consumer = scan(tmp_path)
    assert [h.split("[")[1].split("]")[0] for h in projection] == [
        "bare", "expr", "prefixed", "line 3", "line 4",
    ], projection
    assert consumer == ["roles/other/defaults/main.yml [model] spells 'vendor/physical-name'"], consumer


def test_python_dict_key_is_not_a_retype_but_a_value_still_is(tmp_path: Path):
    """A schema field name in key position (e.g. llama_cpp's `embeddings` bool flag) is not scanned;
    the identical string in value position still is. Uses a word that is not itself a registry value, so
    this test's own source doesn't trip test_projection_zone_carries_no_registry_literal."""
    src = tmp_path / "snippet.py"
    src.write_text("FIXTURE = {'schema-field': False}\nOFFENDER = {'model': 'schema-field'}\n")
    literals = dict(_literals(src))
    assert "line 1" not in literals, literals
    assert literals["line 2"] == "schema-field", literals


def test_common_alias_words_are_scanned_only_in_model_fields(tmp_path: Path):
    (tmp_path / "llm-models.d").mkdir()
    aliases = ["de" + "fault", "au" + "to"]
    registry = {"_r": [{"client_model_id": "fixture-model", "upstream_model_id": "fixture-model",
                        "servable": True, "stable_aliases": aliases}]}
    (tmp_path / "llm-models.d" / "10.yml").write_text(yaml.safe_dump(registry))
    defaults = tmp_path / "roles/llm_router/defaults/main"
    defaults.mkdir(parents=True)
    (defaults / "x.yml").write_text(yaml.safe_dump({"profile": aliases[0], "model": aliases[0], "mode": aliases[1]}))
    projection, _ = scan(tmp_path)
    expected = f"roles/llm_router/defaults/main/x.yml [model] spells '{aliases[0]}'"
    assert projection == [expected], projection


def test_inventory_literal_is_the_contract_not_a_retype(tmp_path: Path):
    """A fixture mirroring an inventory-assigned selector is exempt; derive it in inventory and it is not."""
    (tmp_path / "llm-models.d").mkdir()
    (tmp_path / "llm-models.d" / "10.yml").write_text(
        "_r:\n  - {client_model_id: fabric-router, upstream_model_id: complexity_router}\n"
    )
    inv = tmp_path / "inventory/group_vars"
    inv.mkdir(parents=True)
    fixture = tmp_path / "tests/llm_router"
    fixture.mkdir(parents=True)
    (fixture / "test_x.yml").write_text("- hosts: localhost\n  vars:\n    brain: fabric-router\n")

    (inv / "all.yml").write_text("brain: fabric-router\n")
    assert scan(tmp_path) == ([], [])

    (inv / "all.yml").write_text("brain: '{{ llm_router_hermes_virtual_models[0].client_model_id }}'\n")
    projection, _ = scan(tmp_path)
    assert projection == ["tests/llm_router/test_x.yml [0.vars.brain] spells 'fabric-router'"], projection


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
