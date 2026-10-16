"""Check card sampling defaults and native context limits in rendered units."""

from test_engine_profiles import _exec_start, _profiles, _render


def test_lightning_profile_renders_the_card_generation_defaults():
    profiles = _profiles()
    profile_name = next(name for name in profiles if name.startswith("nemotron-lightning-256k-"))
    exec_start = _exec_start(_render(profile_name, profiles[profile_name]))

    assert "--generation-config auto" in exec_start
    assert "--override-generation-config" in exec_start
    assert '"temperature": 1.0, "top_p": 0.95' in exec_start


def test_flash_next_native_context_renders_without_yarn_extension():
    profiles = _profiles()
    profile_name = next(name for name in profiles if name.startswith("flash-next-262k-"))
    rendered = _render(profile_name, profiles[profile_name])
    exec_start = _exec_start(rendered)

    assert profiles[profile_name]["max_model_len"] == 262144
    assert "--max-model-len 262144" in exec_start
    assert "VLLM_ALLOW_LONG_MAX_MODEL_LEN" not in rendered
    assert "--hf-overrides" not in rendered
