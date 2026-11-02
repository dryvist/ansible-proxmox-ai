"""Privacy contracts for Laya fine-tuning: secrets stay hidden and nothing is uploaded."""

from __future__ import annotations

import json

from test_finetune_contract import _finetune_task_text, _load_tasks, _render_env, _walk_tasks


def test_every_secret_bearing_finetune_task_hides_its_output() -> None:
    tasks = _load_tasks()
    secret_references = (
        "community.hashi_vault.",
        "_s3_access_key_id",
        "_s3_secret_access_key",
        "bao_secret_id",
        "client_token",
    )

    for task in _walk_tasks(tasks):
        own_fields = {key: value for key, value in task.items() if key not in {"block", "rescue", "always"}}
        task_text = json.dumps(own_fields, sort_keys=True).lower()
        environment = task.get("environment", {})
        has_secret = any(reference in task_text for reference in secret_references) or (
            isinstance(environment, dict) and "AWS_SECRET_ACCESS_KEY" in environment
        )
        if has_secret:
            assert task.get("no_log") is True, task.get("name")


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
    task_text = _finetune_task_text().lower()

    assert "push_to_hub" not in task_text
    assert "upload" not in task_text
