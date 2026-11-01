"""Contracts for the opt-in Laya fine-tune path."""

from __future__ import annotations

import json
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/laya_systemone"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/20-finetune.yml").read_text())


def _render_env(*, enabled: bool, model_exists: bool) -> str:
    environment = jinja2.Environment()
    environment.filters["to_json"] = json.dumps
    environment.filters["bool"] = bool
    template = environment.from_string((ROLE / "templates/laya-systemone.env.j2").read_text())
    return template.render(
        laya_systemone_api_key="",
        laya_systemone_finetune_enabled=enabled,
        laya_systemone_finetune_model_dir_stat={"stat": {"isdir": model_exists}},
        laya_systemone_finetune_name="delegation-v1",
        laya_systemone_finetune_model_dir="/var/lib/laya-systemone/models/delegation-v1",
    )


def test_finetune_defaults_are_disabled_and_configuration_is_opt_in() -> None:
    assert DEFAULTS["laya_systemone_finetune_enabled"] is False
    assert DEFAULTS["laya_systemone_finetune_name"] == ""
    assert DEFAULTS["laya_systemone_finetune_bucket"] == "system1"
    assert DEFAULTS["laya_systemone_finetune_bao_kv_path"] == "rustfs/system1/ro"


def test_finetune_tasks_are_gated_and_tagged_with_role_tags() -> None:
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    include = next(task for task in main if task.get("name") == "Optionally fine-tune a Laya model from labeled data")

    assert include["when"] == "laya_systemone_finetune_enabled | bool"
    assert set(include["tags"]) == {"laya_systemone", "laya_systemone_finetune", "llm", "ai"}
    assert set(include["ansible.builtin.include_tasks"]["apply"]["tags"]) == set(include["tags"])


def test_downloaded_csv_files_are_sha256_verified_and_training_is_idempotent() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/finetune.yml").read_text())
    task_text = (ROLE / "tasks/finetune.yml").read_text().lower()

    assert "checksum_algorithm: sha256" in task_text
    assert "laya_systemone_finetune_train_stat.stat.checksum == laya_systemone_finetune_train_sha256" in task_text
    assert "laya_systemone_finetune_eval_stat.stat.checksum == laya_systemone_finetune_eval_sha256" in task_text

    training = next(task for task in tasks if task.get("name") == "Download and train the fine-tuned checkpoint when it is missing")
    train_task = training["block"][-1]["block"][-1]
    assert train_task["name"] == "Train the Laya model from the verified CSV files"
    assert train_task["ansible.builtin.command"]["creates"].endswith("/model.safetensors")


def test_extra_model_is_rendered_only_when_enabled_and_model_directory_exists() -> None:
    disabled = _render_env(enabled=False, model_exists=True)
    missing = _render_env(enabled=True, model_exists=False)
    enabled = _render_env(enabled=True, model_exists=True)

    assert "LAYA_EXTRA_MODELS=" not in disabled
    assert "LAYA_EXTRA_MODELS=" not in missing
    line = next(line for line in enabled.splitlines() if line.startswith("LAYA_EXTRA_MODELS="))
    encoded = line.split("=", maxsplit=1)[1]
    assert encoded.startswith("'") and encoded.endswith("'")
    registration = json.loads(encoded[1:-1])
    assert registration == {"delegation-v1": "/var/lib/laya-systemone/models/delegation-v1"}


def test_finetune_tasks_never_upload_training_data_or_models() -> None:
    task_text = (ROLE / "tasks/finetune.yml").read_text().lower()

    assert "push_to_hub" not in task_text
    assert "huggingface" not in task_text
    assert "upload" not in task_text
