"""Verbatim upstream Hermes source snippets: worker/judge/memory/slack half.

Split from _pinned_sources.py (over the repo's per-file token budget) — the
task/goal/cron-delivery snippets stay there. Same contract: every constant is
upstream source EXACTLY as shipped, never the expected post-patch form.
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
# Verbatim Hindsight failure-path source snippets at Hermes v2026.9.11,
# pinned by hermes_agent_version. These feed the role's actual replace tasks
# below and the behavioral regression test; expected patched source is always
# derived by _apply_runtime_patch, never hand-copied.
PINNED_HINDSIGHT_PREFETCH_SOURCE = (
    "        except Exception as e:\n"
    '            logger.debug("Hindsight recall failed: %s", e, exc_info=True)\n'
    '            return "", 0\n'
)
PINNED_HINDSIGHT_FAILURE_SOURCE = '''\
class HindsightMemoryProvider:
    def _run_hindsight_operation(self, operation):
        """Run an async client operation; for local_embedded, a stale-daemon
        connection failure recreates the client and retries once."""
        try:
            return self._run_sync(operation(self._get_client()))
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}".lower()
            if self._mode != "local_embedded" or not any(m in text for m in _RETRIABLE_CONNECTION_MARKERS):
                raise
            logger.info("Hindsight embedded daemon appears unreachable; recreating client and retrying once: %s", exc)
            self._client = None
            self._client = client = self._get_client()
            return self._run_sync(operation(client))
    # -- retain writer thread + server-side visibility -------------------------
    def _writer_loop(self):
        try:
            job()
        except Exception as exc:
            logger.warning("Hindsight retain failed: %s", exc, exc_info=True)
    def _is_retain_op_complete(self, bank_id: str, op_id: str) -> bool:
        try:
            resp = self._run_hindsight_operation(
                lambda client: client.operations.get_operation_status(bank_id=bank_id, operation_id=op_id)
            )
        except NotFoundException:
            return True
        except Exception as exc:
            logger.debug("Prefetch: operation status check failed for %s: %s", op_id, exc)
            return False
        return str(getattr(resp, "status", "") or "").lower() in {"completed", "failed"}
    def _wait_for_server_retain_ops(self, _expired, timeout):
        """Poll tracked async retain ops until complete or *_expired()* (deadline
        predicate). Ops still pending at the deadline are DROPPED: keeping them
        would let a permanently failing status endpoint burn the full timeout on
        EVERY later prefetch (a per-turn latency penalty via prefetch()'s bounded
        join). Trades a possibly-stale recall for liveness; WARNING once per prefetch."""
        if dropped:
            logger.warning("Prefetch: server retain visibility timed out after %.1fs; "
                           "dropping %d unresolved op(s) so later prefetches stay "
                           "bounded (recall may miss the just-completed turn)", timeout, dropped)
            return False
    def _recall(self, query: str) -> list:
        kwargs: dict = {"bank_id": self._bank_id, "query": query, "budget": self._budget, "max_tokens": self._recall_max_tokens}
        if self._recall_tags:
            kwargs.update(tags=self._recall_tags, tags_match=self._recall_tags_match)
        if self._recall_types:
            kwargs["types"] = self._recall_types
        resp = self._run_hindsight_operation(lambda client: client.arecall(**kwargs))
        return resp.results or []
    def _reflect(self, query: str) -> str | None:
        resp = self._run_hindsight_operation(
            lambda client: client.areflect(bank_id=self._bank_id, query=query, budget=self._budget)
        )
        return resp.text
    def _do_recall(self, query: str) -> tuple[str, int]:
        if self._recall_max_input_chars:
            query = query[:self._recall_max_input_chars]
        try:
            if self._prefetch_method == "reflect":
                return self._reflect(query) or "", 0
            results = self._recall(query)
            return "\\n".join(f"- {r.text}" for r in results if r.text), len(results)
        except Exception as e:
            logger.debug("Hindsight recall failed: %s", e, exc_info=True)
            return "", 0
    def _recall_disabled(self) -> bool:
        return False
    def _finish_prefetch(self, result: str, count: int) -> str:
        self._last_recall_returned, self._last_recall_count = bool(result), count if result else 0
        if not result:
            return ""
        header = self._recall_prompt_preamble or "# Hindsight Memory"
        return f"{header}\\n\\n{result}"
    def _join_prefetch(self, timeout: float, *, log: bool = False) -> None:
        if not (self._prefetch_thread and self._prefetch_thread.is_alive()):
            return
        self._prefetch_thread.join(timeout=timeout)
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._recall_sync:
            return self._finish_prefetch(*(("", 0) if self._recall_disabled() else self._do_recall(query)))
        self._join_prefetch(3.0, log=True)
        with self._prefetch_lock:
            result, count = self._prefetch_result, self._prefetch_count
            self._prefetch_result, self._prefetch_count = "", 0
        return self._finish_prefetch(result, count)
    def recall_status(self):
        return None
    def _retain_batch(self, item: dict, *, bank_id: str, document_id: str | None = None,
                      retain_async: bool | None = None):
        kwargs: Dict[str, Any] = {"bank_id": bank_id, "items": [item], "document_id": document_id, "retain_async": retain_async}
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return self._run_hindsight_operation(lambda client: client.aretain_batch(**kwargs))
    def _session_flush(self, job):
        try:
            job()
        except Exception as e:
            logger.warning("Hindsight flush-on-switch failed: %s", e, exc_info=True)
    def handle_tool_call(self, tool_name: str, args: dict, handler, failure: str):
        try:
            return json.dumps({"result": handler(self, args)})
        except Exception as e:
            logger.warning("%s failed: %s", tool_name, e, exc_info=True)
            return tool_error(f"{failure}: {e}")
'''
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
