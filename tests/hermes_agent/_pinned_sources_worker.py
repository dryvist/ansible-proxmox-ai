"""Verbatim upstream Hermes source snippets: worker-reap and hindsight half.

Split from _pinned_sources.py (over the repo's per-file token budget) — the
task/goal/cron-delivery snippets stay there, and the memory-sync, goal-judge,
Slack connect and dispatch-tick snippets live in _pinned_sources_hooks.py.
Same contract: every constant is upstream source EXACTLY as shipped, never the
expected post-patch form.
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
