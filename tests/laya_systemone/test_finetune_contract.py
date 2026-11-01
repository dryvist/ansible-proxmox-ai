"""Contracts for the opt-in Laya fine-tune path."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/laya_systemone"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/20-finetune.yml").read_text())


def _walk_tasks(tasks: list[dict]) -> Iterator[dict]:
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            nested = task.get(section)
            if isinstance(nested, list):
                yield from _walk_tasks(nested)


def _find_task(tasks: list[dict], name: str) -> dict:
    return next(task for task in _walk_tasks(tasks) if task.get("name") == name)


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
    assert DEFAULTS["laya_systemone_finetune_base"] == "convaiinnovations/laya"
    assert DEFAULTS["laya_systemone_finetune_base_revision"] == "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
    assert DEFAULTS["laya_systemone_finetune_base_path"].endswith(
        "/base/{{ laya_systemone_finetune_base_revision }}"
    )
    assert DEFAULTS["laya_systemone_finetune_staging_dir"].endswith(
        "/models/.staging-{{ laya_systemone_finetune_name }}"
    )


def test_finetune_tasks_are_gated_and_tagged_with_role_tags() -> None:
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    include = next(task for task in main if task.get("name") == "Optionally fine-tune a Laya model from labeled data")

    assert include["when"] == "laya_systemone_finetune_enabled | bool"
    assert set(include["tags"]) == {"laya_systemone", "laya_systemone_finetune", "llm", "ai"}
    assert set(include["ansible.builtin.include_tasks"]["apply"]["tags"]) == set(include["tags"])


def test_downloaded_csv_files_are_sha256_verified_and_training_reuses_only_matching_manifest() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/finetune.yml").read_text())
    task_text = (ROLE / "tasks/finetune.yml").read_text().lower()

    assert "checksum_algorithm: sha256" in task_text
    assert "laya_systemone_finetune_train_stat.stat.checksum == laya_systemone_finetune_train_sha256" in task_text
    assert "laya_systemone_finetune_eval_stat.stat.checksum == laya_systemone_finetune_eval_sha256" in task_text

    training_name = "Download and train the fine-tuned checkpoint when its manifest is stale"
    training = next(task for task in tasks if task.get("name") == training_name)
    skip_condition = json.dumps(training["when"]).lower()
    for field in (
        "train_sha256",
        "eval_sha256",
        "epochs",
        "text_column",
        "label_column",
        "target_error",
        "seed",
        "base",
        "base_revision",
        "laya_version",
    ):
        assert field in skip_condition

    manifest_task = _find_task([training], "Write the fine-tune completion manifest into staging")
    manifest_content = manifest_task["ansible.builtin.copy"]["content"]
    for field in (
        "epochs",
        "text_column",
        "label_column",
        "target_error",
        "seed",
        "base",
        "base_revision",
        "laya_version",
    ):
        assert f"'{field}':" in manifest_content

    for prefix in ("train", "eval"):
        name = "training" if prefix == "train" else "validation"
        checksum = _find_task(
            [training], f"Check the existing {name} CSV checksum before download"
        )
        download = _find_task([training], f"Download the {name} CSV when missing or corrupt")
        assert checksum["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
        assert checksum["ansible.builtin.stat"]["path"] == (
            f"{{{{ laya_systemone_finetune_{prefix}_path }}}}"
        )
        assert checksum["register"].endswith(f"{prefix}_existing_stat")
        assert "creates" not in download["ansible.builtin.command"]
        assert download["when"]
        assert f"{prefix}_existing_stat.stat.checksum" in download["when"]
        assert f"{prefix}_sha256" in download["when"]
        assert download["ansible.builtin.command"]["argv"][-1] == (
            f"{{{{ laya_systemone_finetune_{prefix}_path }}}}.download"
        )
        temp_path = f"{{{{ laya_systemone_finetune_{prefix}_path }}}}.download"
        final_path = f"{{{{ laya_systemone_finetune_{prefix}_path }}}}"
        downloaded = _find_task([training], f"Check the downloaded {name} CSV checksum")
        verify = _find_task([training], f"Verify the downloaded {name} CSV checksum")
        move = _find_task([training], f"Move the verified {name} CSV into place")
        assert downloaded["ansible.builtin.stat"]["path"] == temp_path
        assert f"{prefix}_sha256" in " ".join(verify["ansible.builtin.assert"]["that"])
        assert move["ansible.builtin.command"]["argv"][-2:] == [temp_path, final_path]
        order = [task.get("name") for task in _walk_tasks([training])]
        assert order.index(download["name"]) < order.index(verify["name"]) < order.index(move["name"])

    version = _find_task(tasks, "Read the installed Laya version for fine-tune reuse")
    assert version["check_mode"] is False
    assert "not ansible_check_mode" in version["failed_when"]
    manifest_read = _find_task(tasks, "Read the fine-tune completion manifest")
    assert manifest_read["failed_when"] is False
    manifest_parse = _find_task(tasks, "Parse the existing fine-tune completion manifest")
    assert manifest_parse["rescue"]
    assert _find_task(tasks, "Treat an invalid fine-tune completion manifest as missing")["ansible.builtin.set_fact"][
        "laya_systemone_finetune_manifest"
    ] == {}

    train_task = _find_task([training], "Train the Laya model from the verified CSV files")
    train_argv = train_task["ansible.builtin.command"]["argv"]
    out_index = train_argv.index("--out")
    assert train_argv[out_index + 1] == "{{ laya_systemone_finetune_staging_dir }}"
    base_index = train_argv.index("--base")
    assert train_argv[base_index + 1] == "{{ laya_systemone_finetune_base_path }}"
    assert "creates" not in train_task["ansible.builtin.command"]
    assert train_task["environment"] == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }

    assert set(DEFAULTS["laya_systemone_finetune_base_sha256"]) == {
        "model.safetensors",
        "rl_agent_config.json",
        "encoder/config.json",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_config.json",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in DEFAULTS["laya_systemone_finetune_base_sha256"].values())

    base_download = _find_task([training], "Download the pinned Laya base checkpoint when missing or unverified")
    download_code = base_download["ansible.builtin.command"]["argv"][-1]
    assert "when" not in base_download
    assert (
        download_code.index("verify_digests(base_dir")
        < download_code.index("shutil.rmtree(")
        < download_code.index("snapshot_download(repo_id")
    )
    assert "allow_patterns=sorted(expected)" in download_code
    base_verify = _find_task([training], "Verify the pinned Laya base checkpoint before training")
    verify_code = base_verify["ansible.builtin.command"]["argv"][-1]
    assert "resolve_revision" in verify_code
    assert "verify_digests" in verify_code
    assert "HfApi" not in verify_code
    assert base_verify["environment"]["HF_HUB_OFFLINE"] == "1"
    assert base_verify["no_log"] is True
    ordered_names = [task.get("name") for task in _walk_tasks([training])]
    assert ordered_names.index(base_verify["name"]) < ordered_names.index(train_task["name"])

    model_stat = _find_task([training], "Check the staged model.safetensors file")
    config_stat = _find_task([training], "Check the staged rl_agent_config.json file")
    validation = _find_task([training], "Validate staged checkpoint files before promotion")
    validation_conditions = " ".join(validation["ansible.builtin.assert"]["that"])
    assert model_stat["ansible.builtin.stat"]["path"].endswith("/model.safetensors")
    assert config_stat["ansible.builtin.stat"]["path"].endswith("/rl_agent_config.json")
    assert "staging_model_stat.stat.exists" in validation_conditions
    assert "staging_model_stat.stat.size" in validation_conditions
    assert "staging_config_stat.stat.exists" in validation_conditions

    ordered_names = [task.get("name") for task in _walk_tasks([training])]
    promotion = _find_task([training], "Promote the validated fine-tuned model atomically")
    promotion_names = [task.get("name") for task in promotion["block"]]
    assert ordered_names.index(validation["name"]) < ordered_names.index(promotion["name"])
    assert promotion_names.index("Rename the current model directory to .prev") < promotion_names.index(
        "Move the validated staging directory into place"
    )
    restore = _find_task(promotion["rescue"], "Restore the previous model after a failed promotion")
    restore_argv = restore["ansible.builtin.command"]["argv"]
    assert restore_argv[-2:] == [
        "{{ laya_systemone_finetune_model_dir }}.prev",
        "{{ laya_systemone_finetune_model_dir }}",
    ]
    assert "not laya_systemone_finetune_paths_after_failed_promotion.results[0].stat.exists" in restore[
        "when"
    ]

    tasks_by_name = {task.get("name"): task for task in tasks}
    recovery = tasks_by_name["Restore the previous model when an interrupted promotion left no current model"]
    assert recovery["ansible.builtin.command"]["argv"][-2:] == [
        "{{ laya_systemone_finetune_model_dir }}.prev",
        "{{ laya_systemone_finetune_model_dir }}",
    ]


def test_laya_starts_in_always_and_failures_are_reported_afterward() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/finetune.yml").read_text())
    training_name = "Download and train the fine-tuned checkpoint when its manifest is stale"
    training = next(task for task in tasks if task.get("name") == training_name)

    assert "always" in training
    restart = next(task for task in training["always"] if task.get("name") == "Start Laya after GPU fine-tuning")
    assert restart["ansible.builtin.systemd"]["name"] == "laya-systemone.service"
    assert restart["ansible.builtin.systemd"]["state"] == "started"
    assert not any(task.get("name") == "Start Laya after GPU fine-tuning" for task in tasks)

    failure_name = "Report the fine-tuning failure without exposing dataset output"
    failure = next(task for task in tasks if task.get("name") == failure_name)
    assert failure["ansible.builtin.fail"]["msg"] == (
        "Laya fine-tuning failed; service start was attempted where a unit was present."
    )


def test_every_secret_bearing_finetune_task_hides_its_output() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/finetune.yml").read_text())
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
    task_text = (ROLE / "tasks/finetune.yml").read_text().lower()

    assert "push_to_hub" not in task_text
    assert "upload" not in task_text
