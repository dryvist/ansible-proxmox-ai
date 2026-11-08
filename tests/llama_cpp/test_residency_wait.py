"""The residency check waits for router-mode loads to finish.

`POST /models/load` returns before llama-server has loaded the model, so a
`GET /models` read taken at once can report "loading" and fail the residency
assert on a model that is about to be resident. The read-back task polls until
no model is mid-load, bounded by two role defaults, and only then asserts.
"""

from pathlib import Path

import yaml


ROLE = Path(__file__).resolve().parents[2] / "roles" / "llama_cpp"
DEFAULTS: dict = {}
for _defaults_file in sorted((ROLE / "defaults" / "main").glob("*.yml")):
    DEFAULTS.update(yaml.safe_load(_defaults_file.read_text()))
VERIFY = yaml.safe_load((ROLE / "tasks" / "verify-residency.yml").read_text())


def _task(name: str) -> dict:
    return next(task for task in VERIFY if task.get("name") == name)


def test_readback_waits_until_no_model_is_loading() -> None:
    readback = _task("Read back which models are actually resident")
    assert "'loading'" in readback["until"]
    assert readback["retries"] == "{{ llama_cpp_residency_retries }}"
    assert readback["delay"] == "{{ llama_cpp_residency_delay_seconds }}"


def test_wait_bounds_are_positive_defaults() -> None:
    assert int(DEFAULTS["llama_cpp_residency_retries"]) > 0
    assert int(DEFAULTS["llama_cpp_residency_delay_seconds"]) > 0


def test_assert_still_reads_only_loaded_models() -> None:
    check = _task("Assert every declared model fits and stayed resident together")
    resident = check["vars"]["_llama_cpp_resident_model_ids"]
    assert "'loaded'" in resident
