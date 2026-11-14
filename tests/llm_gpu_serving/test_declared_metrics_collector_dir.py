"""The textfile collector directory is created before metrics are published."""

from __future__ import annotations

from pathlib import Path

from llm_gpu_serving.declared_metrics_helpers import (
    CALL_PARAMETERS,
    LEGACY_GROUP,
    LEGACY_PUBLISH_CALL,
    LEGACY_ROOT,
    LLAMACPP_ROOT,
    METRIC_FILE,
    PUBLISH_CALL,
    SHARED_ROOT,
    SGLANG_ROOT,
    TEXTFILE_DIR_KEY,
    VLLM_ROOT,
    _load,
    _run_publish_task,
    _task,
)


def test_publish_writes_metrics_when_the_collector_directory_exists(tmp_path: Path):
    collector_dir = tmp_path / "textfile_collector"
    collector_dir.mkdir()

    result = _run_publish_task(tmp_path, collector_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    content = (collector_dir / METRIC_FILE).read_text(encoding="utf-8")
    assert 'llm_serving_declared_slots{accelerator="example",profile="example",engine="example"} 3' in content


def test_publish_creates_a_missing_collector_directory_and_writes_metrics(tmp_path: Path):
    collector_dir = tmp_path / "textfile_collector"

    result = _run_publish_task(tmp_path, collector_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    content = (collector_dir / METRIC_FILE).read_text(encoding="utf-8")
    assert 'llm_serving_declared_slots{accelerator="example",profile="example",engine="example"} 3' in content


def test_publish_fails_loudly_when_the_collector_directory_cannot_be_created(tmp_path: Path):
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("example", encoding="utf-8")
    collector_dir = blocker / "missing_textfile_collector"

    result = _run_publish_task(tmp_path, collector_dir)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert str(collector_dir) in output
    assert TEXTFILE_DIR_KEY in output
    assert "Cannot publish declared serving metrics" in output
    assert "could not be created" in output


def test_publish_creates_the_collector_directory_and_reports_creation_failure():
    shared = _load(SHARED_ROOT / "tasks/publish.yml")
    ensure = next(
        task
        for task in shared
        if task.get("name") == "Ensure the node_exporter textfile collector directory exists"
    )
    create = next(task for task in ensure["block"] if "ansible.builtin.file" in task)
    failure = next(task for task in ensure["rescue"] if "ansible.builtin.fail" in task)
    copy = next(task for task in shared if "ansible.builtin.copy" in task)
    assert create["ansible.builtin.file"]["path"] == "{{ llm_serving_declared_metrics_textfile_collector_dir }}"
    assert create["ansible.builtin.file"]["state"] == "directory"
    assert TEXTFILE_DIR_KEY in failure["ansible.builtin.fail"]["msg"]
    assert "{{ llm_serving_declared_metrics_textfile_collector_dir }}" in failure["ansible.builtin.fail"]["msg"]
    assert "could not be created" in failure["ansible.builtin.fail"]["msg"]
    assert "when" not in copy
    assert copy["ansible.builtin.copy"]["mode"] == "0644"
    assert copy["ansible.builtin.copy"]["dest"] == (
        "{{ llm_serving_declared_metrics_textfile_collector_dir }}/{{ llm_serving_declared_metrics_metric_file }}"
    )
    for root, activation, render_only in (
        (VLLM_ROOT, "tasks/activate.yml", "vllm_serving_render_only"),
        (SGLANG_ROOT, "tasks/activate.yml", "sglang_serving_render_only"),
        (LEGACY_ROOT, "tasks/main.yml", "llm_gpu_serving_render_only"),
        (LLAMACPP_ROOT, "tasks/activate.yml", "llamacpp_serving_render_only"),
    ):
        call_name = LEGACY_PUBLISH_CALL if root == LEGACY_ROOT else PUBLISH_CALL
        call = _task(root / activation, call_name)
        assert call["ansible.builtin.include_role"] == {
            "name": "llm_serving_declared_metrics",
            "tasks_from": "publish.yml",
        }
        # Each caller passes the profile selection, profile map and engine; the shared task derives the rest.
        expected_parameters = (
            (CALL_PARAMETERS - {"llm_serving_declared_metrics_profile"})
            | {"llm_serving_declared_metrics_profile_names"}
            if root == LEGACY_ROOT
            else CALL_PARAMETERS
        )
        assert set(call["vars"]) == expected_parameters
        assert render_only in call["when"]
    # The directory is declared once, in the shared role's defaults.
    for root in (VLLM_ROOT, SGLANG_ROOT, LEGACY_ROOT, LLAMACPP_ROOT):
        for defaults_file in (root / "defaults/main").glob("*.yml"):
            assert TEXTFILE_DIR_KEY not in defaults_file.read_text(encoding="utf-8")
