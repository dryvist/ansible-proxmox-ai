#!/usr/bin/env python3
"""Mint GitHub installation tokens from OpenBao and write one file per set.

Runs as root from hermes-gh-token.timer. For each KEY=SET pair in
GH_TOKEN_SETS it logs in with the configured AppRole, POSTs
<BAO_ADDR>/v1/<GH_TOKEN_MOUNT>/token/<SET>, writes the token to
<GH_TOKEN_DIR>/<KEY> (mode 0600, owned by GH_TOKEN_OWNER), then revokes its
own OpenBao token. The gh shim reads those files per invocation. Stdout and
logs never carry a token value.
"""

from __future__ import annotations

import json
import os
import pwd
import sys
import tempfile
import urllib.request

REQUIRED = (
    "BAO_ADDR",
    "HERMES_BAO_ROLE_ID",
    "HERMES_BAO_SECRET_ID",
    "GH_TOKEN_MOUNT",
    "GH_TOKEN_SETS",
    "GH_TOKEN_DIR",
    "GH_TOKEN_OWNER",
)


def post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Vault-Token", token)
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https base from config
        raw = resp.read()
    return json.loads(raw) if raw else {}


def parse_sets(spec: str) -> dict[str, str]:
    """`review=hermes-review author=hermes-author` -> {key: set}."""
    out: dict[str, str] = {}
    for pair in spec.split():
        key, sep, name = pair.partition("=")
        if not sep or not key.isidentifier() or not name:
            raise ValueError(f"bad GH_TOKEN_SETS entry: {pair!r}")
        out[key] = name
    if not out:
        raise ValueError("GH_TOKEN_SETS is empty")
    return out


def mint_all(env: dict, sets: dict[str, str], http=post) -> dict[str, str]:
    """Return {key: installation_token}. Revokes the OpenBao token on exit."""
    addr = env["BAO_ADDR"].rstrip("/")
    login = http(
        f"{addr}/v1/auth/approle/login",
        {"role_id": env["HERMES_BAO_ROLE_ID"], "secret_id": env["HERMES_BAO_SECRET_ID"]},
    )
    bao = (login.get("auth") or {}).get("client_token")
    if not bao:
        raise RuntimeError("AppRole login returned no client_token")
    try:
        tokens = {}
        for key, name in sets.items():
            data = http(f"{addr}/v1/{env['GH_TOKEN_MOUNT']}/token/{name}", {}, bao)
            tok = (data.get("data") or {}).get("token")
            if not tok:
                raise RuntimeError(f"no token in response for set {name}")
            tokens[key] = tok
        return tokens
    finally:
        try:
            http(f"{addr}/v1/auth/token/revoke-self", {}, bao)
        except Exception as exc:  # noqa: BLE001 - revoke is best effort; TTL still bounds the token
            print(f"hermes-gh-token: revoke-self failed: {type(exc).__name__}", file=sys.stderr)


def write_tokens(directory: str, owner: str, tokens: dict[str, str]) -> None:
    pw = pwd.getpwnam(owner)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chown(directory, pw.pw_uid, pw.pw_gid)
    os.chmod(directory, 0o700)
    for key, tok in tokens.items():
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{key}.")
        try:
            os.fchmod(fd, 0o600)
            os.fchown(fd, pw.pw_uid, pw.pw_gid)
            os.write(fd, tok.encode())
        finally:
            os.close(fd)
        os.replace(tmp, os.path.join(directory, key))


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print(f"hermes-gh-token: missing env: {' '.join(missing)}", file=sys.stderr)
        return 2
    env = {k: os.environ[k] for k in REQUIRED}
    sets = parse_sets(env["GH_TOKEN_SETS"])
    tokens = mint_all(env, sets)
    write_tokens(env["GH_TOKEN_DIR"], env["GH_TOKEN_OWNER"], tokens)
    print(f"hermes-gh-token: refreshed {' '.join(sorted(tokens))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
