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

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_YML = REPO_ROOT / "inventory" / "group_vars" / "all.yml"

# The artifact's constants object as it actually was during the outage: many
# port families, no `serving`.
CONSTANTS_WITHOUT_SERVING: dict[str, Any] = {
    "service_ports": {"ollama_api": 11434},
    "ingress_ports": {},
    "vector_db_ports": {},
}


def _group_vars() -> dict[str, Any]:
    return yaml.safe_load(ALL_YML.read_text())


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
    assert value == 1, "fallback must yield the published value, not blow up"
    assert from_fallback is True, "an artifact without serving must report the fallback path"


def test_prefers_the_published_constant_when_present() -> None:
    """Once an apply republishes the artifact, the constant wins outright."""
    value, from_fallback = _resolve({"serving": {"llm_concurrency": 2}})
    assert value == 2, "the published constant must win over the fallback default"
    assert from_fallback is False, "with serving present nothing may report the fallback"


def test_fallback_matches_the_published_default() -> None:
    """The shim must not be a second, divergent definition.

    Its only legitimate value is the one the constant currently publishes; if
    they ever disagree the shim has become a real duplicate definition, which
    is the DRY violation this whole change set removed.
    """
    fallback_value, _ = _resolve(CONSTANTS_WITHOUT_SERVING)
    published_value, _ = _resolve({"serving": {"llm_concurrency": 1}})
    assert fallback_value == published_value


def test_shim_is_documented_as_temporary() -> None:
    """A migration shim with no removal instruction becomes permanent."""
    text = ALL_YML.read_text()
    assert "ROLLOUT WINDOW" in text, "the shim must be labelled as a rollout window"
    assert "ai_llm_concurrency_from_fallback" in text, (
        "the shim must ship the flag that makes its use visible at converge time"
    )
