"""Chunked untrusted request classification stays bounded and fail-closed."""

from __future__ import annotations

import asyncio

import pytest

from test_untrusted_classify import (
    MAX_CHARS_ENV,
    MAX_CHUNKS_ENV,
    OVERLAP_CHARS_ENV,
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


def test_role_default_chunk_caps_and_timeout_are_rendered_to_the_callback():
    defaults = (REPO_ROOT / "roles/llm_router/defaults/main/56d-untrusted-classify.yml").read_text()
    environment = (REPO_ROOT / "roles/llm_router/templates/litellm.env.j2").read_text()
    assert "llm_router_untrusted_classify_max_chunks: 8" in defaults
    assert "LLM_ROUTER_UNTRUSTED_CLASSIFY_MAX_CHUNKS={{ llm_router_untrusted_classify_max_chunks }}" in environment
    assert "llm_router_untrusted_classify_overlap_chars: 512" in defaults
    assert (
        "LLM_ROUTER_UNTRUSTED_CLASSIFY_OVERLAP_CHARS="
        "{{ llm_router_untrusted_classify_overlap_chars }}" in environment
    )
    assert "llm_router_untrusted_classify_timeout_seconds: 30" in defaults
    assert (
        "LLM_ROUTER_UNTRUSTED_CLASSIFY_TIMEOUT_SECONDS="
        "{{ llm_router_untrusted_classify_timeout_seconds }}" in environment
    )


def test_three_max_char_chunks_are_all_classified_and_public_text_passes(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")
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
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        label = "sensitive" if "private" in content else "public"
        return {"choices": [{"message": {"content": label}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), _data("ordinaryprivate"))


def test_chunk_count_over_cap_refuses_as_oversized_without_a_call(ns, monkeypatch, caplog):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")
    monkeypatch.setenv(MAX_CHUNKS_ENV, "2")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), _data("abcdefghijkl"))
    assert classifier.calls == []
    assert "classifier oversized" in caplog.text


def test_classifier_error_on_a_chunk_refuses_request(ns, monkeypatch, caplog):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        if "fail" in content:
            raise RuntimeError("request content must not be logged")
        return {"choices": [{"message": {"content": "public"}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), _data("goodfailgood"))
    assert "classifier error" in caplog.text
    assert "request content must not be logged" not in caplog.text


def test_chunk_timeout_refuses_request(ns, monkeypatch, caplog):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_TIMEOUT_SECONDS", "0.01")
    classifier = Classifier(reply="public", delay=1.0)
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), _data("abcdefgh"))
    assert "classifier timeout" in caplog.text
    assert "abcdefgh" not in caplog.text


def test_chunk_classifier_concurrency_is_bounded_to_four(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "1")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")
    classifier = MeasuringClassifier()
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data("abcdefgh")
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 8
    assert classifier.max_active == 4


def test_first_refusal_cancels_remaining_chunk_classification(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "0")
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


def test_secret_spanning_a_chunk_boundary_is_seen_whole_by_one_chunk(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "12")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "8")
    secret = "S3CRET"  # shorter than the overlap
    # The secret straddles the first chunk boundary: the first chunk reads its
    # first three characters, the chunk that starts at the boundary reads only
    # its last three. Only the overlap lets one chunk read it whole.
    text = "x" * 9 + secret + "y" * 12
    seen = []

    async def classify(url, key, body, timeout):
        content = body["messages"][1]["content"]
        seen.append(content[len("<text>\n") : -len("\n</text>")])
        label = "sensitive" if secret in content else "public"
        return {"choices": [{"message": {"content": label}}]}

    monkeypatch.setitem(ns, "_post", classify)
    # The classifier labels a chunk sensitive only when it carries the whole
    # secret, so the refusal proves one chunk's input was the secret in full.
    _refused(ns, _key("untrusted"), _data(text))
    assert text[4:16] in seen


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (9, ["abcdefghi"]),
        (10, ["abcdefghij"]),
        (11, ["abcdefghij", "ghijk"]),
    ],
)
def test_chunk_counts_and_coverage_around_one_chunk(ns, monkeypatch, length, expected):
    monkeypatch.setenv(MAX_CHARS_ENV, "10")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "4")
    text = "abcdefghijklmnopqrstuvwxyz"[:length]
    data = _data(text)
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    assert _run(ns, _key("untrusted"), data) is data
    chunks = [
        call["body"]["messages"][1]["content"][len("<text>\n") : -len("\n</text>")]
        for call in classifier.calls
    ]
    assert sorted(chunks) == sorted(expected)
    # Whole-text coverage: every character is carried by at least one chunk.
    for character in text:
        assert any(character in chunk for chunk in chunks)


def test_chunk_count_and_tail_at_the_role_default_size(ns, monkeypatch):
    # The fixture env carries the role defaults: 16000-char chunks with a
    # 512-char overlap. One character over a chunk boundary costs exactly one
    # more chunk, and its new content is the overlap plus that character.
    text = "a" * 16001
    data = _data(text)
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    assert _run(ns, _key("untrusted"), data) is data
    chunks = [
        call["body"]["messages"][1]["content"][len("<text>\n") : -len("\n</text>")]
        for call in classifier.calls
    ]
    assert sorted(len(chunk) for chunk in chunks) == [513, 16000]
    assert all(set(chunk) == {"a"} for chunk in chunks)


def test_refusal_cancels_and_collects_in_flight_chunk_classifications(ns, monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "4")
    monkeypatch.setenv(OVERLAP_CHARS_ENV, "2")
    cancelled = 0

    async def classify(url, key, body, timeout):
        nonlocal cancelled
        content = body["messages"][1]["content"]
        delay = 0.05 if "bad" in content else 5.0
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            cancelled += 1
            raise
        label = "sensitive" if "bad" in content else "public"
        return {"choices": [{"message": {"content": label}}]}

    monkeypatch.setitem(ns, "_post", classify)
    data = _data("bad" + "good" * 3)

    async def scenario():
        try:
            await ns["proxy_handler_instance"].async_pre_call_hook(
                _key("untrusted"), None, data, "acompletion"
            )
            return 0
        except ns["HTTPException"]:
            # Chunk 0 ("badg") is labelled sensitive at once while the other
            # in-flight chunk classifications are still sleeping. The refusal
            # must cancel and collect them before it returns, so by the time
            # the exception is observed only this scenario's own task may
            # still be pending. Without the cleanup, the chunk tasks linger
            # here and are only cancelled at event-loop shutdown.
            return len(asyncio.all_tasks())

    assert asyncio.run(scenario()) == 1
    assert cancelled == 3
