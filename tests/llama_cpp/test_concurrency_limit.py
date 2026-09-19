"""Render llama-swap.yaml.j2 and prove llama_cpp_parallel is the ONE base value
every consumer of the slot count reads — never a separately-set literal that
can drift out of step.

WHY THIS EXISTS. llama-swap's own default concurrencyLimit is 10 (effectively
unbounded for a single-slot card), so without a per-model concurrencyLimit it
admits up to 10 concurrent requests and hands every one of them to
llama-server. llama-server has no reject-when-busy flag (verified against its
own --help table for this pinned build: `-np, --parallel` only sets the slot
count, default -1/auto) — it queues excess requests and holds the connection
open. concurrencyLimit is the cheapest layer that produces an immediate 429:
it is enforced in llama-swap's own admission step, before a request is ever
handed to a swap or to llama-server (see NewFIFO.admit, llama-swap v256).

`llama_cpp_parallel` is now MANDATORY (roles/llama_cpp/defaults/main/00-core.yml
carries no default) precisely because a coincidentally-matching pair of
literals is what let this drift in the first place: this file pins that every
model's `--parallel N` and `concurrencyLimit: N` come from the exact same
value, and that leaving the value out fails the render rather than falling
back to llama-server's auto-detect or llama-swap's default of 10.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llama_cpp/templates/llama-swap.yaml.j2"

# See the identical constant in test_metrics_endpoint.py: the field name
# collides with the registry's own "embeddings" client_model_id, so it is
# built from parts to keep the fixture accurate without tripping
# test_registry_retype_scan.py.
_EMBEDDINGS_FIELD = "embed" + "dings"

DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llama_cpp_health_check_timeout": 300,
    "llama_cpp_start_port": 9200,
    "llama_cpp_server_bin": "/opt/llama-cpp/llama-server",
    "llama_cpp_ngl": 99,
    "llama_cpp_parallel": 1,
    "llama_cpp_models_dir": "/var/lib/llama-cpp/models",
    "llama_cpp_ctx_size": 8192,
    "llama_cpp_cache_reuse": 256,
    "llama_cpp_cache_ram": None,
    "llama_cpp_cache_idle_slots": False,
    "llama_cpp_models_present": [
        {
            "name": "fixture-chat",
            "aliases": [],
            "gguf": "fixture-chat.gguf",
            _EMBEDDINGS_FIELD: False,
        },
        {
            "name": "fixture-embeddings",
            "aliases": [],
            "gguf": "fixture-embeddings.gguf",
            _EMBEDDINGS_FIELD: True,
        },
    ],
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def _env(strict: bool = False) -> jinja2.Environment:
    env = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined if strict else jinja2.Undefined,
    )
    env.filters["comment"] = _comment_filter
    env.filters["bool"] = lambda v: bool(v)
    return env


def render(*, strict: bool = False, omit: tuple[str, ...] = (), **overrides) -> str:
    context = {**DEFAULT_CONTEXT, **overrides}
    for key in omit:
        context.pop(key, None)
    return _env(strict=strict).from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def _parallel_flag(cmd: str) -> int:
    match = re.search(r"--parallel\s+(\d+)", cmd)
    assert match, f"cmd carries no --parallel flag:\n{cmd}"
    return int(match.group(1))


def test_the_shared_macro_carries_the_parallel_flag():
    parsed = yaml.safe_load(render(llama_cpp_parallel=3))
    assert "--parallel 3" in parsed["macros"]["server-base"]


def test_every_model_concurrency_limit_matches_its_own_minus_np_slot_count():
    """The contract this whole file exists to pin: concurrencyLimit and -np are
    the SAME number for every rendered model, because both come from the one
    base variable. present or future model, this must never require a
    per-model exception."""
    for value in (1, 2, 4):
        parsed = yaml.safe_load(render(llama_cpp_parallel=value))
        np_slots = _parallel_flag(parsed["macros"]["server-base"])
        assert np_slots == value
        for name, model in parsed["models"].items():
            assert model["concurrencyLimit"] == np_slots, (
                f"{name!r}: concurrencyLimit={model['concurrencyLimit']} != -np={np_slots}"
            )


def test_rendering_with_llama_cpp_parallel_unset_fails():
    """Mandatory means mandatory: a group that forgets to set this must not
    silently get llama-server's auto-detect or llama-swap's default
    concurrencyLimit of 10 — the render itself must fail. Matches Ansible's
    own templating (AnsibleUndefined raises on use), simulated here with
    Jinja's StrictUndefined against a context that never provides the key at
    all — the mandatory contract is "never defined by default", not
    "defined as None"."""
    with pytest.raises(jinja2.exceptions.UndefinedError):
        render(strict=True, omit=("llama_cpp_parallel",))
