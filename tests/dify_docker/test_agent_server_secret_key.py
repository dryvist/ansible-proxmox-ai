"""The Dify Agent Stub key must be a 43-character unpadded base64url value.

Dify 1.18's Agent Stub decodes DIFY_AGENT_SERVER_SECRET_KEY as unpadded
base64url into exactly 32 bytes: 43 characters, the last one carrying no
unused bits. The seed expression in storage_and_secret.yml must generate a
value in that shape when no caller value is given, must reject a stored value
that fails the shape (valid_pattern), and must leave every other stored field
untouched.
"""

import base64
import re

from _admin_secret_shared import SEED_TASK, seed_fields, task_named


ZERO_TAIL_CHARS = "AEIMQUYcgkosw048"


def _upstream_decode(value):
    return base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )


def _seed_variables(server_key):
    return {
        "dify_docker_secret_key_caller": "fixture-secret-key",
        "dify_docker_agent_api_token": "fixture-agent-token",
        "dify_docker_agent_server_secret_key": server_key,
        "dify_docker_agent_local_sandbox_auth_token": "fixture-sandbox-token",
        "dify_docker_admin_password": "fixture-admin-password",
        "dify_docker_admin_email": "fixture-admin@example.com",
    }


def test_generated_value_is_a_valid_unpadded_base64url_key():
    fields = seed_fields([], _seed_variables(""))
    value = fields["DIFY_AGENT_SERVER_SECRET_KEY"]["value"]

    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", value)
    assert len(_upstream_decode(value)) == 32
    assert value[-1] in ZERO_TAIL_CHARS


def test_caller_value_wins_over_generation():
    caller = "caller-supplied-agent-key"

    fields = seed_fields([], _seed_variables(caller))

    assert fields["DIFY_AGENT_SERVER_SECRET_KEY"]["value"] == caller


def test_valid_pattern_matches_only_the_upstream_shape():
    fields = seed_fields([], _seed_variables(""))
    pattern = re.compile(fields["DIFY_AGENT_SERVER_SECRET_KEY"]["valid_pattern"])

    assert fields["DIFY_AGENT_SERVER_SECRET_KEY"]["valid_pattern"] == (
        "^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$"
    )
    valid = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
    assert pattern.fullmatch(valid) is not None


def test_stored_value_that_fails_the_rule_is_planned_for_replacement():
    fields = seed_fields([], _seed_variables(""))
    pattern = re.compile(fields["DIFY_AGENT_SERVER_SECRET_KEY"]["valid_pattern"])

    # The shared seed task plans a field when it is absent or when
    # valid_pattern (re.search) rejects the stored value.
    def planned(stored):
        return stored == "" or pattern.search(stored) is None

    # Missing field is always planned.
    assert planned("") is True
    # The old 50-character alnum shape: wrong length, and base64url-decoding
    # it does not yield 32 bytes.
    assert planned("A1b2C3d4E5f6G7h8I9j0" * 2 + "A1b2C3d4E5") is True
    # A valid stored key is kept.
    assert planned(base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()) is False
    # 43 base64url chars whose last character carries unused bits.
    valid = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
    assert planned(valid[:-1] + "B") is True
    # 43 chars with a character outside the base64url alphabet.
    assert planned("+" + "A" * 42) is True


def test_other_stored_fields_are_never_replaced():
    fields = seed_fields([], _seed_variables(""))
    seed_vars = task_named(SEED_TASK)["block"][0]["vars"]

    # No rotation list: only valid_pattern can plan a stored-field write.
    assert "openbao_secrets_seed_rotate_fields" not in seed_vars
    # Every other field keeps its shape, carries no valid_pattern, and passes
    # the caller value through unchanged, so the shared task keeps the stored
    # value.
    expected = {
        "DIFY_SECRET_KEY": "fixture-secret-key",
        "DIFY_AGENT_API_TOKEN": "fixture-agent-token",
        "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN": "fixture-sandbox-token",
        "DIFY_ADMIN_PASSWORD": "fixture-admin-password",
        "DIFY_ADMIN_EMAIL": "fixture-admin@example.com",
    }
    assert fields.keys() == {
        *expected,
        "DIFY_AGENT_SERVER_SECRET_KEY",
    }
    for name, value in expected.items():
        assert fields[name] == {
            "name": name,
            "length": 50,
            "charset": "alnum",
            "value": value,
        }
