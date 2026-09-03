"""Verbatim upstream Hermes source snippets the patch tests run against.

Split out of conftest.py: a version bump only ever edits these strings, and an
agent re-anchoring a patch should read the snippets without the fixtures, and
vice versa. Every constant is upstream source EXACTLY as shipped — never the
expected post-patch form, which is what let seven dead patches stay green.
"""

from __future__ import annotations

PINNED_CREATE_TASK_SOURCE = '''\
def create_task(conn, *, idempotency_key=None, goal_mode=False, goal_max_turns=None):
    if idempotency_key:
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if row:
            return row["id"]
    raise RuntimeError("insert path")
'''
PINNED_GOAL_COMPLETION_SOURCE = "        verdict, reason, _, _, _ = judge_goal(\n"
# Verbatim upstream lines at the pinned release, indentation included. A copy
# that drifts from upstream silently stops patching and the test goes green on
# nothing — these sat at v2026.7.7.2 while the role installed a much later
# release, which is how seven patches came to match zero times with every test
# passing. Re-verify with scripts/verify-pinned-patches.py on a version bump.
PINNED_TC_BOOST_CAP_SOURCE = (
    "                                _tc_boost_cap = max("
    "32768, _tc_requested_cap or 0)\n"
)
PINNED_BOOST_CAP_SOURCE = (
    "            _boost_cap = max(32768, _requested_cap or 0)\n"
)
PINNED_COMPRESSOR_SCAN_SOURCE = (
    "        for idx in range(end - 1, start - 1, -1):\n"
)
# Upstream now supplies both protocol-violation behaviors itself, so these are
# no longer patch INPUTS — they are the upstream text the retained assertions
# pin. Both role patches were retired for matching zero times.
PINNED_PROTOCOL_VIOLATION_SOURCE = (
    '                    "without a terminal kanban call counts as failed no "\n'
)
PINNED_PROTOCOL_RETRY_SOURCE = (
    "                failure_limit=1 if is_systemic else None,\n"
)
# Both delivery call sites: the success path (inside the side-effect fence)
# and the outer exception handler, which delivers its own failure summary.
PINNED_CRON_DELIVERY_SOURCE = (
    '''\
                if success:
                    deliver_content = final_response
                        delivery_error = _deliver_result(
                            job,
                            deliver_content,
                            adapters=adapters,
                            loop=loop,
                        )
                    delivery_error = _deliver_result(
                        job,
                        # Composed exactly like the normal failure delivery above.
                        # mark_job_run below records THIS run in failure_streak
                        _summarize_cron_failure_for_delivery(job, _err_text)
                        + _failure_streak_nudge(job),
                        adapters=adapters,
                        loop=loop,
                    )
'''
    # Upstream's line; the memory patch deliberately leaves it in place.
    # Reversed upstream — cron now builds the built-in memory store itself.
    "            skip_memory=False,\n"
)
# Verbatim from cron/scheduler.py — the single run_conversation submit that
# opt-in cron goal mode wraps.
PINNED_CRON_SUBMIT_SOURCE = (
    "        _cron_context = contextvars.copy_context()\n"
    "        _cron_future = _cron_pool.submit(_cron_context.run,"
    " agent.run_conversation, prompt)\n"
    "        _inactivity_timeout = False\n"
)
# Exact upstream v2026.8.3 monitor targeted by the aggregate-deadline patch.
# Keep the complete control flow, not only replacement anchors: the transformed
# fixture is compiled and executed with fake time/futures in the behavioral
# tests, so a syntactically valid but incorrectly composed patch cannot pass.
PINNED_CRON_TIMEOUT_SOURCE = '''\
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        _POLL_INTERVAL = 5.0
        _job_schedule = job.get("schedule")
        _is_oneshot = (
            isinstance(_job_schedule, dict) and _job_schedule.get("kind") == "once"
        )
        _run_claim = job.get("run_claim")
        _run_claim_owner = (
            str(_run_claim.get("by") or "") if isinstance(_run_claim, dict) else ""
        )
        _last_claim_heartbeat = time.monotonic()

        def _heartbeat_run_claim_if_due():
            nonlocal _last_claim_heartbeat
            if not _is_oneshot or not _run_claim_owner:
                return
            _mono = time.monotonic()
            if _mono - _last_claim_heartbeat < _RUN_CLAIM_HEARTBEAT_SECONDS:
                return
            _last_claim_heartbeat = _mono
            try:
                heartbeat_run_claim(job_id, expected_owner=_run_claim_owner)
            except Exception:
                logger.debug(
                    "Job '%s': run_claim heartbeat failed", job_name, exc_info=True
                )

        _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _cron_context = contextvars.copy_context()
        _cron_future = _cron_pool.submit(_cron_context.run, agent.run_conversation, prompt)
        _inactivity_timeout = False
        try:
            if _cron_inactivity_limit is None:
                if _is_oneshot:
                    result = None
                    while True:
                        done, _ = concurrent.futures.wait(
                            {_cron_future}, timeout=_POLL_INTERVAL,
                        )
                        if done:
                            result = _cron_future.result()
                            break
                        _heartbeat_run_claim_if_due()
                else:
                    result = _cron_future.result()
            else:
                result = None
                while True:
                    done, _ = concurrent.futures.wait(
                        {_cron_future}, timeout=_POLL_INTERVAL,
                    )
                    if done:
                        result = _cron_future.result()
                        break
                    _heartbeat_run_claim_if_due()
                    # Agent still running — check inactivity.
                    _idle_secs = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            _act = agent.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    if _idle_secs >= _cron_inactivity_limit:
                        _inactivity_timeout = True
                        break
        except Exception:
            _cron_pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            _cron_pool.shutdown(wait=False, cancel_futures=True)

        if _inactivity_timeout:
            _activity = {}
            if hasattr(agent, "get_activity_summary"):
                try:
                    _activity = agent.get_activity_summary()
                except Exception:
                    pass
            _last_desc = _activity.get("last_activity_desc", "unknown")
            _secs_ago = _activity.get("seconds_since_activity", 0)
            _cur_tool = _activity.get("current_tool")
            _iter_n = _activity.get("api_call_count", 0)
            _iter_max = _activity.get("max_iterations", 0)

            logger.error(
                "Job '%s' idle for %.0fs (inactivity limit %.0fs) "
                "| last_activity=%s | iteration=%s/%s | tool=%s",
                job_name, _secs_ago, _cron_inactivity_limit,
                _last_desc, _iter_n, _iter_max,
                _cur_tool or "none",
            )
            request_hard_interrupt(agent, "Cron job timed out (inactivity)")
            raise TimeoutError(
                f"Cron job '{job_name}' idle for "
                f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
                f"— last activity: {_last_desc}"
            )
'''
PINNED_WORKER_REAP_SOURCE = '''\
def _reap(pid, signal_fn=None):
    killed = False
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is not None:
        try:
            kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        for _ in range(10):
            if not _pid_alive(pid):
                break
            time.sleep(0.5)
        if _pid_alive(pid):
            try:
                _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                kill(pid, _sigkill)
                killed = True
            except (ProcessLookupError, OSError):
                pass
    return killed
'''
PINNED_STALE_RECLAIM_TERMINATE_SOURCE = '''\
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
        try:
            _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            kill(int(pid), _sigkill)
            info["sigkill"] = True
        except (ProcessLookupError, OSError):
            return info

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
        if interrupted:
            return
        if not (self._memory_manager and final_response and original_user_message):
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
            self._memory_manager.queue_prefetch_all(
                user_text,
                session_id=self.session_id or "",
            )
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
PINNED_JUDGE_CALL_SOURCE = '''\
    try:
        resp = call_llm(
            task="goal_judge",
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=_goal_judge_max_tokens(),
            timeout=timeout,
        )
    except Exception:
        raw = ""

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
