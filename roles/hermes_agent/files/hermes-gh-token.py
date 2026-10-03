#!/usr/bin/env python3
"""Mint GitHub installation tokens from OpenBao and write one file per set.

Runs as root from hermes-gh-token.timer. GH_TOKEN_SETS lists
`KEY=BOUNDARY:SET` entries. For each boundary it logs in with that
boundary's AppRole (HERMES_BAO_ROLE_ID_<BOUNDARY> /
HERMES_BAO_SECRET_ID_<BOUNDARY>), POSTs <BAO_ADDR>/v1/<GH_TOKEN_MOUNT>/token/<SET>
for each of its sets, writes the token to <GH_TOKEN_DIR>/<KEY> (mode 0600,
owned by GH_TOKEN_OWNER), then revokes its own OpenBao token. Stdout and logs
never carry a token value.
"""

from __future__ import annotations

import json
import os
import pwd
import sys
import tempfile
import urllib.parse
import urllib.request

REQUIRED = ("BAO_ADDR", "GH_TOKEN_MOUNT", "GH_TOKEN_SETS", "GH_TOKEN_DIR", "GH_TOKEN_OWNER")


def post(url: str, body: dict, token: str | None = None) -> dict:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("OpenBao URL must be https")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Vault-Token", token)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def parse_sets(spec: str) -> dict[str, dict[str, str]]:
    """`review-public=public:hermes-review-public ...` -> {boundary: {key: set}}."""
    out: dict[str, dict[str, str]] = {}
    for pair in spec.split():
        key, sep, rest = pair.partition("=")
        boundary, sep2, name = rest.partition(":")
        if not (sep and sep2 and key and boundary.isidentifier() and name):
            raise ValueError(f"bad GH_TOKEN_SETS entry: {pair!r}")
        out.setdefault(boundary, {})[key] = name
    if not out:
        raise ValueError("GH_TOKEN_SETS is empty")
    return out


def mint_boundary(addr: str, mount: str, role_id: str, secret_id: str,
                  sets: dict[str, str], http=post) -> dict[str, str]:
    """Return {key: installation_token} for one AppRole. Always revokes its token."""
    addr = addr.rstrip("/")
    login = http(f"{addr}/v1/auth/approle/login", {"role_id": role_id, "secret_id": secret_id})
    bao = (login.get("auth") or {}).get("client_token")
    if not bao:
        raise RuntimeError("AppRole login returned no client_token")
    try:
        tokens = {}
        for key, name in sets.items():
            data = http(f"{addr}/v1/{mount}/token/{name}", {}, bao)
            tok = (data.get("data") or {}).get("token")
            if not tok:
                raise RuntimeError(f"no token in response for set {name}")
            tokens[key] = tok
        return tokens
    finally:
        try:
            http(f"{addr}/v1/auth/token/revoke-self", {}, bao)
        except (OSError, ValueError) as exc:  # revoke is best effort; the token TTL still bounds it
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
    by_boundary = parse_sets(os.environ.get("GH_TOKEN_SETS", "")) if not missing else {}
    for b in by_boundary:
        missing += [k for k in (f"HERMES_BAO_ROLE_ID_{b.upper()}", f"HERMES_BAO_SECRET_ID_{b.upper()}")
                    if not os.environ.get(k)]
    if missing:
        print(f"hermes-gh-token: missing env: {' '.join(missing)}", file=sys.stderr)
        return 2
    failed = []
    for b, sets in by_boundary.items():
        try:
            tokens = mint_boundary(os.environ["BAO_ADDR"], os.environ["GH_TOKEN_MOUNT"],
                                   os.environ[f"HERMES_BAO_ROLE_ID_{b.upper()}"],
                                   os.environ[f"HERMES_BAO_SECRET_ID_{b.upper()}"], sets)
        except (OSError, ValueError, RuntimeError) as exc:  # one boundary failing must not stop the other
            print(f"hermes-gh-token: {b}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(b)
            continue
        write_tokens(os.environ["GH_TOKEN_DIR"], os.environ["GH_TOKEN_OWNER"], tokens)
        print(f"hermes-gh-token: refreshed {' '.join(sorted(tokens))}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
