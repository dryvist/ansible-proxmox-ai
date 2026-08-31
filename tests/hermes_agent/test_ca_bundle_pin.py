"""The runtime's CA bundle must be the distro's, never the virtualenv's.

Left unpinned, the runtime's startup autodetection settles on the certifi
bundle inside its own virtualenv and writes that path into the process
environment once, for the daemon's whole lifetime. Reinstalling the
virtualenv replaces that file, so any agent run building an HTTP client
during the window dies on a CA bundle that "does not exist" — while the
same path is plainly present by the time anyone investigates.

These are text/order assertions rather than a full template render: the
render needs the role's entire secret surface, and the invariant worth
guarding is only that the pin is present, points outside the virtualenv,
and is gated on the file existing before any unit is asked to trust it.
"""

from __future__ import annotations

import re

from conftest import ROLE_ROOT
from _role_files import role_defaults, role_tasks

ENV_TEMPLATE = ROLE_ROOT / "templates" / "hermes-env.j2"
GATE_TASK = "Gate — the distro CA bundle exists before it is pinned as SSL_CERT_FILE"
ENV_TASK = "Deploy the Hermes secrets file (.env)"


def test_env_template_pins_ssl_cert_file_to_the_role_variable() -> None:
    """The pin exists and is sourced from the role variable, not a literal."""
    text = ENV_TEMPLATE.read_text()
    assert re.search(
        r"^SSL_CERT_FILE=\{\{\s*hermes_agent_ca_bundle_path\s*\}\}\s*$",
        text,
        re.MULTILINE,
    ), (
        "hermes-env.j2 must pin SSL_CERT_FILE to hermes_agent_ca_bundle_path. "
        "Without it the runtime falls back to the certifi bundle inside its "
        "own virtualenv, which a reinstall deletes out from under the daemon."
    )


def test_ca_bundle_default_is_outside_the_virtualenv() -> None:
    """A pin only helps if it names a path the virtualenv cannot replace."""
    defaults = role_defaults(ROLE_ROOT)
    path = defaults["hermes_agent_ca_bundle_path"]
    assert path.startswith("/"), f"CA bundle path must be absolute, got {path!r}"
    # Compare against the install root the role itself declares, so relocating
    # the install keeps this check honest instead of chasing a stale literal.
    install_dir = defaults["hermes_agent_install_dir"].rstrip("/")
    assert not path.startswith(f"{install_dir}/") and "site-packages" not in path, (
        f"hermes_agent_ca_bundle_path ({path}) points back inside the "
        "virtualenv — that is the very lifetime problem the pin exists to "
        "escape. Name a distro-owned bundle instead."
    )


def test_bundle_is_gated_before_the_env_file_is_written() -> None:
    """Pinning a missing bundle would break every outbound HTTPS call.

    Worse than the bug being fixed, so the existence check has to run first,
    in real execution order — not merely exist somewhere in the role.
    """
    names = [task.get("name") for task in role_tasks(ROLE_ROOT)]
    assert GATE_TASK in names, f"missing CA-bundle gate task: {GATE_TASK!r}"
    assert ENV_TASK in names, f"missing .env deploy task: {ENV_TASK!r}"
    assert names.index(GATE_TASK) < names.index(ENV_TASK), (
        "the CA-bundle gate must run before .env is rendered, otherwise a "
        "converge can write a pin to a bundle that is not there."
    )
