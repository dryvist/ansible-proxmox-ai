"""Root and every profile config render the same plugins.enabled, from one plugin set."""

from __future__ import annotations

import yaml

from _role_files import role_defaults
from test_profile_contract import ROLE_ROOT, _jinja_env, _profiles

TEMPLATES = ROLE_ROOT / "templates"


def _expected(langfuse_enabled: bool) -> list[str]:
    defaults = role_defaults(ROLE_ROOT)
    names = [plugin["name"] for plugin in defaults["hermes_agent_plugins_core"]]
    if langfuse_enabled:
        names += [plugin["name"] for plugin in defaults["hermes_agent_plugins_langfuse"]]
    return names


def test_root_and_every_profile_list_the_same_plugins_enabled() -> None:
    for template in ("config.yaml.j2", "config-profile.yaml.j2"):
        source = (TEMPLATES / template).read_text()
        assert "{% include 'config-plugins.yaml.j2' %}" in source, template

    partial = _jinja_env().get_template("config-plugins.yaml.j2")
    for langfuse_enabled in (False, True):
        expected = _expected(langfuse_enabled)
        root = yaml.safe_load(partial.render(hermes_agent_plugins_enabled=expected))
        assert root == {"plugins": {"enabled": expected}}
        for profile in _profiles():
            rendered = partial.render(hermes_agent_plugins_enabled=expected, hermes_agent_profile=profile)
            assert yaml.safe_load(rendered) == root, profile["name"]
