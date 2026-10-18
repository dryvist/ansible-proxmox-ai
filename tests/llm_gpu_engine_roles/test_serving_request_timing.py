from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_serving_templates_enable_native_request_timing():
    llama_cpp = (ROOT / "roles/llama_cpp/templates/llama-server.service.j2").read_text(
        encoding="utf-8"
    )
    llamacpp_serving = (
        ROOT / "roles/llamacpp_serving/templates/llama-cpp-serve-args.j2"
    ).read_text(encoding="utf-8")
    vllm = (ROOT / "roles/vllm_serving/templates/vllm-serving.service.j2").read_text(
        encoding="utf-8"
    )

    assert llama_cpp.count("--log-jsonl") == 1
    assert llamacpp_serving.count("--log-jsonl") == 1
    assert "--enable-request-id-headers" in vllm
    assert "--enable-per-request-metrics" in vllm
