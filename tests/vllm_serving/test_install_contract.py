"""vLLM-owned post-install CLI links remain on the default guest path."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/vllm_serving"


def test_campaign_tools_are_linked_onto_the_default_path_after_install():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/install.yml").read_text(encoding="utf-8"))
    names = [task["name"] for task in tasks]
    link = tasks[names.index("Put the vLLM and Hugging Face CLIs on the default PATH")]
    shared_tasks = yaml.safe_load(
        (REPO_ROOT / "roles/nvidia_gpu_guest/tasks/main.yml").read_text(encoding="utf-8")
    )
    main_tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    install_engine_index = next(
        index for index, task in enumerate(main_tasks) if task.get("name") == "Install the selected vLLM engine"
    )
    install_shared_index = next(
        index for index, task in enumerate(main_tasks) if task.get("name") == "Install shared NVIDIA guest prerequisites"
    )

    assert link["ansible.builtin.file"]["state"] == "link"
    assert {item["name"] for item in link["loop"]} == {"vllm", "hf"}
    assert names.index(link["name"]) > names.index("Install the pinned vLLM build with SM120 b12x kernels")
    assert any(task.get("name") == "Install the pinned Hugging Face CLI in the model cache" for task in shared_tasks)
    assert install_shared_index < install_engine_index
