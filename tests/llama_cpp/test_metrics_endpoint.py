"""Render llama-swap.yaml.j2 and prove every model's llama-server gets
`--metrics`, and that the /upstream metrics scrape path can never trigger a
model swap.

WHY THIS EXISTS. `--metrics` turns on llama-server's Prometheus exporter — it
is on the shared `server-base` macro, so every model entry inherits it, but a
future edit to a per-model `cmd` block that bypasses the macro would silently
drop it for just that model. Separately, llama-swap treats any request to an
unloaded model's `/upstream/<model>/...` path as a load request; scraping the
metrics endpoint of a model currently swapped out would load-thrash it every
scrape interval. `upstream.ignorePaths` is what stops that — this test pins
that the pattern is present and that it does not silently drop llama-swap's
own default static-asset patterns.
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
    # lstrip_blocks=False matches Ansible's real Jinja environment exactly
    # (see test_concurrency_limit.py for why True here would hide a real bug).
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.filters["comment"] = _comment_filter
    env.filters["bool"] = lambda v: bool(v)
    env.filters["mandatory"] = lambda v, msg="": v
    context = {**DEFAULT_CONTEXT, **overrides}
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def test_template_exists():
    assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH


def test_renders_to_valid_yaml():
    parsed = yaml.safe_load(render())
    assert parsed["models"], "expected at least one rendered model"


def test_every_model_cmd_enables_the_metrics_endpoint():
    """Every model's cmd references the shared server-base macro (never
    inlines llama-server flags directly), and that macro carries --metrics —
    so asserting on the macro covers every model at once, present or future."""
    parsed = yaml.safe_load(render())
    assert "--metrics" in parsed["macros"]["server-base"]
    for name, model in parsed["models"].items():
        assert "${server-base}" in model["cmd"], f"{name!r} cmd bypasses the shared server-base macro"


def test_upstream_metrics_scrapes_are_excluded_from_swap_triggering():
    parsed = yaml.safe_load(render())
    ignore_paths = parsed["upstream"]["ignorePaths"]
    assert any(p.endswith("/metrics$") for p in ignore_paths), (
        "no ignorePaths pattern excludes a /metrics scrape from triggering a swap"
    )


def test_ignore_paths_still_covers_the_stock_static_asset_patterns():
    """ignorePaths is a full override, not a merge — losing the default
    pattern here would make every model's own static assets start
    triggering a swap on every request, which is the exact churn this key
    exists to prevent."""
    parsed = yaml.safe_load(render())
    ignore_paths = parsed["upstream"]["ignorePaths"]
    assert any("js" in p and "css" in p for p in ignore_paths), (
        "the default static-asset ignorePaths pattern was dropped, not extended"
    )
