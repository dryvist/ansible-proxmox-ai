"""Fixtures for the agentgateway_docker tests: the rendered /hindsight route and its rules."""

from __future__ import annotations

from typing import Any

import pytest
from hindsight_acl_support import _render_hindsight
from test_authz_render import _routes


@pytest.fixture(scope="module")
def route() -> dict[str, Any]:
    routes = _routes(_render_hindsight())
    return next(r for r in routes if r["matches"][0]["path"].get("pathPrefix") == "/hindsight")


@pytest.fixture(scope="module")
def rules(route: dict[str, Any]) -> list[dict[str, str]]:
    return route["policies"]["authorization"]["rules"]
