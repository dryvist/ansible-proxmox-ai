"""Pinned upstream source for worker reap/reclaim, goal judge, memory sync,
Slack transport, and the kanban dispatcher tick.

Split out of _pinned_sources.py once the shared module crossed the token
budget: this half holds the worker-reap/reclaim/spawn, goal-judge, memory
sync, kanban dispatcher tick, and Slack socket-mode transport snippets --
the constants a worker/judge/dispatch test needs, without pulling in the
cron scheduler and task-reconcile snippets a goal-mode test needs instead.
Same drift protection as _pinned_sources.py: every constant here is
upstream source EXACTLY as shipped, re-verify with
scripts/verify-pinned-patches.py on a version bump.
"""

from __future__ import annotations

# Both reapers now escalate through one shared helper (2026-09 upstream
# rewrite, hermes_cli/kanban_db_dispatch.py) — the SIGKILL escalation patch
# targets that helper's own body, not either call site, so it must be part
# of both fixtures below for the escalation patch application to have
# anything to match.
PINNED_WORKER_REAP_SOURCE = '''\
def _sigkill(kill, pid: int) -> bool:
    """Best-effort SIGKILL; True when the signal was delivered."""
    try:
        kill(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
        return True
    except (ProcessLookupError, OSError):
        return False


def _reap(pid, signal_fn=None):
    killed = False
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is not None:
        with contextlib.suppress(ProcessLookupError, OSError):
            kill(pid, signal.SIGTERM)
        for _ in range(10):
            if not _pid_alive(pid):
                break
            time.sleep(0.5)
        if _pid_alive(pid):
            killed = _sigkill(kill, pid)
    return killed
'''
PINNED_STALE_RECLAIM_TERMINATE_SOURCE = '''\
def _sigkill(kill, pid: int) -> bool:
    """Best-effort SIGKILL; True when the signal was delivered."""
    try:
        kill(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
        return True
    except (ProcessLookupError, OSError):
        return False


def _reclaim(pid, signal_fn=None):
    info = {"terminated": False, "sigkill": False}
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is None:
        return info

    info["termination_attempted"] = True
    try:
        kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        info["terminated"] = True
        return info
    except OSError:
        return info

    for _ in range(10):
        if not _pid_alive(pid):
            info["terminated"] = True
            return info
        time.sleep(0.5)

    if _pid_alive(pid):
        if not _sigkill(kill, pid):
            return info
        info["sigkill"] = True

    info["terminated"] = not _pid_alive(pid)
    return info
'''
PINNED_WORKER_SPAWN_SOURCE = '''\
def build_worker_argv(task, prompt):
    cmd = []
    cmd.extend([
        "chat",
        "-q", prompt,
    ])
    return cmd
'''
# Verbatim upstream lines at the pinned tag (v2026.7.7.2), from
# plugins/memory/hindsight/__init__.py's queue_prefetch._run() exception
# handler — indentation included, same drift protection as the other
# PINNED_*_SOURCE fixtures above.
PINNED_HINDSIGHT_PREFETCH_SOURCE = (
    "            except Exception as e:\n"
    '                logger.debug("Hindsight recall failed: %s", e, exc_info=True)\n'
)
# Verbatim upstream shape of run_agent.py's _sync_external_memory_for_turn —
# indentation included, same drift protection as the other PINNED_*_SOURCE
# fixtures above. Full behavioral coverage of the four patches this feeds
# lives in test_memory_sync_observability.py; this copy exists only so
# _source_postconditions() below has something to default the new
# hermes_agent_run_agent_source context var to.
PINNED_SYNC_EXTERNAL_MEMORY_SOURCE = '''\
class _Agent:
    def _sync_external_memory_for_turn(
        self,
        *,
        original_user_message,
        final_response,
        interrupted,
        messages=None,
    ) -> None:
        if interrupted or not (self._memory_manager and final_response and original_user_message):
            return
        user_text = _summarize_user_message_for_log(original_user_message, sep="\\n")
        response_text = _summarize_user_message_for_log(final_response, sep="\\n")
        if not (user_text and response_text):
            return
        try:
            sync_kwargs = {"session_id": self.session_id or ""}
            if messages is not None:
                sync_kwargs["messages"] = messages
            self._memory_manager.sync_all(
                user_text,
                response_text,
                **sync_kwargs,
            )
            if not is_trivial_prompt(user_text):
                self._memory_manager.queue_prefetch_all(user_text, session_id=self.session_id or "")
        except Exception:
            pass
'''
# Verbatim upstream producer of the transport-failure flag the guard keys on
# (judge_goal's except handler; the trailing True IS the flag). Kept pinned so
# the fail-closed test proves the converge assert goes red if upstream stops
# setting it.
PINNED_JUDGE_ERROR_SENTINEL_SOURCE = (
    '        return "continue", f"judge error: {type(exc).__name__}", '
    "False, None, True\n"
)
# Verbatim upstream `_goal_judge_available` tail — the completion gate's
# reachability probe, whose two False paths logged nothing.
PINNED_JUDGE_AVAILABLE_SOURCE = '''\
def _goal_judge_available() -> bool:
    try:
        from agent.auxiliary_client import get_text_auxiliary_client
        client, model = get_text_auxiliary_client("goal_judge")
    except Exception:
        return False
    return client is not None and bool(model)
'''
JUDGE_ERROR = ("continue", "judge error: NotFoundError", False, None, True)
# The two anchor regions of upstream judge_goal, verbatim and in order, with
# the lines between them dropped — no patch keys on those, and the except
# handler is already pinned above. Identical in v2026.8.3 and v2026.8.13.
# Verbatim from hermes_cli/goals.py (v2026.9.11): the call moved into its
# own _call_goal_judge_llm() helper (returns a plain str, discards the
# response object), fetched and diffed directly against the pinned tag —
# the previous "resp = call_llm(...)" inline shape no longer exists there.
PINNED_JUDGE_CALL_SOURCE = '''\
    try:
        raw = _call_goal_judge_llm(call_llm, JUDGE_SYSTEM_PROMPT, prompt, timeout)
    except Exception as exc:
        logger.info("goal judge: API call failed (%s) — falling through to continue", exc)
        return "continue", f"judge error: {type(exc).__name__}", False, None, True

    verdict, reason, parse_failed, wait_directive = _parse_judge_response(raw)
'''

