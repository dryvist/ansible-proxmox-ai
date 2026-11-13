"""Declared serving metrics match each active profile's serving capacity."""

from __future__ import annotations

from llm_gpu_serving.declared_metrics_helpers import (
    COLLECTOR_DIR,
    LEGACY_GROUP,
    LEGACY_ROOT,
    LLAMACPP_ROOT,
    SGLANG_ROOT,
    VLLM_ROOT,
    _capacity_from_flags,
    _expected,
    _guest_vars,
    _legacy_context,
    _llamacpp_context,
    _load,
    _render_publish,
    _vllm_context,
    group_var,
)


def test_vllm_medium_profile_declares_its_slots_and_context():
    context = _vllm_context(legacy=False)
    profile = context["vllm_serving_profiles"]["medium-a"]
    dest, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_group_member_labels_the_vllm_lane_gpu_secondary():
    context = _vllm_context(legacy=True)
    profile = context["vllm_serving_profiles"]["medium-a"]
    _, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert content == _expected(
        group_var("llm_gpu_accelerator", LEGACY_GROUP),
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_llama_cpp_profile_declares_its_slots_and_per_slot_context():
    context = _legacy_context("small", legacy=True)
    profile = context["llm_gpu_serving_profiles"]["small"]
    dest, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator", LEGACY_GROUP),
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_active_and_co_resident_profiles_are_published_together():
    selected_profiles = ["small", "small-c4-64k"]
    context = _legacy_context(
        "small",
        legacy=True,
        co_resident_profiles=selected_profiles[1:],
    )
    dest, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    accelerator = group_var("llm_gpu_accelerator", LEGACY_GROUP)
    expected = "".join(
        _expected(
            accelerator,
            profile_name,
            context["llm_gpu_serving_profiles"][profile_name]["engine"],
            context["llm_gpu_serving_profiles"][profile_name]["max_num_seqs"],
            context["llm_gpu_serving_profiles"][profile_name]["max_model_len"],
        )
        for profile_name in selected_profiles
    )
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == expected


def test_non_legacy_llama_cpp_profile_labels_gpu_primary():
    context = _legacy_context("small", legacy=False)
    profile = context["llm_gpu_serving_profiles"]["small"]
    _, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_llamacpp_serving_profile_publishes_through_the_shared_task():
    context = _llamacpp_context("small", legacy=False)
    profile = context["llamacpp_serving_profiles"]["small"]
    dest, content = _render_publish(LLAMACPP_ROOT, "tasks/activate.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        context["llamacpp_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_sglang_serving_profile_publishes_through_the_shared_task():
    profiles_file = _load(SGLANG_ROOT / "defaults/main/10-profiles.yml")
    profiles = profiles_file["sglang_serving_profiles"]
    for profile_name, profile in profiles.items():
        context = {
            "sglang_serving_profiles": profiles,
            "sglang_serving_active_profile": profile_name,
            "sglang_serving_engine": profiles_file["sglang_serving_engine"],
        } | _guest_vars(legacy=False)
        dest, content = _render_publish(SGLANG_ROOT, "tasks/activate.yml", context)
        assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
        assert content == _expected(
            group_var("llm_gpu_accelerator"),
            profile_name,
            context["sglang_serving_engine"],
            profile["max_num_seqs"],
            profile["max_model_len"],
        )


def test_declared_metrics_match_rendered_start_flag_values():
    vllm_context = _vllm_context(legacy=False)
    vllm_profile = vllm_context["vllm_serving_profiles"]["medium-a"]
    _, vllm_content = _render_publish(VLLM_ROOT, "tasks/activate.yml", vllm_context)
    assert vllm_content == _expected(
        group_var("llm_gpu_accelerator"),
        "medium-a",
        vllm_context["vllm_serving_engine"],
        *_capacity_from_flags(
            VLLM_ROOT / "templates/vllm-serving.service.j2",
            {"vllm_serving_profile": vllm_profile},
            "vllm",
        ),
    )

    llamacpp_context = _llamacpp_context("small", legacy=False)
    llamacpp_profile = llamacpp_context["llamacpp_serving_profiles"]["small"]
    _, llamacpp_content = _render_publish(
        LLAMACPP_ROOT,
        "tasks/activate.yml",
        llamacpp_context,
    )
    assert llamacpp_content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        llamacpp_context["llamacpp_serving_engine"],
        *_capacity_from_flags(
            LLAMACPP_ROOT / "templates/llama-cpp-serve-args.j2",
            {"lc_profile": llamacpp_profile},
            "llama_cpp",
        ),
    )

    legacy_context = _legacy_context(
        "small",
        legacy=True,
        co_resident_profiles=["small-c4-64k"],
    )
    selected_profiles = ["small", "small-c4-64k"]
    _, legacy_content = _render_publish(LEGACY_ROOT, "tasks/main.yml", legacy_context)
    legacy_accelerator = group_var("llm_gpu_accelerator", LEGACY_GROUP)
    legacy_expected = "".join(
        _expected(
            legacy_accelerator,
            profile_name,
            legacy_context["llm_gpu_serving_profiles"][profile_name]["engine"],
            *_capacity_from_flags(
                LEGACY_ROOT / "templates/llama-cpp-serve-args.j2",
                {"lc_profile": legacy_context["llm_gpu_serving_profiles"][profile_name]},
                "llama_cpp",
            ),
        )
        for profile_name in selected_profiles
    )
    assert legacy_content == legacy_expected

    gpu_vllm_context = _legacy_context("medium-a", legacy=True)
    gpu_vllm_profile = gpu_vllm_context["llm_gpu_serving_profiles"]["medium-a"]
    _, gpu_vllm_content = _render_publish(LEGACY_ROOT, "tasks/main.yml", gpu_vllm_context)
    gpu_vllm_expected = _expected(
        legacy_accelerator,
        "medium-a",
        gpu_vllm_profile["engine"],
        *_capacity_from_flags(
            LEGACY_ROOT / "templates/llm-gpu-serving.service.j2",
            {"llm_gpu_serving_profile": gpu_vllm_profile},
            "vllm",
        ),
    )
    assert gpu_vllm_content == gpu_vllm_expected
    assert gpu_vllm_content == _expected(
        legacy_accelerator,
        "medium-a",
        gpu_vllm_profile["engine"],
        *_capacity_from_flags(
            LEGACY_ROOT / "templates/llm-gpu-serving-host.service.j2",
            {"llm_gpu_serving_host_profile": gpu_vllm_profile},
            "vllm",
        ),
    )
