"""The data-exfiltration guardrail is declared in config with one fixed shape.

Renders roles/llm_router/templates/guardrails.yaml.j2 from the defaults file and
checks the parsed result. Also checks that no skip option, and no other option
this role must never set, appears anywhere in it. The litellm package is not
importable in this test environment, so the content filter's error handling is
not exercised here.
"""

import json
import re
from pathlib import Path

import jinja2
import yaml

ROLE_DIR = Path(__file__).resolve().parents[2] / "roles/llm_router"
DEFAULTS = ROLE_DIR / "defaults/main/39-guardrails.yml"
TEMPLATES = ROLE_DIR / "templates"
GUARDRAIL_TEMPLATE = "guardrails.yaml.j2"

GUARDRAIL_NAME = "Prompt Injection: Data Exfiltration"
# Every skip and per-message scope option the litellm guardrail layer reads.
FORBIDDEN_OPTIONS = (
    "skip_system_message_in_guardrail",
    "skip_tool_message_in_guardrail",
    "scan_only_tool_results",
    "only_scan_new_messages",
)
ERROR_HANDLING_KEYS = ("fail_on_error", "unreachable_fallback", "on_flagged", "sanitize_error_detail")
EXPECTED_PARAMS = {
    "guardrail": "litellm_content_filter",
    "mode": "pre_call",
    "default_on": True,
    "categories": [
        {
            "category": "prompt_injection_data_exfiltration",
            "enabled": True,
            "action": "BLOCK",
            "severity_threshold": "medium",
        }
    ],
}


def render_guardrails() -> dict:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined)
    env.filters["to_json"] = json.dumps
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    return yaml.safe_load(env.get_template(GUARDRAIL_TEMPLATE).render(**defaults))


def role_text_files() -> list[Path]:
    files = []
    for path in sorted(ROLE_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def test_renders_exactly_one_guardrail_with_the_declared_params():
    rendered = render_guardrails()

    assert list(rendered) == ["guardrails"]
    assert len(rendered["guardrails"]) == 1
    entry = rendered["guardrails"][0]
    assert entry["guardrail_name"] == GUARDRAIL_NAME
    assert entry["litellm_params"] == EXPECTED_PARAMS


def test_error_handling_keys_are_not_rendered():
    params = render_guardrails()["guardrails"][0]["litellm_params"]

    for key in ERROR_HANDLING_KEYS:
        assert key not in params, key


def test_forbidden_options_appear_nowhere_in_the_role():
    hits = [
        f"{path.relative_to(ROLE_DIR)}: {option}"
        for path in role_text_files()
        for text in [path.read_text(encoding="utf-8")]
        for option in FORBIDDEN_OPTIONS
        if option in text
    ]

    assert not hits, "\n".join(hits)


def test_no_other_template_attaches_guardrails_to_keys_or_config():
    guardrail_key = re.compile(r"^\s*guardrails\s*:", re.MULTILINE)
    hits = [
        str(path.relative_to(ROLE_DIR))
        for path in sorted(TEMPLATES.rglob("*.j2"))
        if path.name != GUARDRAIL_TEMPLATE
        and guardrail_key.search(path.read_text(encoding="utf-8"))
    ]

    assert not hits, "\n".join(hits)


def test_config_template_includes_the_guardrail_block_once():
    text = (TEMPLATES / "config.yaml.j2").read_text(encoding="utf-8")

    assert text.count("{% include 'guardrails.yaml.j2' %}") == 1
