"""Both legacy llm_gpu_serving vLLM units must return cached prompt tokens in usage."""

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "roles/llm_gpu_serving/templates"


@pytest.mark.parametrize(
    ("name", "profile", "branch_end"),
    [
        ("llm-gpu-serving.service.j2", "llm_gpu_serving_profile",
         "{% elif llm_gpu_serving_profile.engine == 'llama_cpp' %}"),
        ("llm-gpu-serving-host.service.j2", "llm_gpu_serving_host_profile", "{% else %}"),
    ],
)
def test_legacy_vllm_unit_returns_prompt_token_details(name, profile, branch_end):
    text = (TEMPLATES / name).read_text()
    start = text.index(f"{{% if {profile}.engine == 'vllm' %}}\nExecStart=")
    end = text.index(branch_end, start)
    assert "--enable-prompt-tokens-details \\" in text[start:end]
