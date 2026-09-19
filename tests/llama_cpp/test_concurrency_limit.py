"""Render llama-swap.yaml.j2 and prove llama_cpp_parallel is the one base
value both -np and concurrencyLimit read, and that it is mandatory.

WHY THIS EXISTS. llama-swap's own default concurrencyLimit is 10, so without
a per-model concurrencyLimit it admits up to 10 concurrent requests and hands
them to llama-server, which QUEUES anything past its actual slot count
instead of rejecting it. Pinning concurrencyLimit to the same llama_cpp_parallel
value passed to --parallel makes llama-swap reject the (slots+1)th request
immediately (HTTP 429) instead of queueing it — and `| mandatory(...)` in the
template means a group that forgets to set llama_cpp_parallel fails the
render loud, rather than falling back to llama-server's auto-detect.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llama_cpp/templates/llama-swap.yaml.j2"

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
        {"name": "fixture-chat", "aliases": [], "gguf": "fixture-chat.gguf", "embeddings": False},
    ],
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(*, strict: bool = False, omit: tuple[str, ...] = (), **overrides) -> str:
    context = {**DEFAULT_CONTEXT, **overrides}
    for key in omit:
        context.pop(key, None)
    env = jinja2.Environment(
        trim_blocks=True, lstrip_blocks=True, undefined=jinja2.StrictUndefined if strict else jinja2.Undefined
    )
    env.filters["comment"] = _comment_filter
    env.filters["bool"] = lambda v: bool(v)
    env.filters["mandatory"] = lambda v, msg="": v
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def test_concurrency_limit_matches_its_own_minus_np_slot_count():
    for value in (1, 2, 4):
        parsed = yaml.safe_load(render(llama_cpp_parallel=value))
        np_slots = int(re.search(r"--parallel\s+(\d+)", parsed["macros"]["server-base"]).group(1))
        assert np_slots == value
        for name, model in parsed["models"].items():
            assert model["concurrencyLimit"] == np_slots, f"{name!r}: concurrencyLimit != -np"


def test_rendering_with_llama_cpp_parallel_unset_fails():
    with pytest.raises(jinja2.exceptions.UndefinedError):
        render(strict=True, omit=("llama_cpp_parallel",))
