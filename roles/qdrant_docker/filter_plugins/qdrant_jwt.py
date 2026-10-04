"""Jinja filter: an HS256 JWT carrying a Qdrant collection-access claim.

Qdrant (service.jwt_rbac) accepts a JWT signed with its API key. The token
carries an `access` claim; a token WITHOUT one is granted manage access, so
the claim is validated here and never defaults: an empty or malformed access
list raises instead of producing a token.

The token has no `exp` and a canonical JSON encoding, so the same key and
access list always yield the same token (callers can compare-and-skip). Changing
the API key invalidates every token minted from it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from ansible.errors import AnsibleFilterError

LEVELS = ("r", "rw")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def qdrant_jwt(api_key: str, access: list[dict]) -> str:
    if not isinstance(api_key, str) or not api_key:
        raise AnsibleFilterError("qdrant_jwt: api_key must be a non-empty string")
    if not isinstance(access, list) or not access:
        raise AnsibleFilterError("qdrant_jwt: access must be a non-empty list")
    for entry in access:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"collection", "access"}
            or not isinstance(entry["collection"], str)
            or not entry["collection"]
            or entry["access"] not in LEVELS
        ):
            raise AnsibleFilterError(
                f"qdrant_jwt: each access entry needs a collection and an access of {LEVELS}, got {entry!r}"
            )
    signing_input = f"{_b64(_json({'alg': 'HS256', 'typ': 'JWT'}))}.{_b64(_json({'access': access}))}"
    signature = hmac.new(api_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


class FilterModule:
    def filters(self) -> dict:
        return {"qdrant_jwt": qdrant_jwt}
