"""Tests for roles/llamaindex/files/index_agent_sessions.py and index_docs.py.

No network: urlopen, time.sleep, Qdrant and the llama-index embedder are faked.
Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/llamaindex -p 'test_index_scripts.py' -v
"""

import contextlib
import email.message
import importlib.util
import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "roles" / "llamaindex" / "files"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, FILES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_script("index_agent_sessions")
docs = load_script("index_docs")

TOKEN = "tok-must-not-appear-in-output"
SEARCH_MARKER = "search index="


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    headers = email.message.Message()
    return urllib.error.HTTPError("https://splunk.example:8089/", code, "error", headers, io.BytesIO(body))


def response(*lines: str) -> io.BytesIO:
    return io.BytesIO("".join(line + "\n" for line in lines).encode())


def row(time_value: str) -> str:
    return json.dumps({"result": {"_time": time_value, "_agent_session": "s1"}})


class FakeUrlopen:
    """Raises or returns the next scripted outcome on each call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, request, timeout=None):
        del request, timeout  # the script's request and timeout are not inspected
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ExportTestCase(unittest.TestCase):
    def setUp(self):
        sleep = mock.patch.object(agent.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.watermarks = self.dir / "watermarks.json"
        base = self.dir / "config.yaml"
        base.write_text("embeddings: {}\nqdrant: {}\n", encoding="utf-8")
        agent_config = self.dir / "agent.yaml"
        agent_config.write_text(
            "splunk:\n"
            "  url: https://splunk.example:8089\n"
            f"  token: {TOKEN}\n"
            "index:\n"
            f"  watermark_file: {self.watermarks}\n"
            "  collection: agent_sessions\n"
            "  harnesses:\n"
            "    - name: claude\n"
            "      index: claude\n"
            "      role_field: message.role\n"
            "      content_field: message.content\n"
            "      session_field: session_id\n"
            "      cwd_field: cwd\n",
            encoding="utf-8",
        )
        self.argv = ["index_agent_sessions", "--config", str(base), "--agent-config", str(agent_config)]

    def run_main(self, outcomes):
        opener = FakeUrlopen(outcomes)
        stderr = io.StringIO()
        with (
            mock.patch.object(agent, "make_clients", return_value=(None, None)),
            mock.patch.dict(sys.modules, {"qdrant_client": mock.MagicMock()}),
            mock.patch.object(agent.urllib.request, "urlopen", opener),
            mock.patch.object(sys, "argv", self.argv),
            contextlib.redirect_stderr(stderr),
        ):
            code = agent.main()
        return code, stderr.getvalue(), opener

    def export(self, outcomes):
        opener = FakeUrlopen(outcomes)
        with mock.patch.object(agent.urllib.request, "urlopen", opener):
            rows = list(agent.export_rows("https://splunk.example:8089", TOKEN, "search index=claude"))
        return rows, opener


class ExportMainTests(ExportTestCase):
    def test_401_logs_reason_retries_three_times_and_exits_1(self):
        code, stderr, opener = self.run_main([http_error(401, b'{"messages":[{"text":"Unauthorized"}]}')] * 3)
        self.assertEqual(code, 1)
        self.assertEqual(opener.calls, 3)
        self.assertEqual(self.sleep.call_args_list, [mock.call(5), mock.call(20)])
        self.assertIn("agent session export failed for claude: HTTPError status=401", stderr)
        self.assertIn("Unauthorized", stderr)
        self.assertNotIn(TOKEN, stderr)
        self.assertNotIn(SEARCH_MARKER, stderr)
        self.assertFalse(self.watermarks.exists())

    def test_429_logs_reason_and_retries(self):
        code, stderr, opener = self.run_main([http_error(429, b"rate limited")] * 3)
        self.assertEqual(code, 1)
        self.assertEqual(opener.calls, 3)
        self.assertIn("HTTPError status=429 body='rate limited'", stderr)

    def test_404_is_not_retried(self):
        code, stderr, opener = self.run_main([http_error(404, b"not found")])
        self.assertEqual(code, 1)
        self.assertEqual(opener.calls, 1)
        self.sleep.assert_not_called()
        self.assertIn("HTTPError status=404 body='not found'", stderr)

    def test_url_error_logs_reason_and_retries(self):
        code, stderr, opener = self.run_main([urllib.error.URLError("connection refused")] * 3)
        self.assertEqual(code, 1)
        self.assertEqual(opener.calls, 3)
        self.assertIn("agent session export failed for claude: URLError reason=connection refused", stderr)


class ExportRowsTests(ExportTestCase):
    def test_two_transient_failures_then_success_yields_each_row_once(self):
        rows, opener = self.export(
            [http_error(500), http_error(502), response(row("1"), row("2"))],
        )
        self.assertEqual(rows, [{"_time": "1", "_agent_session": "s1"}, {"_time": "2", "_agent_session": "s1"}])
        self.assertEqual(opener.calls, 3)
        self.assertEqual(self.sleep.call_args_list, [mock.call(5), mock.call(20)])

    def test_socket_timeout_before_first_row_is_retried(self):
        rows, opener = self.export([socket.timeout("timed out"), response(row("1"))])
        self.assertEqual(rows, [{"_time": "1", "_agent_session": "s1"}])
        self.assertEqual(opener.calls, 2)
        self.sleep.assert_called_once_with(5)

    def test_malformed_json_is_not_retried(self):
        opener = FakeUrlopen([response("not json")])
        with (
            mock.patch.object(agent.urllib.request, "urlopen", opener),
            self.assertRaises(json.JSONDecodeError),
        ):
            list(agent.export_rows("https://splunk.example:8089", TOKEN, "search index=claude"))
        self.assertEqual(opener.calls, 1)
        self.sleep.assert_not_called()


def guaranteed_backoff_seconds(max_retries: int) -> float:
    """Lower bound on the sleep for one embedding batch that keeps receiving 429.

    Mirrors the pinned library sources (llama-index-embeddings-openai 0.7.0,
    openai 2.54.0):
    - openai/_base_client.py: retry k sleeps min(0.5 * 2**k, 8.0) * (1 - 0.25 * random()),
      for k = 0 .. max_retries-1, on each attempt.
    - llama_index/embeddings/openai/utils.py: each batch is retried max_retries times by
      tenacity, waiting wait_exponential(multiplier=1, min=4, max=10) between tries.
      Each try is a full openai call, so the client sleeps repeat max_retries times.
    If either library changes, revisit this function and the max_retries value.
    """
    client_sleep = sum(min(0.5 * 2**k, 8.0) for k in range(max_retries)) * 0.75
    wrapper_sleep = sum(min(max(2 ** (a - 1), 4), 10) for a in range(1, max_retries))
    return max_retries * client_sleep + wrapper_sleep


class EmbedderBudgetTests(unittest.TestCase):
    def embedder_kwargs(self):
        fake = mock.MagicMock()
        modules = {
            "qdrant_client": fake.qdrant_client,
            "llama_index": fake,
            "llama_index.core": fake.core,
            "llama_index.embeddings": fake.embeddings,
            "llama_index.embeddings.openai": fake.embeddings.openai,
            "llama_index.vector_stores": fake.vector_stores,
            "llama_index.vector_stores.qdrant": fake.vector_stores.qdrant,
        }
        cfg = {
            "embeddings": {"model": "example-embedding", "base_url": "https://llm.example/v1", "api_key": "k"},
            "qdrant": {"host": "qdrant.example", "port": 6333},
            "index": {"collection": "homelab_docs"},
        }
        with mock.patch.dict(sys.modules, modules), contextlib.redirect_stderr(io.StringIO()):
            docs.build_index(cfg, [])
        return fake.embeddings.openai.OpenAIEmbedding.call_args.kwargs

    def test_arithmetic_matches_known_values(self):
        self.assertAlmostEqual(guaranteed_backoff_seconds(12), 733.5)
        self.assertAlmostEqual(guaranteed_backoff_seconds(8), 287.0)

    def test_embedder_has_ten_minute_budget_and_keeps_batch_size(self):
        kwargs = self.embedder_kwargs()
        self.assertEqual(kwargs["embed_batch_size"], 8)
        self.assertGreaterEqual(guaranteed_backoff_seconds(kwargs["max_retries"]), 600)


if __name__ == "__main__":
    unittest.main()
