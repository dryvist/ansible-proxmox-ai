"""untrusted_classify refuses an untrusted key unless a local classifier labels its text public.

Loaded from source with litellm, fastapi and httpx stubbed for each test, like the other
callback tests. The classify POST is replaced, so no network call is made.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "roles/llm_router/files/callbacks/untrusted_classify.py"
MASTER = "sk-master-under-test"
MODEL = "classify-under-test"
URL = "http://classify.example.test/v1/chat/completions"


@pytest.fixture
def ns(monkeypatch):
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    litellm = types.ModuleType("litellm")
    litellm.__path__ = []
    integrations = types.ModuleType("litellm.integrations")
    integrations.__path__ = []
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code, self.detail = status_code, detail

    fastapi.HTTPException = HTTPException
    # Only imported by the module; every test replaces _post, so no client is built.
    httpx = types.ModuleType("httpx")
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", custom_logger)
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_MODEL", MODEL)
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_URL", URL)
    monkeypatch.setenv("LLM_ROUTER_MASTER_KEY", MASTER)
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_MAX_CHARS", "16000")
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_TIMEOUT_SECONDS", "10")
    out: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), out)  # noqa: S102 - our own file
    return out


class Classifier:
    """Stands in for the classify POST and records every call."""

    def __init__(self, reply=None, exc=None, delay=0.0):
        self.reply, self.exc, self.delay = reply, exc, delay
        self.calls = []

    async def __call__(self, url, key, body, timeout):
        self.calls.append({"url": url, "key": key, "body": body, "timeout": timeout})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return {"choices": [{"message": {"content": self.reply}}]}


def _key(tier):
    metadata = {} if tier is None else {"trust_tier": tier}
    return types.SimpleNamespace(key_alias="caller-under-test", metadata=metadata)


def _data(text="hello there"):
    return {"model": "caller-under-test", "messages": [{"role": "user", "content": text}]}


def _run(ns, key, data):
    return asyncio.run(ns["proxy_handler_instance"].async_pre_call_hook(key, None, data, "acompletion"))


def _refused(ns, key, data):
    with pytest.raises(ns["HTTPException"]) as exc:
        _run(ns, key, data)
    assert exc.value.status_code == 403
    return exc.value


def test_source_parses():
    ast.parse(SOURCE.read_text())


@pytest.mark.parametrize("tier", ["trusted", None])
def test_keys_not_marked_untrusted_are_never_classified(ns, monkeypatch, tier):
    classifier = Classifier(reply="sensitive")
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data("the private plan")
    assert _run(ns, _key(tier), data) is data
    assert classifier.calls == []


def test_untrusted_key_with_public_label_passes(ns, monkeypatch):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data()
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 1


def test_untrusted_key_with_sensitive_label_is_refused(ns, monkeypatch):
    monkeypatch.setitem(ns, "_post", Classifier(reply="sensitive"))
    _refused(ns, _key("untrusted"), _data())


def test_classifier_error_is_refused(ns, monkeypatch):
    monkeypatch.setitem(ns, "_post", Classifier(exc=RuntimeError("backend down")))
    _refused(ns, _key("untrusted"), _data())


def test_classifier_timeout_is_refused(ns, monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setitem(ns, "_post", Classifier(reply="public", delay=1.0))
    _refused(ns, _key("untrusted"), _data())


@pytest.mark.parametrize("reply", ["", "public.", "I think it is public", "PUBLIC please", None, 7])
def test_unparsable_label_is_refused(ns, monkeypatch, reply):
    monkeypatch.setitem(ns, "_post", Classifier(reply=reply))
    _refused(ns, _key("untrusted"), _data())


@pytest.mark.parametrize("reply", [" Public\n", "PUBLIC"])
def test_public_label_ignores_case_and_surrounding_space(ns, monkeypatch, reply):
    monkeypatch.setitem(ns, "_post", Classifier(reply=reply))
    data = _data()
    assert _run(ns, _key("untrusted"), data) is data


def test_missing_settings_refuse_without_a_call(ns, monkeypatch):
    monkeypatch.delenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_MODEL")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), _data())
    assert classifier.calls == []


def test_request_without_text_is_refused_without_a_call(ns, monkeypatch):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), {"model": "caller-under-test", "messages": []})
    assert classifier.calls == []


def test_responses_input_text_is_classified(ns, monkeypatch):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    data = {
        "model": "caller-under-test",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "from the input field"}]}],
    }
    _run(ns, _key("untrusted"), data)
    assert "from the input field" in classifier.calls[0]["body"]["messages"][1]["content"]


def test_classifier_pass_through_state_is_classified(ns, monkeypatch):
    classifier = Classifier(reply="sensitive")
    monkeypatch.setitem(ns, "_post", classifier)
    data = {"state": "from the state field", "questions": {"route": {"type": "choice"}}}
    _refused(ns, _key("untrusted"), data)
    assert "from the state field" in classifier.calls[0]["body"]["messages"][1]["content"]


def test_classify_call_is_local_capped_and_uses_the_master_key(ns, monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_MAX_CHARS", "5")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _run(ns, _key("untrusted"), _data("abcdefghij"))
    call = classifier.calls[0]
    assert call["url"] == URL
    assert call["key"] == MASTER
    body = call["body"]
    assert body["model"] == MODEL
    assert body["temperature"] == 0
    assert body["max_tokens"] == 4
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    user = body["messages"][1]["content"]
    assert "abcde" in user
    assert "fghij" not in user
