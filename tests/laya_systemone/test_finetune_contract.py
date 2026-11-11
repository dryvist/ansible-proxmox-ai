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
TASKS_DIR = ROLE / "tasks"
FINETUNE_TASKS = TASKS_DIR / "finetune.yml"
FINETUNE_COLLECT_TASKS = TASKS_DIR / "finetune-collect.yml"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/20-finetune.yml").read_text())


def _load_tasks(path: Path = FINETUNE_TASKS, references: list[Path] | None = None) -> list[dict]:
    tasks = yaml.safe_load(path.read_text())
    _expand_includes(tasks, path.parent, references)
    return tasks


def _expand_includes(tasks: list[dict], base_dir: Path, references: list[Path] | None) -> None:
    for task in tasks:
        for section in ("block", "rescue", "always"):
            nested = task.get(section)
            if isinstance(nested, list):
                _expand_includes(nested, base_dir, references)
        include = task.get("ansible.builtin.include_tasks")
        if include is not None:
            include_file = include if isinstance(include, str) else include["file"]
            included_path = (base_dir / include_file).resolve()
            if references is not None:
                references.append(included_path)
            task["block"] = _load_tasks(included_path, references)


def _finetune_task_text() -> str:
    references: list[Path] = []
    _load_tasks(references=references)
    _load_tasks(FINETUNE_COLLECT_TASKS, references)
    return "\n".join(path.read_text() for path in [FINETUNE_TASKS, FINETUNE_COLLECT_TASKS, *references])


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
    assert DEFAULTS["laya_systemone_finetune_action"] == ""
    assert DEFAULTS["laya_systemone_finetune_unit"] == "laya-finetune.service"
    assert DEFAULTS["laya_systemone_finetune_base_download_timeout_seconds"] == 420
    assert DEFAULTS["laya_systemone_finetune_dataset_download_timeout_seconds"] == 30
    assert DEFAULTS["laya_systemone_finetune_health_retries"] == 20
    assert DEFAULTS["laya_systemone_finetune_health_delay_seconds"] == 2
    assert DEFAULTS["laya_systemone_finetune_health_timeout_seconds"] == 5
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
    dispatch = next(task for task in main if task.get("name") == "Dispatch the detached Laya fine-tune job")
    collect = next(task for task in main if task.get("name") == "Collect a detached Laya fine-tune job")

    assert dispatch["when"] == [
        "laya_systemone_finetune_enabled | bool",
        "laya_systemone_finetune_action == 'dispatch'",
    ]
    assert collect["when"] == [
        "laya_systemone_finetune_enabled | bool",
        "laya_systemone_finetune_action == 'collect'",
    ]
    for include in (dispatch, collect):
        assert set(include["tags"]) == {"laya_systemone", "laya_systemone_finetune", "llm", "ai"}
        assert set(include["ansible.builtin.include_tasks"]["apply"]["tags"]) == set(include["tags"])
    # Every action loads the shared NVIDIA guest defaults first; only serving is gated.
    assert main[0]["ansible.builtin.include_role"] == {"name": "nvidia_gpu_guest", "public": True}
    assert "when" not in main[0]
    assert main[1]["when"] == "not laya_systemone_finetune_enabled | bool"


def test_finetune_included_files_exist_and_are_referenced() -> None:
    references: list[Path] = []
    _load_tasks(references=references)
    references.append(FINETUNE_COLLECT_TASKS.resolve())
    _load_tasks(FINETUNE_COLLECT_TASKS, references)

    included_files = set(references)
    finetune_files = {path.resolve() for path in TASKS_DIR.glob("finetune*.yml")} - {
        FINETUNE_TASKS.resolve()
    }
    for shared in ("rustfs-credential.yml", "read-device.yml"):
        finetune_files.add((TASKS_DIR / shared).resolve())
    assert all(path.is_file() for path in included_files)
    assert included_files == finetune_files
    assert all(references.count(path) >= 1 for path in finetune_files)


