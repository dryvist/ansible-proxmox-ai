from __future__ import annotations

import re
from typing import Any


_CONTAINER_IMAGE_RE = re.compile(r"[a-z0-9./-]+(:[A-Za-z0-9._-]+)?@sha256:[a-f0-9]{64}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_container_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate container-mode inputs and placement; raise ValueError on any violation.

    Returns the mode flags the playbook branches on. Default mode (no image)
    admits no container inputs and keeps the baseline guest as the candidate guest.
    """
    image = settings["image"]
    env = settings["env"]
    args = settings["args"]
    window = settings["window_minutes"]
    target_group = settings["target_group"]
    baseline_group = settings["baseline_group"]
    target_hosts = settings["target_hosts"]
    baseline_hosts = settings["baseline_hosts"]
    redact = re.compile(settings["redact_regex"])

    if isinstance(window, bool) or not isinstance(window, int) or not 3 <= window <= 60:
        raise ValueError("llm_model_trial_window_minutes must be an integer from 3 to 60")
    if not isinstance(env, dict) or not isinstance(args, list):
        raise ValueError("llm_model_trial_container_env must be a dict and llm_model_trial_container_args a list")
    if not image:
        if env or args:
            raise ValueError("llm_model_trial_container_env and _args require llm_model_trial_container_image")
        if target_group != baseline_group:
            raise ValueError("a separate target group requires llm_model_trial_container_image (container-only)")
        return {"container_mode": False, "separate_guest": False}
    if not _CONTAINER_IMAGE_RE.fullmatch(image):
        raise ValueError("llm_model_trial_container_image must be name@sha256:<64 hex digits>")
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
            raise ValueError("llm_model_trial_container_env keys must be environment variable names")
        if not isinstance(value, (str, int, float, bool)) or redact.search(str(value)):
            raise ValueError(f"llm_model_trial_container_env value for {key} looks like a secret")
    if not all(isinstance(arg, str) and arg and "\n" not in arg for arg in args):
        raise ValueError("llm_model_trial_container_args must be single-line, non-empty strings")
    separate = target_group != baseline_group
    if separate:
        if len(target_hosts) != 1:
            raise ValueError("the target group must resolve to exactly one host")
        if len(baseline_hosts) != 1 or target_hosts[0] == baseline_hosts[0]:
            raise ValueError("the target host must not be the baseline guest")
    return {"container_mode": True, "separate_guest": separate}


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "llm_model_trial_validate_container": validate_container_settings,
        }
