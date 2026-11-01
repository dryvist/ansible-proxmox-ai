"""The sim_docker role keeps its safety properties: the vendor compose file is pinned by commit and
checksum, the override publishes no database port and persists uploads and data, images are
digest-pinned, .env turns off hosted Chat and telemetry, the seed never names a writer policy, and the
isolated playbook is not imported by site.yml."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
import yaml
from jinja2 import meta
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/sim_docker"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
TASKS_TEXT = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
TASKS = yaml.safe_load(TASKS_TEXT)
OVERRIDE_TEMPLATE = (ROLE / "templates/docker-compose.override.yml.j2").read_text(encoding="utf-8")
ENV_TEMPLATE = (ROLE / "templates/env.j2").read_text(encoding="utf-8")
SEED_TASK = "Seed the Sim secrets through the shared seed_generated task"
SEED_FIELDS = {
    "POSTGRES_PASSWORD",
    "BETTER_AUTH_SECRET",
    "ENCRYPTION_KEY",
    "INTERNAL_API_SECRET",
    "API_ENCRYPTION_KEY",
    "CRON_SECRET",
}
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
DIGEST_REF = re.compile(r"^[a-z0-9./-]+:[$A-Za-z0-9._{}-]+@sha256:[0-9a-f]{64}$")
PINNED_COMMIT = "df3db64ed17a5eb73fef8ccc95da180b0a1cf4b5"
PINNED_SHA256 = "0d679883a0e80ecd656abca9fc44e012b572e0f4ec945476216f9ec9a151a9ce"

# Values the role gets from the secret store or the controller environment at run time.
RUNTIME_VALUES = {
    "sim_docker_db_password": "fixture-db-password",
    "sim_docker_better_auth_secret": "fixture-better-auth-secret",
    "sim_docker_encryption_key": "fixture-encryption-key",
    "sim_docker_internal_api_secret": "fixture-internal-api-secret",
    "sim_docker_api_encryption_key": "fixture-api-encryption-key",
    "sim_docker_cron_secret": "fixture-cron-secret",
    "sim_docker_llm_api_key": "",
}


def _context(**overrides: str) -> dict:
    """Role defaults with runtime values filled in and Jinja references resolved.

    Lookup-backed defaults are skipped because they read the controller environment, which the test
    must not depend on.
    """
    context = {key: value for key, value in DEFAULTS.items() if "lookup(" not in str(value)}
    context.update(RUNTIME_VALUES)
    context.update(overrides)
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    for _ in range(4):
        for key, value in list(context.items()):
            if key not in RUNTIME_VALUES and isinstance(value, str) and "{{" in value:
                context[key] = env.from_string(value).render(**context)
    return context


def _render(source: str, **overrides: str) -> str:
    return jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False).from_string(source).render(
        **_context(**overrides)
    )


def _override(**overrides: str) -> dict:
    return yaml.safe_load(_render(OVERRIDE_TEMPLATE, **overrides))


def _env(**overrides: str) -> dict:
    values = {}
    for line in _render(ENV_TEMPLATE, **overrides).splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    return values


def _seed_task() -> dict:
    return next(task for task in TASKS if task.get("name") == SEED_TASK)


def _task(name: str) -> dict:
    return next(task for task in TASKS if task.get("name") == name)


def test_vendor_compose_is_pinned_by_commit_and_checksum() -> None:
    assert DEFAULTS["sim_docker_release_commit"] == PINNED_COMMIT
    assert DEFAULTS["sim_docker_compose_sha256"] == PINNED_SHA256
    assert SHA256_HEX.match(DEFAULTS["sim_docker_compose_sha256"])
    assert _context()["sim_docker_compose_url"].endswith(f"/{PINNED_COMMIT}/docker-compose.prod.yml")

    fetch = _task("Fetch Sim's vendor production compose file at the pinned release commit")
    assert fetch["ansible.builtin.get_url"]["checksum"] == "sha256:{{ sim_docker_compose_sha256 }}"
    assert fetch["ansible.builtin.get_url"]["url"] == "{{ sim_docker_compose_url }}"
    # The vendor file is fetched unchanged, so the role carries no derived copy of it.
    assert not (ROLE / "templates/docker-compose.yml.j2").exists()


def test_openbao_path_defaults_to_the_specialist_eval_namespace() -> None:
    assert DEFAULTS["sim_docker_openbao_path"] == "ai/eval/specialist-platform/sim"
    assert _seed_task()["block"][0]["vars"]["openbao_secrets_seed_path"] == "{{ sim_docker_openbao_path }}"


def test_override_publishes_no_database_port() -> None:
    services = _override()["services"]

    assert "ports" not in services["db"]
    assert all("ports" not in service for service in services.values())
    assert "5432" not in OVERRIDE_TEMPLATE


def test_override_mounts_uploads_and_bind_mounts_the_database() -> None:
    context = _context()
    services = _override()["services"]

    assert f"{context['sim_docker_uploads_data_dir']}:/app/uploads" in services["simstudio"]["volumes"]
    assert f"{context['sim_docker_postgres_data_dir']}:/var/lib/postgresql/data" in services["db"]["volumes"]
    upload_task = _task("Create the Sim uploads directory owned by the app user")
    assert upload_task["ansible.builtin.file"]["owner"] == "{{ sim_docker_app_uid }}"


def test_every_image_is_pinned_by_digest_and_sim_images_follow_sim_version() -> None:
    services = _override()["services"]

    for name, service in services.items():
        assert DIGEST_REF.match(service["image"]), name
    for name in ("simstudio", "realtime", "migrations"):
        assert "${SIM_VERSION}@sha256:" in services[name]["image"], name
    context = _context()
    for key in ("sim_docker_app_digest", "sim_docker_realtime_digest", "sim_docker_migrations_digest",
                "sim_docker_cron_digest"):
        assert SHA256_HEX.match(context[key].removeprefix("sha256:")), key


def test_env_pins_sim_version_and_cron_and_turns_off_hosted_features() -> None:
    env = _env()

    assert env["SIM_VERSION"] == "v0.9.32"
    assert env["SIM_CRON_VERSION"].startswith("v0.9.32@sha256:")
    assert env["NEXT_PUBLIC_CHAT_DISABLED"] == "true"
    assert env["NEXT_TELEMETRY_DISABLED"] == "1"
    for name in (
        "COPILOT_API_KEY",
        "SIM_AGENT_API_URL",
        "MSHIP_SYSPROMPT_OVERRIDE",
        "BILLING_ENABLED",
        "OLLAMA_URL",
        "NEXT_PUBLIC_SOCKET_URL",
        "VLLM_BASE_URL",
    ):
        assert name not in env, name


def test_llm_endpoint_renders_only_when_configured() -> None:
    assert "VLLM_BASE_URL" not in _env()

    env = _env(sim_docker_llm_base_url="http://192.0.2.10:8000/v1")

    assert env["VLLM_BASE_URL"] == "http://192.0.2.10:8000/v1"
    assert "VLLM_API_KEY" in env


def test_seed_binds_the_converge_writer_policy() -> None:
    assert _seed_task()["block"][0]["vars"]["openbao_secrets_seed_expected_writer_policy"] == "ansible-converge"


def test_seed_covers_every_secret_the_env_file_writes() -> None:
    fields = _seed_task()["block"][0]["vars"]["openbao_secrets_seed_fields"]
    env_names = set(_env())

    assert {field["name"] for field in fields} == SEED_FIELDS
    assert SEED_FIELDS <= env_names


@pytest.mark.parametrize(
    ("postgres_initialized", "generation_allowed"),
    [
        pytest.param(True, False, id="initialized-database"),
        pytest.param(False, True, id="new-database"),
    ],
)
def test_secrets_are_generated_only_before_postgres_initializes(
    postgres_initialized: bool, generation_allowed: bool
) -> None:
    fields = _seed_task()["block"][0]["vars"]["openbao_secrets_seed_fields"]
    context = {"sim_docker_postgres_initialized": {"stat": {"exists": postgres_initialized}}}

    for field in fields:
        rendered = NativeEnvironment().from_string(field["generate"]).render(**context)
        assert rendered is generation_allowed, field["name"]


@pytest.mark.parametrize("template", [OVERRIDE_TEMPLATE, ENV_TEMPLATE], ids=["override", "env"])
def test_every_template_variable_is_defined_by_the_role(template: str) -> None:
    names = meta.find_undeclared_variables(jinja2.Environment().parse(template))

    assert names <= set(_context())


def test_site_yml_does_not_import_the_specialist_eval_playbook() -> None:
    site = (ROOT / "playbooks/site.yml").read_text(encoding="utf-8")

    assert "specialist-eval" not in site
    assert "sim_docker" not in site
    assert "sim_eval_group" not in site


def test_specialist_eval_sim_play_is_isolated() -> None:
    plays = yaml.safe_load((ROOT / "playbooks/specialist-eval.yml").read_text(encoding="utf-8"))

    play = next(play for play in plays if play.get("hosts") == "sim_eval_group")
    assert play["become"] is True
    block = play["tasks"][0]
    assert "block" in block and "rescue" in block
    assert block["block"][-1]["ansible.builtin.include_role"]["name"] == "sim_docker"
    assert block["rescue"][-1]["vars"]["_isolated_play_name"] == "sim_docker"


def test_inventory_maps_only_the_sim_eval_tag_to_its_own_group() -> None:
    inventory = (ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml").read_text(encoding="utf-8")

    # Rendered tag-to-group coverage: tests/specialist_eval/test_eval_targeting.py.
    assert "intersect(['sim-eval', 'dify-eval', 'langflow-eval'])" in inventory
    assert "map('regex_replace', '-eval$', '_eval_group')" in inventory
    assert "sim_group" not in inventory