def test_downloaded_csv_files_are_sha256_verified_and_training_reuses_only_matching_manifest() -> None:
    tasks = _load_tasks()
    task_text = _finetune_task_text().lower()

    assert "checksum_algorithm: sha256" in task_text
    assert "laya_systemone_finetune_train_stat.stat.checksum == laya_systemone_finetune_train_sha256" in task_text
    assert "laya_systemone_finetune_eval_stat.stat.checksum == laya_systemone_finetune_eval_sha256" in task_text

    training_name = "Prepare and dispatch the fine-tuned checkpoint when its manifest is stale"
    training = _find_task(tasks, training_name)
    stale_check = _find_task(
        _load_tasks(ROLE / "tasks/finetune-check-manifest.yml"),
        "Determine whether the installed fine-tune manifest is stale",
    )
    assert "when" not in stale_check
    skip_condition = json.dumps(stale_check["ansible.builtin.set_fact"]).lower()
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

    collection_tasks = _load_tasks(FINETUNE_COLLECT_TASKS)
    manifest_task = next(
        task
        for task in _walk_tasks(collection_tasks)
        if task.get("name") == "Write the fine-tune completion manifest into staging"
        and "ansible.builtin.copy" in task
    )
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
        assert download["ansible.builtin.command"]["argv"][:4] == [
            "timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "{{ laya_systemone_finetune_dataset_download_timeout_seconds }}s",
        ]
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

    train_task = _find_task([training], "Launch the Laya training command as a detached systemd job")
    train_argv = train_task["ansible.builtin.command"]["argv"]
    out_index = train_argv.index("--out")
    assert train_argv[out_index + 1] == "{{ laya_systemone_finetune_staging_dir }}"
    base_index = train_argv.index("--base")
    assert train_argv[base_index + 1] == "{{ laya_systemone_finetune_base_path }}"
    assert "creates" not in train_task["ansible.builtin.command"]
    assert "--no-block" in train_argv
    assert "--remain-after-exit" not in train_argv
    assert "--property=RuntimeMaxSec={{ laya_systemone_finetune_train_timeout_seconds }}" in train_argv
    assert "--property=ExecStopPost=+/usr/bin/systemctl start laya-systemone.service" in train_argv
    assert "--property=StandardOutput=journal" in train_argv
    assert "--property=StandardError=journal" in train_argv
    assert "--setenv=HF_HUB_OFFLINE=1" in train_argv
    assert "--setenv=TRANSFORMERS_OFFLINE=1" in train_argv
    assert train_task["no_log"] is True
    assert "async" not in train_task and "poll" not in train_task

    base_digests = DEFAULTS["laya_systemone_finetune_base_sha256"]
    assert {item["path"] for item in base_digests} == {
        "model.safetensors",
        "rl_agent_config.json",
        "encoder/config.json",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_config.json",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in base_digests)

    base_download = _find_task([training], "Download the pinned Laya base checkpoint when missing or unverified")
    assert base_download["ansible.builtin.command"]["argv"][:4] == [
        "timeout",
        "--signal=TERM",
        "--kill-after=5s",
        "{{ laya_systemone_finetune_base_download_timeout_seconds }}s",
    ]
    download_code = base_download["ansible.builtin.command"]["argv"][-1]
    digest_expression = (
        "{{ laya_systemone_finetune_base_sha256 | "
        "items2dict(key_name='path', value_name='sha256') | to_json }}"
    )
    assert base_download["environment"]["LAYA_BASE_SHA256"] == digest_expression
    assert "when" not in base_download
    assert (
        download_code.index("verify_digests(base_dir")
        < download_code.index("shutil.rmtree(")
        < download_code.index("snapshot_download(repo_id")
    )
    assert "allow_patterns=sorted(expected)" in download_code
    assert "revision=revision" in download_code
    assert 'revision = os.environ["LAYA_BASE_REVISION"]' in download_code
    assert base_download["environment"]["LAYA_BASE_REVISION"] == "{{ laya_systemone_finetune_base_revision }}"
    base_verify = _find_task([training], "Verify the pinned Laya base checkpoint before training")
    verify_code = base_verify["ansible.builtin.command"]["argv"][-1]
    assert "resolve_revision" in verify_code
    assert "verify_digests" in verify_code
    assert "HfApi" not in verify_code
    assert base_verify["environment"]["HF_HUB_OFFLINE"] == "1"
    assert base_verify["environment"]["LAYA_BASE_SHA256"] == digest_expression
    assert base_verify["no_log"] is True
    ordered_names = [task.get("name") for task in _walk_tasks([training])]
    assert ordered_names.index(base_verify["name"]) < ordered_names.index(train_task["name"])

    model_stat = _find_task(collection_tasks, "Check the staged model.safetensors file")
    config_stat = _find_task(collection_tasks, "Check the staged rl_agent_config.json file")
    validation = _find_task(collection_tasks, "Validate staged checkpoint files before promotion")
    validation_conditions = " ".join(validation["ansible.builtin.assert"]["that"])
    assert model_stat["ansible.builtin.stat"]["path"].endswith("/model.safetensors")
    assert config_stat["ansible.builtin.stat"]["path"].endswith("/rl_agent_config.json")
    assert "staging_model_stat.stat.exists" in validation_conditions
    assert "staging_model_stat.stat.size" in validation_conditions
    assert "staging_config_stat.stat.exists" in validation_conditions

    ordered_names = [task.get("name") for task in _walk_tasks(collection_tasks)]
    promotion = _find_task(collection_tasks, "Promote the validated fine-tuned model atomically")
    promotion_names = [task.get("name") for task in promotion["block"]]
    assert ordered_names.index(validation["name"]) < ordered_names.index(promotion["name"])
    assert promotion_names.index("Rename the current model directory to .prev") < promotion_names.index(
        "Move the validated staging directory into place"
    )
    assert promotion_names.index("Move the validated staging directory into place") < promotion_names.index(
        "Restart Laya to load the promoted fine-tuned model"
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

    tasks_by_name = {task.get("name"): task for task in _walk_tasks(tasks)}
    recovery = tasks_by_name["Restore the previous model when an interrupted promotion left no current model"]
    assert recovery["ansible.builtin.command"]["argv"][-2:] == [
        "{{ laya_systemone_finetune_model_dir }}.prev",
        "{{ laya_systemone_finetune_model_dir }}",
    ]
