#!/usr/bin/env python3
"""The source-address tolerance in fetch_domain.yml must actually be able to fire.

Background, measured rather than reasoned. On ansible-core 2.21 against
community.hashi_vault's vault_login:

    failed_when: false  -> registered result has failed=False, NO msg, and
                           failed_when_suppressed_exception rendering as
                           "(traceback unavailable)".
    ignore_errors: true -> registered result has failed=True and the module's
                           own msg ("invalid role or secret ID, on post ...").

So `failed_when: false` does not merely rewrite the failure flag; it destroys
the reason. A classification that greps the message and a catch-all that keys
off `.failed` are both dead under it, and the first symptom is an obscure Jinja
attribute error further down when the KV read dereferences a token that was
never issued.

Two halves are asserted here, because a regression in either one restores the
bug on its own:

  1. Structural -- the login task uses ignore_errors and not failed_when, so
     the message the classification reads still exists.
  2. Behavioural -- the classification and the catch-all conditions are pulled
     OUT of the task file and evaluated against synthetic results, so this
     tests the shipped expressions rather than a restatement of them.
"""

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

TASKS = (
    Path(__file__).resolve().parent.parent
    / "roles" / "openbao_secrets" / "tasks" / "fetch_domain.yml"
)

LOGIN = "Log in with the AppRole for {{ openbao_domain.name }}"
CLASSIFY = "Classify a source-address refusal for {{ openbao_domain.name }}"
CATCHALL = "Fail on any other login failure for {{ openbao_domain.name }}"

# A refusal on source-address grounds, as the module reports it.
CIDR_REFUSAL = {"failed": True, "msg": "Module failed: source address unauthorized "
                                       "due to CIDR restrictions, on post /v1/auth/approle/login"}
# Any other refusal. Must NOT be tolerated.
OTHER_FAILURE = {"failed": True, "msg": "Module failed: invalid role or secret ID"}
# A success. vault_login returns `login` only on success.
SUCCESS = {"failed": False, "login": {"auth": {"client_token": "irrelevant"}}}
# What `failed_when: false` produces: the flag is rewritten and the reason is
# gone. Nothing can classify this, which is the point -- it must at least not
# be mistaken for a tolerated refusal.
PRIMITIVE_DAMAGED = {"failed": False}


def _tasks(node):
    if isinstance(node, list):
        for entry in node:
            yield from _tasks(entry)
    elif isinstance(node, dict):
        if "name" in node:
            yield node
        for key in ("tasks", "block", "rescue", "always"):
            if key in node:
                yield from _tasks(node[key])


class LoginClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = {t["name"]: t for t in _tasks(yaml.safe_load(TASKS.read_text()))}
        for name in (LOGIN, CLASSIFY, CATCHALL):
            assert name in cls.tasks, f"{name!r} not found in {TASKS}"
        cls.env = Environment()
        # `bool` is an Ansible filter, not a Jinja builtin, and the shipped
        # conditions use it. Same coercion Ansible applies.
        cls.env.filters["bool"] = lambda v: str(v).strip().lower() in (
            "true", "yes", "on", "1"
        )

        # The shipped expressions, read out of the task file -- never restated.
        cls.classify_expr = cls.tasks[CLASSIFY]["ansible.builtin.set_fact"][
            "openbao_secrets_domain_out_of_scope"
        ]
        cls.catchall_conds = cls.tasks[CATCHALL]["when"]

    def _out_of_scope(self, result):
        rendered = self.env.from_string(self.classify_expr).render(
            openbao_secrets_domain_login=result
        )
        return rendered.strip() == "True"

    def _catchall_fires(self, result):
        out_of_scope = self._out_of_scope(result)
        for cond in self.catchall_conds:
            rendered = self.env.from_string("{{ %s }}" % cond).render(
                openbao_secrets_domain_login=result,
                openbao_secrets_domain_out_of_scope=out_of_scope,
            )
            if rendered.strip() != "True":
                return False
        return True

    # -- structural ------------------------------------------------------

    def test_the_login_keeps_its_failure_reason(self):
        login = self.tasks[LOGIN]
        self.assertTrue(login.get("ignore_errors"),
                        "the login must keep failed=True and its msg")
        self.assertNotIn("failed_when", login,
                         "failed_when on this task destroys the msg the "
                         "classification below reads")

    # -- behavioural -----------------------------------------------------

    def test_a_source_address_refusal_is_tolerated(self):
        self.assertTrue(self._out_of_scope(CIDR_REFUSAL))
        self.assertFalse(self._catchall_fires(CIDR_REFUSAL))

    def test_any_other_refusal_is_fatal(self):
        self.assertFalse(self._out_of_scope(OTHER_FAILURE))
        self.assertTrue(self._catchall_fires(OTHER_FAILURE))

    def test_a_successful_login_neither_skips_nor_fails(self):
        self.assertFalse(self._out_of_scope(SUCCESS))
        self.assertFalse(self._catchall_fires(SUCCESS))

    def test_a_result_stripped_of_its_reason_is_fatal_not_tolerated(self):
        # If the failure primitive is ever swapped back, the reason is gone.
        # The run must then abort loudly rather than silently treat the domain
        # as out of scope and render empty credentials into live config.
        self.assertFalse(self._out_of_scope(PRIMITIVE_DAMAGED))
        self.assertTrue(self._catchall_fires(PRIMITIVE_DAMAGED))


if __name__ == "__main__":
    unittest.main()
