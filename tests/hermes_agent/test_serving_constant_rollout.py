"""ai_llm_concurrency must resolve whether or not the published artifact
carries constants.serving.

Ansible reads the PUBLISHED inventory artifact, not tofu-proxmox's
constants.tf. The artifact is only rewritten by an apply, so a constant added
to constants.tf is absent from the artifact until then. On 2026-08-02 a bare
`tofu_data.constants.serving.llm_concurrency` reference shipped while the
artifact still had no `serving` key, and every converge of the hermes_agent
role failed with:

    object of type 'dict' has no attribute 'serving'

These tests pin both sides of that rollout window so the shim can be removed
deliberately rather than rediscovered by another outage.
"""

from pathlib import Path
from typing import Any

import jinja2
import yaml

from _group_vars import load_group_vars_all

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_YML = REPO_ROOT / "inventory" / "group_vars" / "all" / "00-core.yml"

# The artifact's constants object as it actually was during the outage: many
# port families, no `serving`.
CONSTANTS_WITHOUT_SERVING: dict[str, Any] = {
    "service_ports": {"ollama_api": 11434},
    "ingress_ports": {},
    "vector_db_ports": {},
}


def _group_vars() -> dict[str, Any]:
    return load_group_vars_all()


def _render(expr: str, **ctx: Any) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    return env.from_string(str(expr)).render(**ctx)


def _resolve(constants: dict[str, Any]) -> tuple[int, bool]:
    """Render both vars the way Ansible's templar would, given an artifact."""
    gv = _group_vars()
    ctx = {"tofu_data": {"constants": constants}}
    value = int(_render(gv["ai_llm_concurrency"], **ctx))
    from_fallback = _render(gv["ai_llm_concurrency_from_fallback"], **ctx).strip().lower() == "true"
    return value, from_fallback


def test_resolves_when_artifact_predates_the_constant() -> None:
    """The exact outage condition: no `serving` key at all."""
    value, from_fallback = _resolve(CONSTANTS_WITHOUT_SERVING)
    assert value == 16, "fallback must yield the active profile's published value, not blow up"
    assert from_fallback is True, "an artifact without serving must report the fallback path"


def test_prefers_the_published_constant_when_present() -> None:
    """Once an apply republishes the artifact, the constant wins outright."""
    value, from_fallback = _resolve({"serving": {"llm_concurrency": 16}})
    assert value == 16, "the published constant must win over the fallback default"
    assert from_fallback is False, "with serving present nothing may report the fallback"


def test_fallback_matches_the_published_default() -> None:
    """The shim must not be a second, divergent definition.

    Its only legitimate value is the one the constant currently publishes; if
    they ever disagree the shim has become a real duplicate definition, which
    is the DRY violation this whole change set removed.
    """
    fallback_value, _ = _resolve(CONSTANTS_WITHOUT_SERVING)
    published_value, _ = _resolve({"serving": {"llm_concurrency": 16}})
    assert fallback_value == published_value


def test_medium_lane_slots_match_published_concurrency_and_other_profiles_declare_slots() -> None:
    """The published serving constant equals the medium lane's active slots.

    The constant feeds the shared medium-lane consumers, so only the guest whose
    host_vars declare llm_lane: medium is held to it. Every other selectable
    active profile keeps its own slot count and must declare it.
    """
    all_vars = _group_vars()
    profiles = {}
    vllm_dir = REPO_ROOT / "roles/vllm_serving/defaults/main"
    profiles.update(
        {
            **yaml.safe_load((vllm_dir / "10-profiles.yml").read_text())["vllm_serving_profiles_core"],
            **yaml.safe_load((vllm_dir / "11-profile-families.yml").read_text())[
                "vllm_serving_profiles_specialized"
            ],
        }
    )
    for path, key in (
        (REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml", "llamacpp_serving_profiles"),
        (
            REPO_ROOT / "roles/sglang_serving/defaults/main/10-profiles.yml",
            "sglang_serving_profiles",
        ),
    ):
        profiles.update(yaml.safe_load(path.read_text())[key])

    medium_lane_profiles = {
        host_vars["llm_active_profile"]
        for host_vars in (
            yaml.safe_load(path.read_text()) or {}
            for path in sorted((REPO_ROOT / "inventory/host_vars").glob("*.yml"))
        )
        if host_vars.get("llm_lane") == "medium"
    }
    assert medium_lane_profiles, "no inventory host declares llm_lane: medium"
    published, _ = _resolve({"serving": {"llm_concurrency": 16}})
    assert {profiles[name]["max_num_seqs"] for name in medium_lane_profiles} == {published}

    selected = set(all_vars["llm_gpu_active_profiles_by_engine"].values()) | {"medium-a"}
    for name in sorted(selected - medium_lane_profiles):
        slots = profiles[name]["max_num_seqs"]
        assert isinstance(slots, int) and not isinstance(slots, bool) and slots > 0, (
            f"{name} must declare its own max_num_seqs as a positive int"
        )


def test_shim_is_documented_as_temporary() -> None:
    """A migration shim with no removal instruction becomes permanent."""
    text = ALL_YML.read_text()
    assert "ROLLOUT WINDOW" in text, "the shim must be labelled as a rollout window"
    assert "ai_llm_concurrency_from_fallback" in text, (
        "the shim must ship the flag that makes its use visible at converge time"
    )
