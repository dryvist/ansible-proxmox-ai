"""Preserve prompt bytes and render every cron through real Ansible lookups."""

import hashlib
import json
from pathlib import Path
import re

import pytest
import yaml

from _prompt_catalog import catalog_files, prompt_bodies
from _prompt_catalog_render import ROLE, raw_defaults, render_hashes
from _role_files import role_defaults

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = {
    "hermes_agent_summary_cron_prompt",
    "hermes_agent_voice_learn_cron_prompt",
    "hermes_agent_app_seeding_endpoints",
    "hermes_agent_daily_status_endpoints",
    "hermes_agent_service_pulse_endpoints",
}


def test_prompt_sources_preserve_the_original_literal_bytes():
    defaults = raw_defaults()
    expected = json.loads((FIXTURES / "catalog-prompt-sha256.json").read_text())
    bodies = prompt_bodies(defaults)
    assert bodies.keys() == expected.keys()
    for key, body in bodies.items():
        assert hashlib.sha256(body.encode()).hexdigest() == expected[key], key
    assert re.fullmatch(
        r"github:dryvist/ai-llm-prompts/[0-9a-f]{40}#auto-ai-agent",
        defaults["hermes_agent_prompts_flake_ref"],
    )


def test_defaults_hold_prompt_references_with_only_the_five_private_exceptions():
    found = set()

    def check(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                check(child, path + [key])
        elif isinstance(value, list):
            for child in value:
                check(child, path)
        elif isinstance(value, str):
            key = path[-1]
            prompt_key = key == "prompt" or key.endswith((
                "_prompt", "_exclusion_clause", "_ai_news_interests", "_kanban_summary"))
            if prompt_key or key in PRIVATE:
                if key in PRIVATE:
                    found.add(key)
                else:
                    assert re.fullmatch(r"\{\{.*\}\}", value, re.DOTALL), path

    for file in (ROLE / "defaults/main").glob("*.yml"):
        check(yaml.safe_load(file.read_text()) or {}, [file.name])
    assert found == PRIVATE


@pytest.mark.parametrize("identity", ["fixture", "alternate"])
@pytest.mark.parametrize("tags", [None, "hermes_profiles_env", "hermes_units_gateways"])
def test_every_rendered_cron_body_matches_the_before_migration_hashes(tmp_path, identity, tags):
    defaults = raw_defaults()
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    for name, text in catalog_files(defaults["hermes_agent_prompts_flake_ref"]).items():
        (catalog / name).write_text(text)
    prompts = json.loads((FIXTURES / "catalog-render-prompts.json").read_text())
    crons = json.loads((FIXTURES / "catalog-render-crons.json").read_text())
    expected = {
        "prompts": {**json.loads((FIXTURES / "catalog-prompt-sha256.json").read_text()),
                    **prompts["fixture"], **prompts[identity]},
        "crons": list(crons["fixture"]),
    }
    if identity != "fixture":
        for index, value in crons[identity].items():
            expected["crons"][int(index)] = value
    actual = render_hashes(tmp_path, catalog, identity=identity, include_parts=True, tags=tags)
    jobs = [job for job in role_defaults(ROLE)["hermes_agent_direct_cron_jobs"]
            if any(key in job for key in ("prompt_var", "prompt_file", "prompt_files"))]
    stats = next(index for index, job in enumerate(jobs)
                 if job.get("prompt_var") == "hermes_agent_llm_stats_hourly_cron_prompt")
    assert actual["crons"].pop() == expected["crons"][stats]
    assert actual == expected
