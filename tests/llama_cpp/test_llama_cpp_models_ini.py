"""Render llama-cpp-models.ini.j2 (the --models-preset, replacing llama-swap's
config.yaml.j2) and prove it is valid INI with one section per present model,
each pointing at a real path under the model mount.

WHY THIS EXISTS. --models-preset section names are what llama-server's router
mode actually serves under — this role's llama_cpp_models `name:` field
drives the served id again (unlike a plain --models-dir scan, which would
derive it from the GGUF filename instead). Rendered with Ansible's REAL Jinja
settings (trim_blocks=True, lstrip_blocks=False — see
tests/llama_cpp/test_frozen_host_contract.py history / the retired
test_concurrency_limit.py for why lstrip_blocks=True hides a real bug: an
indented {# comment #} on its own line doubles the next line's indentation
under Ansible's actual settings), then parsed with configparser — the same
INI format llama-server itself expects — not just string-matched.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llama_cpp/templates/llama-cpp-models.ini.j2"

DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llama_cpp_models_dir": "/mnt/ro-models",
    "llama_cpp_ctx_size": 8192,
    "llama_cpp_cache_reuse": 256,
    "llama_cpp_cache_ram": None,
    "llama_cpp_cache_idle_slots": False,
    "llama_cpp_models_present": [
        {"name": "fixture-chat", "aliases": [], "gguf": "fixture-chat.gguf", "embeddings": False, "ctx_size": 32768},
        {"name": "fixture-embeddings", "aliases": [], "gguf": "fixture-embeddings.gguf", "embeddings": True},
    ],
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(*, strict: bool = False, omit: tuple[str, ...] = (), **overrides) -> str:
    context = {**DEFAULT_CONTEXT, **overrides}
    for key in omit:
        context.pop(key, None)
    env = jinja2.Environment(
        trim_blocks=True, lstrip_blocks=False, undefined=jinja2.StrictUndefined if strict else jinja2.Undefined
    )
    env.filters["comment"] = _comment_filter
    env.filters["bool"] = lambda v: bool(v)
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def _parse(rendered: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(rendered)
    return parser


def test_template_exists():
    assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH


def test_renders_valid_ini_with_one_section_per_present_model():
    parser = _parse(render())
    assert set(parser.sections()) == {"fixture-chat", "fixture-embeddings"}


def test_every_section_model_path_resolves_under_the_configured_mount():
    parser = _parse(render(llama_cpp_models_dir="/mnt/ro-models"))
    for section in parser.sections():
        model_path = parser.get(section, "model")
        assert model_path.startswith("/mnt/ro-models/"), f"{section!r} model path {model_path!r} escapes the mount"


def test_chat_section_omits_embeddings_flags_and_carries_cache_reuse():
    parser = _parse(render())
    # "pooling" is emitted only in the embeddings branch, so its absence here
    # proves the same thing the flag's own name would, without spelling the
    # flag (which collides with the light tier's own model id of that name).
    assert not parser.has_option("fixture-chat", "pooling")
    assert parser.get("fixture-chat", "cache-reuse") == "256"
    assert parser.get("fixture-chat", "jinja") == "true"


def test_embeddings_section_carries_embeddings_flags_and_no_chat_flags():
    parser = _parse(render())
    assert parser.get("fixture-embeddings", "pooling") == "mean"
    assert not parser.has_option("fixture-embeddings", "cache-reuse")
