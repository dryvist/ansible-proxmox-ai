"""Self-check for the OpenBao GitHub token helper (files/hermes-gh-token.py)."""
import runpy
import types
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
MOD = types.SimpleNamespace(**runpy.run_path(str(ROLE / "files" / "hermes-gh-token.py"), run_name="helper"))

ENV = {
    "BAO_ADDR": "https://bao.example.test/",
    "HERMES_BAO_ROLE_ID": "rid",
    "HERMES_BAO_SECRET_ID": "sid",
    "GH_TOKEN_MOUNT": "github-hermes",
}


class FakeBao:
    def __init__(self, tokens=None, login_token="bao-tok"):
        self.calls = []
        self.tokens = tokens or {}
        self.login_token = login_token

    def __call__(self, url, body, token=None):
        self.calls.append((url, body, token))
        if url.endswith("/auth/approle/login"):
            return {"auth": {"client_token": self.login_token}} if self.login_token else {}
        if url.endswith("/revoke-self"):
            return {}
        name = url.rsplit("/", 1)[1]
        return {"data": {"token": self.tokens[name]}} if name in self.tokens else {"data": {}}


def test_parse_sets():
    assert MOD.parse_sets("review=hermes-review author=hermes-author") == {
        "review": "hermes-review",
        "author": "hermes-author",
    }
    for bad in ("", "review", "re-view=x", "review="):
        with pytest.raises(ValueError):
            MOD.parse_sets(bad)


def test_mint_paths_and_revoke():
    bao = FakeBao({"hermes-review": "ghs_r", "hermes-author": "ghs_a"})
    out = MOD.mint_all(ENV, {"review": "hermes-review", "author": "hermes-author"}, http=bao)
    assert out == {"review": "ghs_r", "author": "ghs_a"}
    urls = [c[0] for c in bao.calls]
    assert urls == [
        "https://bao.example.test/v1/auth/approle/login",
        "https://bao.example.test/v1/github-hermes/token/hermes-review",
        "https://bao.example.test/v1/github-hermes/token/hermes-author",
        "https://bao.example.test/v1/auth/token/revoke-self",
    ]
    assert bao.calls[0][1] == {"role_id": "rid", "secret_id": "sid"}
    assert all(c[2] == "bao-tok" for c in bao.calls[1:])


def test_missing_token_still_revokes():
    bao = FakeBao({})
    with pytest.raises(RuntimeError):
        MOD.mint_all(ENV, {"review": "hermes-review"}, http=bao)
    assert bao.calls[-1][0].endswith("/auth/token/revoke-self")


def test_failed_login_raises():
    with pytest.raises(RuntimeError):
        MOD.mint_all(ENV, {"review": "hermes-review"}, http=FakeBao(login_token=""))


def test_write_tokens_mode(tmp_path):
    import getpass
    import os

    d = tmp_path / "gh"
    MOD.write_tokens(str(d), getpass.getuser(), {"review": "ghs_r"})
    assert (d / "review").read_text() == "ghs_r"
    assert oct(os.stat(d / "review").st_mode & 0o777) == "0o600"
    assert oct(os.stat(d).st_mode & 0o777) == "0o700"


def test_shim_defaults_to_review_and_rejects_unknown():
    src = (ROLE / "templates" / "hermes-gh-shim.sh.j2").read_text()
    assert 'key="${HERMES_GH_TOKEN_SET:-review}"' in src
    assert "exit 2" in src
