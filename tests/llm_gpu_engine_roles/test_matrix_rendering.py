"""Check card sampling defaults and native context limits in rendered units."""

from test_engine_profiles import _exec_start, _profiles, _registry_profiles, _render


def test_lightning_profile_renders_the_card_generation_defaults():
    profiles = _profiles()
    profile_name = next(name for name in profiles if name.startswith("nemotron-lightning-256k-"))
    exec_start = _exec_start(_render(profile_name, profiles[profile_name]))

    assert "--generation-config auto" in exec_start
    assert "--override-generation-config" in exec_start
    assert '"temperature": 1.0, "top_p": 0.95' in exec_start


def test_flash_next_native_context_profiles_are_retired_from_the_registry():
    profiles = _profiles()
    profile_name = next(name for name in profiles if name.startswith("flash-next-262k-"))

    assert profiles[profile_name]["max_model_len"] == 262144
    assert profiles[profile_name]["enabled"] is False
    assert profile_name not in _registry_profiles()
