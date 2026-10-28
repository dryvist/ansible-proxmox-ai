"""Verbatim upstream Hermes source snippets: worker-reap and hindsight half.

Split from _pinned_sources.py (over the repo's per-file token budget) — the
task/goal/cron-delivery snippets stay there, and the memory-sync, goal-judge,
Slack connect and dispatch-tick snippets live in _pinned_sources_hooks.py.
Same contract: every constant is upstream source EXACTLY as shipped, never the
expected post-patch form.
"""

from __future__ import annotations

# Verbatim upstream Hermes v0.22.5 hermes_cli/kanban_db_dispatch.py lines; the
# text between the markers is never edited. Both reapers escalate through the
# shared _sigkill helper. The timeout excerpt is the kill block of
# enforce_max_runtime, wrapped in a def/for header only so it parses alone.
PINNED_WORKER_REAP_SOURCE = r'''
def _sigkill(kill, pid: int) -> bool:
    """Best-effort SIGKILL; True when the signal was delivered."""
    try:
        # signal.SIGKILL doesn't exist on Windows; SIGTERM maps to TerminateProcess.
        kill(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
        return True
    except (ProcessLookupError, OSError):
        return False


def _enforce_timeout_kill(rows, started_at, signal_fn=None):
    for pid in rows:
        killed = False
        kill = _kill_fn(signal_fn)
        if kill is not None and not (_kb._pid_alive(pid) and _pid_recycled(pid, started_at)):
            with contextlib.suppress(ProcessLookupError, OSError):
                kill(pid, signal.SIGTERM)
            # Short polling wait — no time.sleep on the write txn.
            _poll_worker_exit(pid, started_at)
            if _worker_alive(pid, started_at):
                killed = _sigkill(kill, pid)
        return killed
'''
# Verbatim upstream v0.22.5 _sigkill and _terminate_reclaimed_worker (the
# stale-reclaim path), copied from the same file.
PINNED_STALE_RECLAIM_TERMINATE_SOURCE = r'''
def _sigkill(kill, pid: int) -> bool:
    """Best-effort SIGKILL; True when the signal was delivered."""
    try:
        # signal.SIGKILL doesn't exist on Windows; SIGTERM maps to TerminateProcess.
        kill(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
        return True
    except (ProcessLookupError, OSError):
        return False


def _terminate_reclaimed_worker(
    pid: Optional[int],
    claim_lock: Optional[str],
    *,
    signal_fn=None,
    started_at=None,
) -> dict[str, Any]:
    """Best-effort host-local worker termination for reclaim paths. ``started_at`` is the spawn-time
    fingerprint: when the live process no longer matches it, the PID was recycled and nothing is
    signalled — the worker is gone, which is what the reclaim wanted (``terminated`` = True). An
    UNVERIFIED spawn (fingerprint capture failed) that is still live is never signalled either, but
    it is reported as surviving (``signal_refused``) so the reclaim holds the claim instead of
    spawning a duplicate beside it."""
    info: dict[str, Any] = {
        "prev_pid": int(pid) if pid else None,
        "host_local": False,
        "termination_attempted": False,
        "terminated": False,
        "sigkill": False,
    }
    if not pid or pid <= 0 or not claim_lock:
        return info
    if not str(claim_lock).startswith(_kb._host_prefix()):
        return info
    info["host_local"] = True

    kill = _kill_fn(signal_fn)
    if kill is None:
        return info
    if started_at == UNVERIFIED_WORKER_FINGERPRINT:
        # Never signal by bare number: a dead PID is "gone" (reclaim proceeds), a live one is held.
        info["signal_refused"] = True
        info["terminated"] = not _kb._pid_alive(pid)
        return info
    if _kb._pid_alive(pid) and _pid_recycled(pid, started_at):
        info["terminated"] = True
        info["pid_recycled"] = True
        return info

    info["termination_attempted"] = True
    try:
        kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        # Already gone = successful termination. Leaving terminated=False would
        # make the reclaim guard misread a dead worker as alive and defer forever.
        info["terminated"] = True
        return info
    except OSError:
        return info

    if _poll_worker_exit(pid, started_at):
        info["terminated"] = True
        return info
    if _worker_alive(pid, started_at):
        if not _sigkill(kill, pid):
            return info
        info["sigkill"] = True
    info["terminated"] = not _worker_alive(pid, started_at)
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
