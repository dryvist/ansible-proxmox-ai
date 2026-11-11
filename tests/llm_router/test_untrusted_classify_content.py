"""Untrusted request content: scalars are classified as text, unsupported content logs only its category."""

from __future__ import annotations

from test_untrusted_classify import Classifier, _data, _key, _refused, _run, ns  # noqa: F401


def test_unsupported_block_logs_its_category(ns, monkeypatch, caplog):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data()
    data["messages"].append({"role": "user", "content": [{"type": "image_url", "data": "opaque"}]})
    _refused(ns, _key("untrusted"), data)
    assert "classifier unsupported_block" in caplog.text
    assert "opaque" not in caplog.text
    assert classifier.calls == []


def test_unsupported_value_logs_its_category(ns, monkeypatch, caplog):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data()
    data["messages"].append({"role": "user", "content": {b"opaque"}})
    _refused(ns, _key("untrusted"), data)
    assert "classifier unsupported_value" in caplog.text
    assert "opaque" not in caplog.text
    assert classifier.calls == []


def test_numbers_and_booleans_in_messages_are_classified_as_text(ns, monkeypatch):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    data = _data()
    data["messages"].append({"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}},
    ], "cached": True, "score": 0.5})
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 1
    text = classifier.calls[0]["body"]["messages"][1]["content"]
    assert "\n0\n" in text and "\nTrue" in text and "\n0.5" in text
