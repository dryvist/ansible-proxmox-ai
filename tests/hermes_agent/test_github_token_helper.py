"""Self-check for the OpenBao GitHub token helper (files/hermes-gh-token.py)."""
import getpass
import os
import runpy
import subprocess
import types
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
MOD = types.SimpleNamespace(**runpy.run_path(str(ROLE / "files" / "hermes-gh-token.py"), run_name="helper"))
ADDR = "https://bao.example.test/"


class FakeCLI:
    def __init__(self, tokens=None, login_token="bao-tok"):
        self.calls = []
        self.tokens = tokens or {}
        self.login_token = login_token

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[1] == "login":
            return subprocess.CompletedProcess(args, 0, f"{self.login_token}\n", "")
        name = args[3]
        if name not in self.tokens:
            raise subprocess.CalledProcessError(22, args, stderr="secret_id leaked by mock")
        return subprocess.CompletedProcess(args, 0, f"{self.tokens[name]}\n", "")


def test_parse_sets_groups_by_boundary():
    spec = "review-public=public:hermes-review-public review-private=private:hermes-review-private "
    assert MOD.parse_sets(spec) == {
        "public": {"review-public": "hermes-review-public"},
        "private": {"review-private": "hermes-review-private"},
    }
    for bad in ("", "review", "review=public", "review=:x", "=public:x", "r=pub-lic:x"):
        with pytest.raises(ValueError):
            MOD.parse_sets(bad)


def test_mint_paths_through_shared_cli():
    cli = FakeCLI({"hermes-review-public": "ghs_r", "hermes-author-public": "ghs_a"})
    sets = {"review-public": "hermes-review-public", "author-public": "hermes-author-public"}
    out = MOD.mint_boundary(ADDR, "github-hermes", "rid", "sid", sets, run=cli)
    assert out == {"review-public": "ghs_r", "author-public": "ghs_a"}
    assert [call[0] for call in cli.calls] == [
        ["bao-approle", "login"],
        ["bao-approle", "gh-token", "github-hermes", "hermes-review-public"],
        ["bao-approle", "login"],
        ["bao-approle", "gh-token", "github-hermes", "hermes-author-public"],
    ]
    login_env = cli.calls[0][1]["env"]
    assert login_env["BAO_ADDR"] == "https://bao.example.test"
    assert login_env["BAO_APPROLE_ROLE_ID"] == "rid"
    assert login_env["BAO_APPROLE_SECRET_ID"] == "sid"
    assert cli.calls[1][1]["env"]["BAO_TOKEN"] == "bao-tok"
    assert "sid" not in " ".join(arg for call in cli.calls for arg in call[0])
    assert all(call[1]["check"] is True and call[1]["capture_output"] is True for call in cli.calls)


def test_cli_mint_failure_hides_credentials():
    cli = FakeCLI({})
    with pytest.raises(RuntimeError) as exc:
        MOD.mint_boundary(ADDR, "m", "r", "secret-value", {"review-public": "hermes-review-public"}, run=cli)
    assert str(exc.value) == "bao-approle gh-token failed"
    assert "secret-value" not in str(exc.value)
    assert cli.calls[-1][0] == ["bao-approle", "gh-token", "m", "hermes-review-public"]


def test_revoke_warning_is_logged_without_cli_output(capsys):
    def warn(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "ghs_r\n", "bao-approle: revoke-self failed")

    token = MOD.run_bao_approle(["gh-token", "m", "x"], ADDR, token="bao-tok", run=warn)
    assert token == "ghs_r"
    assert capsys.readouterr().err == "hermes-gh-token: revoke-self failed; token expires with its TTL\n"


def test_failed_login_raises():
    with pytest.raises(RuntimeError):
        MOD.mint_boundary(ADDR, "m", "r", "s", {"k": "x"}, run=FakeCLI(login_token=""))


@pytest.mark.parametrize("addr", ["http://bao.example.test", "file:///etc/shadow"])
def test_refuses_non_https_before_cli(addr):
    cli = FakeCLI({"hermes-review-public": "ghs_r"})
    with pytest.raises(ValueError):
        MOD.mint_boundary(addr, "m", "r", "s", {"review-public": "hermes-review-public"}, run=cli)
    assert cli.calls == []


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
