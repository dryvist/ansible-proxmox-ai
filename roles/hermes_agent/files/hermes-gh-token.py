#!/usr/bin/env python3
"""Mint GitHub installation tokens from OpenBao and write one file per set.

Runs as root from hermes-gh-token.timer. GH_TOKEN_SETS lists
`KEY=BOUNDARY:SET` entries. For each set it logs in with that boundary's
AppRole (HERMES_BAO_ROLE_ID_<BOUNDARY> / HERMES_BAO_SECRET_ID_<BOUNDARY>),
then calls bao-approle gh-token MOUNT SET, which mints the installation token
and revokes its OpenBao token. It writes the token to <GH_TOKEN_DIR>/<KEY>
(mode 0600, owned by GH_TOKEN_OWNER). Stdout and logs never carry a token
value.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
import tempfile
import urllib.parse

REQUIRED = ("BAO_ADDR", "GH_TOKEN_MOUNT", "GH_TOKEN_SETS", "GH_TOKEN_DIR", "GH_TOKEN_OWNER")


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


def run_bao_approle(args: list[str], addr: str, *, role_id: str = "", secret_id: str = "",
                    token: str = "", run=subprocess.run) -> str:
    """Run the shared CLI without putting credentials in argv or error output."""
    if urllib.parse.urlsplit(addr).scheme != "https":  # bao-approle login does not check the scheme
        raise ValueError("OpenBao URL must be https")
    env = os.environ.copy()
    env["BAO_ADDR"] = addr.rstrip("/")
    if role_id or secret_id:
        env.update({
            "BAO_APPROLE_ROLE_ID": role_id,
            "BAO_APPROLE_SECRET_ID": secret_id,
            "FLOW_LOCK_API_MAX_TIME": "15",
        })
    if token:
        env.update({"BAO_TOKEN": token, "BAO_APPROLE_API_MAX_TIME": "15"})
    try:
        result = run(["bao-approle", *args], check=True, capture_output=True, text=True, env=env)
    except subprocess.CalledProcessError:
        raise RuntimeError(f"bao-approle {args[0]} failed") from None
    if args[0] == "gh-token" and "revoke-self" in result.stderr:
        print("hermes-gh-token: revoke-self failed; token expires with its TTL", file=sys.stderr)
    return result.stdout.strip()


def mint_boundary(addr: str, mount: str, role_id: str, secret_id: str,
                  sets: dict[str, str], run=subprocess.run) -> dict[str, str]:
    """Return {key: installation_token}; the CLI revokes each set's token."""
    tokens = {}
    for key, name in sets.items():
        bao = run_bao_approle(["login"], addr, role_id=role_id, secret_id=secret_id, run=run)
        if not bao:
            raise RuntimeError("AppRole login returned no client_token")
        token = run_bao_approle(["gh-token", mount, name], addr, token=bao, run=run)
        if not token:
            raise RuntimeError(f"no token in response for set {name}")
        tokens[key] = token
    return tokens


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
