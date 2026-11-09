"""Per-agent rotation trigger URLs dispatch to the compose-internal nginx base."""

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment

from _defaults import load_defaults
from test_rotation_wiring import AGENT, URL_ADD, WIRING, _flat, _task, _yaml

ROOT = Path(__file__).resolve().parents[2]


def test_agent_trigger_urls_render_to_the_internal_compose_base():
    defaults = load_defaults()
    internal = defaults["dify_docker_internal_trigger_url"]
    parts = urlsplit(internal)
    # Plain HTTP to the stack's own nginx service on the Compose network: the
    # HTTP request node reaches it through the ssrf_proxy, whose squid config
    # allows exactly this origin for the webhook path.
    assert parts.scheme == "http"
    services_block = (
        (ROOT / "roles/dify_docker/templates/docker-compose.yml.j2")
        .read_text(encoding="utf-8")
        .split("\nnetworks:", 1)[0]
    )
    assert parts.hostname in re.findall(r"^  ([a-zA-Z0-9_-]+):$", services_block, re.MULTILINE)
    # The public base stays the TRIGGER_URL default for callers outside the stack.
    assert defaults["dify_docker_trigger_url"] == "{{ dify_docker_public_url }}"
    # The wiring never consults the public variable: the per-agent dispatch is
    # compose-internal only.
    for path in (WIRING, AGENT):
        assert "dify_docker_trigger_url" not in path.read_text(encoding="utf-8")

    # Render the patch entry the way Ansible would: every per-agent
    # <AGENT>_TRIGGER_URL value becomes the slash-stripped internal base.
    env = Environment()
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, str(value)
    )
    env.filters["combine"] = lambda value, other: {**value, **other}
    expression = _flat(
        _task(_yaml(AGENT), URL_ADD)["ansible.builtin.set_fact"][
            "dify_docker_rotation_patch_variables"
        ]
    )
    stored = {
        "id": "var-1",
        "name": "AI_NEWS_TRIGGER_URL",
        "value_type": "string",
        "value": "https://outdated.example.test",
    }
    patched = ast.literal_eval(
        env.from_string(expression).render(
            dify_docker_rotation_agent_trigger_url_var=[stored],
            dify_docker_internal_trigger_url=internal + "/",
        )
    )
    assert patched[0]["name"] == "AI_NEWS_TRIGGER_URL"
    assert patched[0]["value"] == internal

    detection = _flat(
        _task(_yaml(AGENT), "Detect whether the agent draft variables need the wiring")[
            "ansible.builtin.set_fact"
        ]["dify_docker_rotation_agent_trigger_url_matches"]
    )
    assert env.from_string(detection).render(
        dify_docker_rotation_agent_trigger_url_var=[{"value": internal}],
        dify_docker_internal_trigger_url=internal,
    ) == "True"
    assert env.from_string(detection).render(
        dify_docker_rotation_agent_trigger_url_var=[{"value": internal + "/"}],
        dify_docker_internal_trigger_url=internal,
    ) == "False"
