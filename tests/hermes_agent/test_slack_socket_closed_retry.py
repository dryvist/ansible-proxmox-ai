"""The slack_sdk closed-client reconnect guard actually terminates the loop.

slack_sdk's ``SocketModeClient.connect()`` is a ``while True`` retry loop
that never checks ``self.closed``. Its base class spawns
``run_message_listeners`` fire-and-forget (``ensure_future``, tracked in no
attribute), and a Slack "disconnect" envelope routes that task into
``connect()`` — so the Slack adapter's teardown, which cancels only the
client's three named task attributes before closing the shared aiohttp
session, can leave an orphan retrying the closed session every
``ping_interval`` seconds forever ("RuntimeError: Session is closed", one
traceback every 10s for two days on a live guest). These tests exec the
verbatim pinned ``connect()`` both unpatched (must spin past a timeout —
the bug) and patched by the role's replace task (must give up promptly).
"""

from __future__ import annotations

import asyncio
import textwrap
import types
from typing import Any

import pytest

from conftest import _apply_runtime_patch
from _pinned_sources import PINNED_SLACK_CONNECT_SOURCE

PATCH_NAME = "Patch slack_sdk Socket Mode connect retry to stop when the client is closed"


def _connect_fn(method_source: str) -> Any:
    namespace: dict[str, Any] = {"asyncio": asyncio}
    exec(textwrap.dedent(method_source), namespace)  # noqa: S102
    return namespace["connect"]


def _closed_client() -> tuple[Any, list[tuple[str, str]]]:
    """A stub client in the post-teardown state: closed shared session.

    ``close()`` sets ``closed = True`` first, then closes
    ``aiohttp_client_session`` — after which ``ws_connect`` raises
    ``RuntimeError("Session is closed")`` on every attempt, exactly what the
    orphaned listener task sees.
    """
    logs: list[tuple[str, str]] = []

    class _ClosedSession:
        async def ws_connect(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Session is closed")

    client = types.SimpleNamespace(
        closed=True,
        stale=False,
        current_session=None,
        wss_uri="wss://example.invalid/link",
        aiohttp_client_session=_ClosedSession(),
        proxy=None,
        web_client=types.SimpleNamespace(ssl=None),
        ping_interval=0.005,
        default_auto_reconnect_enabled=True,
        auto_reconnect_enabled=True,
        logger=types.SimpleNamespace(
            level=100,
            exception=lambda msg: logs.append(("exception", msg)),
            info=lambda msg: logs.append(("info", msg)),
            debug=lambda *a, **k: None,
        ),
    )
    return client, logs


def test_unpatched_connect_retries_a_closed_session_forever() -> None:
    # The bug: upstream connect() has no exit condition besides success, and
    # success is impossible once the shared session is closed. wait_for
    # timing out IS the demonstration — remove the patch's guard and this is
    # what a live guest does at one traceback per ping_interval, unbounded.
    connect = _connect_fn(PINNED_SLACK_CONNECT_SOURCE)
    client, logs = _closed_client()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(connect(client), timeout=0.1))
    retries = [entry for entry in logs if entry[0] == "exception"]
    assert len(retries) > 1, "expected repeated Retrying... log calls"


def test_patched_connect_gives_up_once_the_client_is_closed() -> None:
    patched = _apply_runtime_patch(PATCH_NAME, PINNED_SLACK_CONNECT_SOURCE)
    connect = _connect_fn(patched)
    client, logs = _closed_client()
    # Must return promptly instead of looping; a hang here would raise.
    asyncio.run(asyncio.wait_for(connect(client), timeout=1.0))
    assert logs == [
        ("info", "The Socket Mode client is closed; giving up reconnecting")
    ], "the give-up must be logged exactly once, with no Retrying... noise"


def test_patched_connect_still_retries_while_open() -> None:
    # The guard must not suppress legitimate retries: with closed=False the
    # patched loop behaves exactly like upstream and keeps trying.
    patched = _apply_runtime_patch(PATCH_NAME, PINNED_SLACK_CONNECT_SOURCE)
    connect = _connect_fn(patched)
    client, logs = _closed_client()
    client.closed = False
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(connect(client), timeout=0.1))
    assert all(kind == "exception" for kind, _ in logs)
    assert len(logs) > 1
