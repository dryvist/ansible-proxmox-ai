"""The legacy llm_gpu_serving vLLM unit must request priority scheduling."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "roles/llm_gpu_serving/templates/llm-gpu-serving.service.j2"


def _vllm_branch(text: str) -> str:
    start = text.index("{% if llm_gpu_serving_profile.engine == 'vllm' %}\nExecStart=")
    end = text.index("{% elif llm_gpu_serving_profile.engine == 'llama_cpp' %}", start)
    return text[start:end]


def test_legacy_vllm_unit_requests_priority_scheduling():
    assert "--scheduling-policy priority" in _vllm_branch(TEMPLATE.read_text())
