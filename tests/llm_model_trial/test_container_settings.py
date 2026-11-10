from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/llm_model_trial"
SPEC = importlib.util.spec_from_file_location(
    "model_trial", ROLE / "filter_plugins/model_trial.py"
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)
REDACT = yaml.safe_load((ROLE / "defaults/main/00-trial.yml").read_text())["llm_model_trial_redact_regex"]
DIGEST = "example/model@sha256:" + "a" * 64


def settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "image": "",
        "env": {},
        "args": [],
        "window_minutes": 60,
        "redact_regex": REDACT,
        "target_group": "llm_gpu_group",
        "baseline_group": "llm_gpu_group",
        "target_hosts": ["gpu-baseline"],
        "baseline_hosts": ["gpu-baseline"],
    }
    base.update(overrides)
    return base


def test_default_mode_admits_no_container_inputs() -> None:
    assert PLUGIN.validate_container_settings(settings()) == {
        "container_mode": False,
        "separate_guest": False,
    }


def test_default_mode_rejects_container_inputs_without_image() -> None:
    with pytest.raises(ValueError, match="require llm_model_trial_container_image"):
        PLUGIN.validate_container_settings(settings(args=["--max-model-len", "4096"]))
    with pytest.raises(ValueError, match="require llm_model_trial_container_image"):
        PLUGIN.validate_container_settings(settings(env={"MODE": "x"}))


def test_separate_guest_requires_the_container_image() -> None:
    with pytest.raises(ValueError, match="container-only"):
        PLUGIN.validate_container_settings(
            settings(target_group="llm_candidate_group", target_hosts=["gpu-candidate"])
        )


@pytest.mark.parametrize(
    "image",
    [
        "example/model:latest",
        "example/model",
        "Example/Model@sha256:" + "a" * 64,
        "example/model@sha256:" + "a" * 63,
        "example/model@sha256:" + "A" * 64,
        DIGEST + "\n",
    ],
)
def test_image_must_be_digest_pinned(image: str) -> None:
    with pytest.raises(ValueError, match="container_image must be"):
        PLUGIN.validate_container_settings(settings(image=image))


def test_digest_pinned_image_with_tag_is_admitted() -> None:
    assert PLUGIN.validate_container_settings(
        settings(image="registry.example/vllm/vllm-openai:v0.11@sha256:" + "b" * 64)
    ) == {"container_mode": True, "separate_guest": False}


@pytest.mark.parametrize(
    "value",
    ["hf_abcdefghijklmnop", "Bearer abc123def456", "sk-abcdef123456", "token=abc123"],
)
def test_secret_looking_env_value_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="looks like a secret"):
        PLUGIN.validate_container_settings(settings(image=DIGEST, env={"VLLM_LOG": value}))


def test_benign_env_values_and_names_are_admitted() -> None:
    assert PLUGIN.validate_container_settings(
        settings(image=DIGEST, env={"VLLM_LOGGING_LEVEL": "INFO", "CUDA_VISIBLE_DEVICES": 0})
    )["container_mode"] is True


def test_env_key_must_be_an_environment_variable_name() -> None:
    with pytest.raises(ValueError, match="environment variable names"):
        PLUGIN.validate_container_settings(settings(image=DIGEST, env={"BAD-KEY": "1"}))


@pytest.mark.parametrize("args", [[1], ["--port\n10435"], [""]])
def test_container_args_must_be_single_line_strings(args: list[Any]) -> None:
    with pytest.raises(ValueError, match="container_args must be"):
        PLUGIN.validate_container_settings(settings(image=DIGEST, args=args))


@pytest.mark.parametrize("window", [0, 61, True, "60", 1.5])
def test_window_is_bounded_to_one_through_sixty_minutes(window: Any) -> None:
    with pytest.raises(ValueError, match="window_minutes must be"):
        PLUGIN.validate_container_settings(settings(window_minutes=window))


@pytest.mark.parametrize("window", [1, 60])
def test_window_bounds_are_inclusive(window: int) -> None:
    assert PLUGIN.validate_container_settings(settings(window_minutes=window))["container_mode"] is False


def test_separate_guest_with_one_distinct_host_is_admitted() -> None:
    assert PLUGIN.validate_container_settings(
        settings(
            image=DIGEST,
            target_group="llm_candidate_group",
            target_hosts=["gpu-candidate"],
        )
    ) == {"container_mode": True, "separate_guest": True}


@pytest.mark.parametrize(
    ("target_hosts", "baseline_hosts", "message"),
    [
        (["gpu-candidate", "gpu-other"], ["gpu-baseline"], "exactly one host"),
        ([], ["gpu-baseline"], "exactly one host"),
        (["gpu-baseline"], ["gpu-baseline"], "must not be the baseline guest"),
        (["gpu-candidate"], ["gpu-baseline", "gpu-other"], "must not be the baseline guest"),
    ],
)
def test_separate_guest_placement_is_checked(
    target_hosts: list[str], baseline_hosts: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PLUGIN.validate_container_settings(
            settings(
                image=DIGEST,
                target_group="llm_candidate_group",
                target_hosts=target_hosts,
                baseline_hosts=baseline_hosts,
            )
        )
