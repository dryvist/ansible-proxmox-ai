"""Render llama-server.service.j2 and prove the systemd unit actually asks for
router mode, one resident model, the right port, and the mandatory slot count.

WHY THIS EXISTS. llama-swap was retired 2026-09-19 (see roles/llama_cpp/README.md):
llama-server now runs directly as the unit, in its own native router mode
(--models-dir), rather than being fronted by a second process and a second
config format. Nothing here parses this file as YAML — it is a systemd unit
(INI-style), not YAML — so this test does the equivalent thing that
test_concurrency_limit.py did for the retired llama-swap.yaml.j2: render with
Ansible's REAL Jinja settings (trim_blocks=True, lstrip_blocks=False — see
that file's comment for why lstrip_blocks=True would hide a real bug) and
assert on the rendered ExecStart tokens.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llama_cpp/templates/llama-server.service.j2"

DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llama_cpp_gpu_backend": "vulkan",
    "llama_cpp_user": "llama-cpp",
    "llama_cpp_group": "llama-cpp",
    "llama_cpp_data_dir": "/var/lib/llama-cpp",
    "llama_cpp_install_dir": "/opt/llama-cpp",
    "llama_cpp_hsa_override_gfx_version": "10.3.0",
    "llama_cpp_server_bin": "/opt/llama-cpp/llama-server",
    "llama_cpp_api_port": 10434,
    "llama_cpp_models_dir": "/var/lib/llama-cpp/models",
    "llama_cpp_models_max": 1,
    "llama_cpp_parallel": 1,
    "llama_cpp_ngl": 99,
    "llama_cpp_ctx_size": 32768,
    "llama_cpp_cache_reuse": 256,
    "llama_cpp_cache_ram": None,
    "llama_cpp_cache_idle_slots": False,
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(**overrides) -> str:
    context = {**DEFAULT_CONTEXT, **overrides}
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.filters["comment"] = _comment_filter
    env.filters["mandatory"] = lambda v, msg="": v
    env.filters["bool"] = lambda v: bool(v)
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def _exec_start(rendered: str) -> str:
    """ExecStart may continue across lines with a trailing backslash — join
    them into one string before token-matching, the way systemd itself does."""
    match = re.search(r"^ExecStart=(.*?)(?=^\[Install\])", rendered, re.MULTILINE | re.DOTALL)
    assert match, f"no ExecStart found in rendered unit:\n{rendered}"
    return match.group(1).replace("\\\n", " ")


def test_template_exists():
    assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH


def test_exec_start_carries_router_mode_and_the_mandatory_slot_count():
    exec_start = _exec_start(render(llama_cpp_models_dir="/mnt/ro-models", llama_cpp_models_max=1, llama_cpp_parallel=3))
    assert "/opt/llama-cpp/llama-server" in exec_start
    assert "--models-dir /mnt/ro-models" in exec_start
    assert "--models-max 1" in exec_start
    assert "--parallel 3" in exec_start
    assert "--metrics" in exec_start


def test_exec_start_listens_on_the_configured_port():
    exec_start = _exec_start(render(llama_cpp_api_port=10434))
    assert "--host 0.0.0.0 --port 10434" in exec_start


def test_rendering_with_llama_cpp_parallel_unset_fails():
    """Mandatory means mandatory here too — see the identical contract in the
    (now-deleted) llama-swap render test's history: a group that leaves this
    unset must not silently get a working unit."""
    context = {k: v for k, v in DEFAULT_CONTEXT.items() if k != "llama_cpp_parallel"}
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False, undefined=jinja2.StrictUndefined)
    env.filters["comment"] = _comment_filter
    env.filters["mandatory"] = lambda v, msg="": v
    env.filters["bool"] = lambda v: bool(v)
    with pytest.raises(jinja2.exceptions.UndefinedError):
        env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)
