"""Self-check for the OpenBao GitHub token helper (files/hermes-gh-token.py)."""
import getpass
import os
import runpy
import types
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
MOD = types.SimpleNamespace(**runpy.run_path(str(ROLE / "files" / "hermes-gh-token.py"), run_name="helper"))
ADDR = "https://bao.example.test/"


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


def test_parse_sets_groups_by_boundary():
    spec = "review-public=public:hermes-review-public review-private=private:hermes-review-private "
    assert MOD.parse_sets(spec) == {
        "public": {"review-public": "hermes-review-public"},
        "private": {"review-private": "hermes-review-private"},
    }
    for bad in ("", "review", "review=public", "review=:x", "=public:x", "r=pub-lic:x"):
        with pytest.raises(ValueError):
            MOD.parse_sets(bad)


def test_mint_paths_and_revoke():
    bao = FakeBao({"hermes-review-public": "ghs_r", "hermes-author-public": "ghs_a"})
    sets = {"review-public": "hermes-review-public", "author-public": "hermes-author-public"}
    out = MOD.mint_boundary(ADDR, "github-hermes", "rid", "sid", sets, http=bao)
    assert out == {"review-public": "ghs_r", "author-public": "ghs_a"}
    assert [c[0] for c in bao.calls] == [
        "https://bao.example.test/v1/auth/approle/login",
        "https://bao.example.test/v1/github-hermes/token/hermes-review-public",
        "https://bao.example.test/v1/github-hermes/token/hermes-author-public",
        "https://bao.example.test/v1/auth/token/revoke-self",
    ]
    assert bao.calls[0][1] == {"role_id": "rid", "secret_id": "sid"}
    assert all(c[2] == "bao-tok" for c in bao.calls[1:])


def test_missing_token_still_revokes():
    bao = FakeBao({})
    with pytest.raises(RuntimeError):
        MOD.mint_boundary(ADDR, "m", "r", "s", {"review-public": "hermes-review-public"}, http=bao)
    assert bao.calls[-1][0].endswith("/auth/token/revoke-self")


def test_failed_login_raises():
    with pytest.raises(RuntimeError):
        MOD.mint_boundary(ADDR, "m", "r", "s", {"k": "x"}, http=FakeBao(login_token=""))


def test_write_tokens_mode(tmp_path):
    d = tmp_path / "gh"
    MOD.write_tokens(str(d), getpass.getuser(), {"review-public": "ghs_r"})
    assert (d / "review-public").read_text() == "ghs_r"
    assert oct(os.stat(d / "review-public").st_mode & 0o777) == "0o600"
    assert oct(os.stat(d).st_mode & 0o777) == "0o700"


def test_main_requires_per_boundary_credentials(monkeypatch, capsys):
    for k, v in {"BAO_ADDR": ADDR, "GH_TOKEN_MOUNT": "m", "GH_TOKEN_DIR": "/nonexistent",
                 "GH_TOKEN_OWNER": "nobody", "GH_TOKEN_SETS": "review-public=public:x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HERMES_BAO_ROLE_ID_PUBLIC", raising=False)
    monkeypatch.delenv("HERMES_BAO_SECRET_ID_PUBLIC", raising=False)
    assert MOD.main() == 2
    assert "HERMES_BAO_ROLE_ID_PUBLIC" in capsys.readouterr().err
