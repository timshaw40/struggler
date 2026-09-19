"""Tests for the OpenAI `LLMClient` adapter: request building, response
parsing, usage normalization, and the strict-mode schema transform. The
`openai` SDK's own HTTP call is monkeypatched out -- no real network access.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("openai")

from struggler.bots.llm.client import (
    LLMClientError,
    LLMMessage,
    LLMRequest,
    LLMTruncatedError,
    StructuredOutputSpec,
    redact_secrets,
    strip_json_fence,
)
from struggler.bots.llm.openai_client import OpenAIClient, _to_openai_strict_schema
from struggler.bots.llm.schema import PLAN_SCHEMA


def _client() -> OpenAIClient:
    return OpenAIClient(model="gpt-5", api_key="test-key-not-a-real-key")


def test_complete_builds_request_and_parses_response(monkeypatch):
    client = _client()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        payload = {"justification": "because", "steps": []}
        message = SimpleNamespace(content=json.dumps(payload))
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=34)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    request = LLMRequest(
        system="system prompt",
        messages=(LLMMessage(role="user", content="hello"),),
        output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"}),
    )
    response = client.complete(request)

    assert captured["messages"][0] == {"role": "system", "content": "system prompt"}
    assert captured["messages"][1] == {"role": "user", "content": "hello"}
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert response.structured == {"justification": "because", "steps": []}
    assert json.loads(response.raw_text) == {"justification": "because", "steps": []}
    assert response.usage == {"input_tokens": 12, "output_tokens": 34}


def test_complete_raises_llm_client_error_on_unparseable_content(monkeypatch):
    client = _client()

    def fake_create(**kwargs):
        message = SimpleNamespace(content="not json")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    request = LLMRequest(
        system="s", messages=(), output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"})
    )
    with pytest.raises(LLMClientError):
        client.complete(request)


def test_complete_raises_llm_client_error_on_missing_content(monkeypatch):
    client = _client()

    def fake_create(**kwargs):
        message = SimpleNamespace(content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    request = LLMRequest(
        system="s", messages=(), output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"})
    )
    with pytest.raises(LLMClientError):
        client.complete(request)


def test_complete_raises_llm_client_error_on_sdk_exception(monkeypatch):
    client = _client()

    def fake_create(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    request = LLMRequest(
        system="s", messages=(), output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"})
    )
    with pytest.raises(LLMClientError):
        client.complete(request)


def test_complete_falls_back_to_reasoning_content_when_content_empty(monkeypatch):
    # Reasoning models behind LM Studio emit the schema-constrained answer
    # as `reasoning_content` with an empty `content`.
    client = _client()

    def fake_create(**kwargs):
        message = SimpleNamespace(content="", reasoning_content='{"a": 1}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    request = LLMRequest(
        system="s", messages=(), output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"})
    )
    assert client.complete(request).structured == {"a": 1}


def test_constructor_forwards_base_url(monkeypatch):
    import openai

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    OpenAIClient(model="qwen/qwen3.8-27b", api_key="local", base_url="http://192.168.10.91:1234/v1")
    assert captured == {
        "api_key": "local",
        "base_url": "http://192.168.10.91:1234/v1",
        "timeout": 600.0,
    }


def test_build_llm_client_openai_compatible_uses_base_url(monkeypatch):
    import openai
    from main import build_llm_client

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("STRUGGLER_LLM_BASE_URL", "http://192.168.10.91:1234/v1")
    client = build_llm_client(provider="openai_compatible")
    assert client.model_name == "qwen3.6-35b-a3b-uncensored-genesis-hermes-v7"
    assert captured["base_url"] == "http://192.168.10.91:1234/v1"


def test_to_openai_strict_schema_marks_optional_payload_keys_nullable_and_required():
    transformed = _to_openai_strict_schema(PLAN_SCHEMA)

    top_required = set(transformed["required"])
    assert top_required == {"justification", "steps"}

    step_schema = transformed["properties"]["steps"]["items"]
    assert set(step_schema["required"]) == {"kind", "payload"}

    payload_schema = step_schema["properties"]["payload"]
    payload_keys = set(payload_schema["properties"])
    assert payload_keys == {"country", "card", "mode", "type", "order", "choice"}
    assert set(payload_schema["required"]) == payload_keys  # every optional key is now required
    for key in payload_keys:
        prop_type = payload_schema["properties"][key]["type"]
        assert "null" in prop_type
    # Strict mode also rejects a nullable enum that doesn't list null.
    for key in ("mode", "type", "order"):
        enum = payload_schema["properties"][key].get("enum")
        assert enum is not None and None in enum


def test_complete_raises_truncated_on_finish_reason_length(monkeypatch):
    client = _client()
    truncated = SimpleNamespace(
        finish_reason="length",
        message=SimpleNamespace(content='{"a": 1}', reasoning_content=None),
    )
    monkeypatch.setattr(
        client._client.chat.completions, "create",
        lambda **kw: SimpleNamespace(choices=[truncated], usage=None),
    )
    request = LLMRequest(
        system="s", messages=(),
        output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"}),
    )
    with pytest.raises(LLMTruncatedError):
        client.complete(request)


def test_complete_strips_a_json_code_fence(monkeypatch):
    client = _client()
    fenced = SimpleNamespace(
        finish_reason="stop",
        message=SimpleNamespace(content='```json\n{"a": 1}\n```', reasoning_content=None),
    )
    monkeypatch.setattr(
        client._client.chat.completions, "create",
        lambda **kw: SimpleNamespace(choices=[fenced], usage=None),
    )
    request = LLMRequest(
        system="s", messages=(),
        output=StructuredOutputSpec(name="x", description="y", schema={"type": "object"}),
    )
    assert client.complete(request).structured == {"a": 1}


def test_strip_json_fence_and_redact_secrets():
    assert strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_fence('{"a": 1}') == '{"a": 1}'
    assert "sk-abc12345" not in redact_secrets("bad key: sk-abc12345")
    assert "supersecret" not in redact_secrets('api_key="supersecret"')
    assert "leakedtok" not in redact_secrets("Authorization: Bearer leakedtok123")