# Verbatim ``SocketModeClient.connect()`` from slack-sdk 3.43.0
# (slack_sdk/socket_mode/aiohttp/__init__.py) — the version hermes-agent's
# pyproject pins for the [slack] extra. Unlike every constant above, this
# re-anchors on a slack-sdk bump, not a hermes-agent bump; the converge-time
# read-back assert in venv_extras_and_users.yml is what catches that drift.
PINNED_SLACK_CONNECT_SOURCE = '''\
    async def connect(self):
        # This loop is used to ensure when a new session is created,
        # a new monitor and a new message receiver are also created.
        # If a new session is created but we failed to create the new
        # monitor or the new message, we should try it.
        while True:
            try:
                old_session: Optional[ClientWebSocketResponse] = (
                    None if self.current_session is None else self.current_session
                )

                # If the old session is broken (e.g. reset by peer), it might fail to close it.
                # We don't want to retry when this kind of cases happen.
                try:
                    # We should close old session before create a new one. Because when disconnect
                    # reason is `too_many_websockets`, we need to close the old one first to
                    # to decrease the number of connections.
                    self.auto_reconnect_enabled = False
                    if old_session is not None:
                        await old_session.close()
                        old_session_id = self.build_session_id(old_session)
                        self.logger.info(f"The old session ({old_session_id}) has been abandoned")
                except Exception as e:
                    self.logger.exception(f"Failed to close the old session : {e}")

                if self.wss_uri is None:
                    # If the underlying WSS URL does not exist,
                    # acquiring a new active WSS URL from the server-side first
                    self.wss_uri = await self.issue_new_wss_url()

                self.current_session = await self.aiohttp_client_session.ws_connect(
                    self.wss_uri,
                    autoping=False,
                    heartbeat=self.ping_interval,
                    proxy=self.proxy,
                    ssl=self.web_client.ssl if self.web_client.ssl is not None else True,
                )
                session_id: str = await self.session_id()
                self.auto_reconnect_enabled = self.default_auto_reconnect_enabled
                self.stale = False
                self.logger.info(f"A new session ({session_id}) has been established")

                # The first ping from the new connection
                if self.logger.level <= logging.DEBUG:
                    self.logger.debug(f"Sending a ping message with the newly established connection ({session_id})...")
                t = time.time()
                await self.current_session.ping(f"sdk-ping-pong:{t}".encode("utf-8"))

                if self.current_session_monitor is not None:
                    self.current_session_monitor.cancel()
                self.current_session_monitor = asyncio.ensure_future(self.monitor_current_session())
                if self.logger.level <= logging.DEBUG:
                    self.logger.debug(f"A new monitor_current_session() executor has been recreated for {session_id}")

                if self.message_receiver is not None:
                    self.message_receiver.cancel()
                self.message_receiver = asyncio.ensure_future(self.receive_messages())
                if self.logger.level <= logging.DEBUG:
                    self.logger.debug(f"A new receive_messages() executor has been recreated for {session_id}")
                break
            except Exception as e:
                self.logger.exception(f"Failed to connect (error: {e}); Retrying...")
                await asyncio.sleep(self.ping_interval)
'''


# Verbatim from gateway/kanban_watchers.py — the dispatcher's per-tick result
# loop and the stuck-streak counter the tick log patches rewrite.
PINNED_DISPATCH_TICK_SOURCE = (
    '''\
                    for slug, res in (results or []):
                        if res is not None and getattr(res, "spawned", None):
                            any_spawned = True
                            logger.info(
                                "kanban dispatcher [%s]: spawned=%d reclaimed=%d "
                                "crashed=%d timed_out=%d promoted=%d auto_blocked=%d",
                                slug,
                                len(res.spawned),
                                res.reclaimed,
                                len(res.crashed) if hasattr(res.crashed, "__len__") else 0,
                                len(res.timed_out) if hasattr(res.timed_out, "__len__") else 0,
                                res.promoted,
                                len(res.auto_blocked) if hasattr(res.auto_blocked, "__len__") else 0,
                            )
                    ready_pending = await _to_thread_process_service(_ready_nonempty)
                    bad_ticks = bad_ticks + 1 if ready_pending and not any_spawned else 0
'''
)
