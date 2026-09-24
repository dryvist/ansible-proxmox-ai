#!/usr/bin/env python3
"""fetch_domain.yml resolves each domain's AppRole role_id/secret_id from
OPENBAO_APPROLE_<DOMAIN>_ROLE_ID/_SECRET_ID first, falling back to the
legacy <DOMAIN>_VAULT_ROLE_ID/_SECRET_ID pair -- the same derivation and
resolution order as the ansible-proxmox-apps copy of this role. Before this
fix, this repo's copy read only the legacy names, so a domain whose env
only carried the new names (as the scheduled rotator now publishes them)
resolved empty and failed the converge.

These render the REAL expressions out of the task file, never a
reimplementation, against a fake `lookup('env', ...)` so no real
environment variable is touched.
"""

from pathlib import Path
import re
import unittest
from typing import Any

import yaml
from jinja2 import Environment

TASKS = (
    Path(__file__).resolve().parent.parent
    / "roles" / "openbao_secrets" / "tasks" / "fetch_domain.yml"
)

DERIVE_PREFIXES = "Resolve role_id/secret_id from env for {{ openbao_domain.name }}"
RESOLVE_VALUES = (
    "Resolve role_id/secret_id values, new name, then legacy, then the "
    "operator pair for {{ openbao_domain.name }}"
)


def _tasks():
    return {
        t["name"]: t
        for t in yaml.safe_load(TASKS.read_text(encoding="utf-8"))
        if "name" in t
    }


class ApproleNameResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = _tasks()
        for name in (DERIVE_PREFIXES, RESOLVE_VALUES):
            assert name in cls.tasks, f"{name!r} not found in {TASKS}"
        cls.derive = cls.tasks[DERIVE_PREFIXES]["ansible.builtin.set_fact"]
        cls.resolve = cls.tasks[RESOLVE_VALUES]["ansible.builtin.set_fact"]

    def _resolve(self, domain_name, env, operator_domains=()):
        """env: mapping of env-var name -> value, standing in for the real
        process environment. Ansible's `lookup('env', X)` on an unset var
        returns '' , never Undefined -- mirror that exactly.

        operator_domains: stands in for openbao_secrets_operator_domains
        (defaults/main/10-domains.yml) -- empty unless a test is exercising
        the operator-pair fallback, so every pre-existing test keeps its
        original meaning regardless of that list's real contents."""
        jinja_env = Environment()
        jinja_env.globals["lookup"] = (
            lambda kind, name: env.get(name, "") if kind == "env" else ""
        )
        # regex_replace is an Ansible filter, not native Jinja2.
        jinja_env.filters["regex_replace"] = (
            lambda value, pattern, repl: re.sub(pattern, repl, str(value))
        )
        variables: dict[str, Any] = {
            "openbao_domain": {"name": domain_name},
            "openbao_secrets_operator_domains": list(operator_domains),
        }

        # Every value below is already a full `{{ ... }}`-wrapped string in
        # the source YAML (this role's set_fact convention) -- rendering it
        # directly, never re-wrapping it in another `{{ }}`.
        new_prefix = jinja_env.from_string(
            self.derive["openbao_secrets_domain_env_new_prefix"]
        ).render(**variables)
        legacy_prefix = jinja_env.from_string(
            self.derive["openbao_secrets_domain_env_legacy_prefix"]
        ).render(**variables)
        variables["openbao_secrets_domain_env_new_prefix"] = new_prefix
        variables["openbao_secrets_domain_env_legacy_prefix"] = legacy_prefix

        role_id = jinja_env.from_string(
            self.resolve["openbao_secrets_domain_role_id"]
        ).render(**variables)
        secret_id = jinja_env.from_string(
            self.resolve["openbao_secrets_domain_secret_id"]
        ).render(**variables)
        return role_id, secret_id

    def test_the_new_names_resolve_alone(self):
        # Exactly today's real case: the scheduled rotator publishes only
        # OPENBAO_APPROLE_APPS_ROLE_ID/_SECRET_ID, and the legacy pair was
        # never set for this domain.
        role_id, secret_id = self._resolve(
            "apps",
            {
                "OPENBAO_APPROLE_APPS_ROLE_ID": "new-role",
                "OPENBAO_APPROLE_APPS_SECRET_ID": "new-secret",
            },
        )
        self.assertEqual((role_id, secret_id), ("new-role", "new-secret"))

    def test_the_legacy_names_resolve_alone(self):
        # An unmigrated converge wrapper that still only exports the
        # pre-rotation pair must keep working.
        role_id, secret_id = self._resolve(
            "apps",
            {
                "APPS_VAULT_ROLE_ID": "legacy-role",
                "APPS_VAULT_SECRET_ID": "legacy-secret",
            },
        )
        self.assertEqual((role_id, secret_id), ("legacy-role", "legacy-secret"))

    def test_the_new_names_win_when_both_are_set(self):
        role_id, secret_id = self._resolve(
            "apps",
            {
                "OPENBAO_APPROLE_APPS_ROLE_ID": "new-role",
                "OPENBAO_APPROLE_APPS_SECRET_ID": "new-secret",
                "APPS_VAULT_ROLE_ID": "legacy-role",
                "APPS_VAULT_SECRET_ID": "legacy-secret",
            },
        )
        self.assertEqual((role_id, secret_id), ("new-role", "new-secret"))

    def test_neither_set_resolves_empty(self):
        # The fail-loud guard downstream keys off this staying empty.
        self.assertEqual(self._resolve("apps", {}), ("", ""))

    def test_the_prefix_is_derived_from_the_domain_name_not_a_literal(self):
        # A hyphenated domain name must fold to underscores -- the same
        # regex_replace this role's other env-var derivations use.
        role_id, _ = self._resolve(
            "ai-public", {"OPENBAO_APPROLE_AI_PUBLIC_ROLE_ID": "ai-public-role"}
        )
        self.assertEqual(role_id, "ai-public-role")

    def test_operator_pair_resolves_for_a_listed_domain_with_neither_pair_set(self):
        role_id, secret_id = self._resolve(
            "apps",
            {
                "OPERATOR_VAULT_ROLE_ID": "operator-role",
                "OPERATOR_VAULT_SECRET_ID": "operator-secret",
            },
            operator_domains=("apps",),
        )
        self.assertEqual((role_id, secret_id), ("operator-role", "operator-secret"))

    def test_the_domain_pair_wins_over_the_operator_pair(self):
        role_id, secret_id = self._resolve(
            "apps",
            {
                "OPENBAO_APPROLE_APPS_ROLE_ID": "new-role",
                "OPENBAO_APPROLE_APPS_SECRET_ID": "new-secret",
                "OPERATOR_VAULT_ROLE_ID": "operator-role",
                "OPERATOR_VAULT_SECRET_ID": "operator-secret",
            },
            operator_domains=("apps",),
        )
        self.assertEqual((role_id, secret_id), ("new-role", "new-secret"))

    def test_the_operator_pair_never_leaks_to_an_unlisted_domain(self):
        # The operator identity's policies cover only the domains named in
        # openbao_secrets_operator_domains -- an unlisted domain must not
        # pick up its credentials just because they happen to be set.
        role_id, secret_id = self._resolve(
            "apps",
            {
                "OPERATOR_VAULT_ROLE_ID": "operator-role",
                "OPERATOR_VAULT_SECRET_ID": "operator-secret",
            },
            operator_domains=(),
        )
        self.assertEqual((role_id, secret_id), ("", ""))


if __name__ == "__main__":
    unittest.main()
