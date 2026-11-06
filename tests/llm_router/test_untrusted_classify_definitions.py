"""Tool definitions are classified as JSON, including schema keys and scalar values."""

import json

import pytest

from test_untrusted_classify import Classifier, _data, _key, _refused, _run, ns


@pytest.fixture
def definition():
    return {
        "name": "lookup",
        "description": "Find a public record",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text: café"},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "score": {"type": "number", "minimum": 0.5},
                "enabled": {"type": "boolean", "default": False},
                "tags": {"type": "array", "items": {"type": ["string", "null"]}},
                "role": {"type": "string", "enum": ["reader"]},
                "type": {"type": "string", "const": "record"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


@pytest.fixture(params=["chat", "responses", "legacy"])
def data(request, definition):
    data = _data()
    if request.param == "chat":
        data["tools"] = [{"type": "function", "function": definition}]
    elif request.param == "responses":
        data["tools"] = [{"type": "function", **definition}]
    else:
        data["functions"] = [definition]
    return data


def test_public_tool_definition_is_classified_in_full(ns, monkeypatch, data):
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    assert _run(ns, _key("untrusted"), data) is data
    assert len(classifier.calls) == 1
    text = classifier.calls[0]["body"]["messages"][1]["content"]
    key = "tools" if "tools" in data else "functions"
    assert json.loads(text.removeprefix("<text>\nhello there\n").removesuffix("\n</text>")) == data[key]
    assert "café" in text


@pytest.mark.parametrize("location", ["description", "property", "enum"])
def test_sensitive_schema_text_is_refused(ns, monkeypatch, data, location):
    definition = data.get("functions", data.get("tools"))[0]
    definition = definition.get("function", definition)
    if location == "description":
        definition["parameters"]["description"] = "sensitive schema text"
    elif location == "property":
        definition["parameters"]["properties"]["sensitive property"] = {"type": "string"}
    else:
        definition["parameters"]["properties"]["role"]["enum"] = ["sensitive enum"]
    calls = []

    async def classify(url, key, body, timeout):
        text = body["messages"][1]["content"]
        calls.append(text)
        return {"choices": [{"message": {"content": "sensitive" if "sensitive" in text else "public"}}]}

    monkeypatch.setitem(ns, "_post", classify)
    _refused(ns, _key("untrusted"), data)
    assert len(calls) == 1
    assert "sensitive" in calls[0]


def test_tool_definition_counts_toward_character_cap(ns, monkeypatch, data):
    monkeypatch.setenv("LLM_ROUTER_UNTRUSTED_CLASSIFY_MAX_CHARS", "20")
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), data)
    assert classifier.calls == []


def test_tool_schema_does_not_disable_content_block_validation(ns, monkeypatch, data):
    data["messages"][0]["content"] = [{"type": "image_url", "image_url": {}}]
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), data)
    assert classifier.calls == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_tool_schema_is_refused(ns, monkeypatch, definition, value):
    definition["parameters"]["default"] = value
    data = {**_data(), "functions": [definition]}
    classifier = Classifier(reply="public")
    monkeypatch.setitem(ns, "_post", classifier)
    _refused(ns, _key("untrusted"), data)
    assert classifier.calls == []
