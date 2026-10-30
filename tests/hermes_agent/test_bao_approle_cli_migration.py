from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALLERS = (
    REPO / "roles/hermes_agent/files/hermes-event-relay.py",
    REPO / "roles/hermes_agent/files/hermes-gh-token.py",
    REPO / "roles/hermes_agent/templates/hermes-brain-sync.sh.j2",
    REPO / "roles/open_webui/templates/open-webui-brain-sync.sh.j2",
)


def test_guest_approle_callers_use_the_shared_cli_without_inline_login():
    for path in CALLERS:
        source = path.read_text()
        assert "bao-approle" in source, path
        assert "/v1/auth/approle/login" not in source, path
