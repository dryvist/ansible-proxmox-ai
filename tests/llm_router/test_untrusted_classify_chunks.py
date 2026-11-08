"""Chunked untrusted request classification stays bounded and fail-closed."""

from __future__ import annotations

import asyncio

from test_untrusted_classify import (
    MAX_CHARS_ENV,
    MAX_CHUNKS_ENV,
    Classifier,
    REPO_ROOT,
    _data,
    _key,
    _refused,
    _run,
    ns,
)


class MeasuringClassifier(Classifier):
    """Tracks peak concurrent calls while allowing them to overlap."""

    def __init__(self, delay=0.01):
        super().__init__(reply="public", delay=delay)
        self.active = 0
        self.max_active = 0

    async def __call__(self, url, key, body, timeout):
        self.calls.append({"url": url, "key": key, "body": body, "timeout": timeout})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return {"choices": [{"message": {"content": self.reply}}]}
        finally:
            self.active -= 1


def test_role_default_chunk_cap_is_rendered_to_the_callback():
    defaults = (REPO_ROOT / "roles/llm_router/defaults/main/56d-untrusted-classify.yml").read_text()
    environment = (REPO_ROOT / "roles/llm_router/templates/litellm.env.j2").read_text()
    assert "llm_router_untrusted_classify_max_chunks: 8" in defaults
    assert "LLM_ROUTER_UNTRUSTED_CLASSIFY_MAX_CHUNKS={{ llm_router_untrusted_classify_max_chunks }}" in environment


def test_three_max_char_chunks_are_all_classified_and_public_text_passes(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    data = _data("abcdefghijkl")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 3
    chunks = [
        call["body"]["messages"][1]["content"][len("<text>\n") : -len("\n</text>")]
        for call in classifier.calls
    ]
    assert sorted(chunks) == ["abcd", "efgh", "ijkl"]


def test_sensitive_chunk_refuses_request(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "8")

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        label = "sensitive" if "private" in content else "public"
        return {"choices": [{"message": {"content": label}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), _data("ordinaryprivate"))


def test_chunk_count_over_cap_refuses_as_oversized_without_a_call(ns, monkeypatch, caplog):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(MAX_CHUNKS_ENV, "2")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), _data("abcdefghijkl"))
    assert classifier.calls == []
    assert "classifier oversized" in caplog.text


def test_classifier_error_on_a_chunk_refuses_request(ns, monkeypatch, caplog):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        if "fail" in content:
            raise RuntimeError("request content must not be logged")
        return {"choices": [{"message": {"content": "public"}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), _data("goodfailgood"))
    assert "classifier error" in caplog.text
    assert "request content must not be logged" not in caplog.text


def test_chunk_classifier_concurrency_is_bounded_to_four(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "1")
    classifier = MeasuringClassifier()
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data("abcdefgh")
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 8
    assert classifier.max_active == 4


def test_first_refusal_cancels_remaining_chunk_classification(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    calls = []

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        chunk = content[len("<text>\n") : -len("\n</text>")]
        calls.append(chunk)
        if chunk == "nope":
            return {"choices": [{"message": {"content": "sensitive"}}]}
        await asyncio.sleep(0.1)
        return {"choices": [{"message": {"content": "public"}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), _data("nope" + "good" * 7))
    assert calls[0] == "nope"
    assert len(calls) <= 4
