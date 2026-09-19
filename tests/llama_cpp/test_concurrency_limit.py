"""Render llama-swap.yaml.j2 and prove a card with a fixed slot count rejects
the (slots+1)th request instead of queueing it.

WHY THIS EXISTS. llama-swap's own default concurrencyLimit is 10 (unlimited
in practice for a single-slot card), so without a per-model concurrencyLimit
it admits up to 10 concurrent requests and hands every one of them to
llama-server. llama-server has no reject-when-busy flag (verified against its
own --help table for this pinned build: `-np, --parallel` only sets the slot
count, default -1/auto) — it queues excess requests and holds the connection
open. Measured 2026-09-19 (ansible-splunk run 35466855232): a request to the
review-public role's local rung sat ~6 minutes behind the card's one busy slot
before the router's own Gateway Timeout fired — a queue, not a reject.
concurrencyLimit is the cheapest layer that produces an immediate 429: it is
enforced in llama-swap's own admission step, before a request is ever handed
to a swap or to llama-server (see NewFIFO.admit, llama-swap v256).
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llama_cpp/templates/llama-swap.yaml.j2"

DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llama_cpp_health_check_timeout": 300,
    "llama_cpp_start_port": 9200,
    "llama_cpp_server_bin": "/opt/llama-cpp/llama-server",
    "llama_cpp_ngl": 99,
    "llama_cpp_parallel": None,
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
            "embeddings": False,
        },
        {
            "name": "fixture-embeddings",
            "aliases": [],
            "gguf": "fixture-embeddings.gguf",
            "embeddings": True,
        },
    ],
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(**overrides) -> str:
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True)
    env.filters["comment"] = _comment_filter
    env.filters["bool"] = lambda v: bool(v)
    context = {**DEFAULT_CONTEXT, **overrides}
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def test_every_model_gets_the_group_slot_count_as_its_concurrency_limit():
    """A group that declares a fixed slot count (e.g. the 4080's
    llama_cpp_parallel: 1) must reject past that count, not queue — the fix
    applies to every model in that group's config at once, present or future."""
    parsed = yaml.safe_load(render(llama_cpp_parallel=1))
    for name, model in parsed["models"].items():
        assert model["concurrencyLimit"] == 1, f"{name!r} did not get concurrencyLimit: 1"


def test_concurrency_limit_tracks_a_non_default_slot_count():
    parsed = yaml.safe_load(render(llama_cpp_parallel=2))
    for name, model in parsed["models"].items():
        assert model["concurrencyLimit"] == 2, f"{name!r} did not get concurrencyLimit: 2"


def test_concurrency_limit_is_absent_when_the_group_never_pinned_a_slot_count():
    """Groups that leave llama_cpp_parallel unset (llama-server auto-picks slots)
    get no concurrencyLimit override, so llama-swap's own default (10) applies —
    unchanged behaviour for hosts that never opted into a fixed slot count."""
    parsed = yaml.safe_load(render())
    for name, model in parsed["models"].items():
        assert "concurrencyLimit" not in model, f"{name!r} unexpectedly got a concurrencyLimit"
