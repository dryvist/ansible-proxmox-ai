"""Replay the pinned HF startup check with the role's supported environment."""

import os
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/llamacpp_release/fixtures/hf-2.1.1-update-check.txt"


def _replay(tmp_path, disabled):
    requests = []
    version_lookups = []

    def installed_version(library):
        version_lookups.append(library)
        return "2.1.1"

    namespace = {
        "Literal": Literal, "Path": Path, "os": os, "time": time,
        "constants": SimpleNamespace(
            HF_HUB_DISABLE_UPDATE_CHECK=disabled,
            CHECK_FOR_UPDATE_DONE_PATH=str(tmp_path / "checked"),
        ),
        "importlib": SimpleNamespace(metadata=SimpleNamespace(version=installed_version)),
        "installation_method": lambda: "uv",
        "_fetch_latest_pypi_version": lambda library: requests.append(library),
    }
    exec(compile(FIXTURE.read_text(), str(FIXTURE), "exec"), namespace)
    namespace["_check_cli_update"]("huggingface_hub")
    return requests, version_lookups


def test_pinned_cli_would_query_pypi_without_the_supported_flag(tmp_path):
    assert _replay(tmp_path, False) == (["huggingface_hub"], ["huggingface_hub"])


def test_shared_role_environment_prevents_the_pinned_cli_update_request(tmp_path):
    for role in ("nvidia_gpu_guest", "llm_gpu_serving"):
        core = yaml.safe_load((ROOT / f"roles/{role}/defaults/main/00-core.yml").read_text())
        version = core[f"{role}_huggingface_hub_version"]
        assert FIXTURE.name == f"hf-{version}-update-check.txt"
    defaults = yaml.safe_load(
        (ROOT / "roles/llamacpp_release/defaults/main/00-release.yml").read_text()
    )
    value = defaults["llamacpp_release_proxy_environment"]["HF_HUB_DISABLE_UPDATE_CHECK"]
    assert value == "1"
    assert _replay(tmp_path, value in {"1", "ON", "YES", "TRUE"}) == ([], [])
