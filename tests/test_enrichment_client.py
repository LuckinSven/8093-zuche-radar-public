import json

import httpx
import pytest

from app.enrichment.client import EnrichmentClientError, OpenAICompatibleClient
from app.enrichment.domain import ModelPrompt


PROMPT = ModelPrompt(
    model_id=4952,
    name="比亚迪海狮05",
    description="1.5L插电混动 | SUV 5座",
)


def _completion(items, *, prompt_tokens=80, completion_tokens=30):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "custom-model",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": json.dumps({"items": items}, ensure_ascii=False),
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _suggestion(model_id=4952, **overrides):
    return {
        "model_id": model_id,
        "energy_type": "新能源",
        "energy_subtype": "插电混动",
        "confidence": "HIGH",
        "rationale": "车型名称和原始描述均明确标注插电混动",
        "sources": [],
    } | overrides


@pytest.mark.asyncio
async def test_client_posts_json_mode_and_returns_validated_suggestions():
    captured = {}

    def handler(request: httpx.Request):
        captured["request"] = request
        return httpx.Response(200, json=_completion([_suggestion()]))

    async with OpenAICompatibleClient(
        api_key="sk-test-secret",
        base_url="https://api.example.com/v1/",
        model="custom-model",
        timeout_seconds=90,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.classify([PROMPT])

    request = captured["request"]
    body = json.loads(request.content)
    assert request.url == "https://api.example.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-secret"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0
    assert body["model"] == "custom-model"
    assert "不知道时返回未知" in body["messages"][0]["content"]
    assert result.items[0].energy_subtype == "插电混动"
    assert result.usage.total_tokens == 110
    assert result.request_count == 1


@pytest.mark.asyncio
async def test_client_retries_retryable_status_and_counts_requests():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="temporary limit")
        return httpx.Response(200, json=_completion([_suggestion()]))

    async with OpenAICompatibleClient(
        "secret", "https://api.example.com", "model-x", 10, 1,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.classify([PROMPT])

    assert calls == 2
    assert result.request_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "retryable"), [
    (400, False), (401, False), (403, False), (404, False),
    (408, True), (429, True), (500, True), (503, True),
])
async def test_client_maps_http_status_to_safe_retryability(status_code, retryable):
    secret = "sk-private-secret"
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status_code, text=f"Bearer {secret} invalid"))
    async with OpenAICompatibleClient(
        secret, "https://api.example.com", "model-x", 10, 0, transport=transport,
    ) as client:
        with pytest.raises(EnrichmentClientError) as captured:
            await client.classify([PROMPT])

    assert captured.value.retryable is retryable
    assert secret not in str(captured.value)
    assert "Bearer" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"choices": []},
    {"choices": [{"message": {"content": ""}}]},
    {"choices": [{"message": {"content": "not-json"}}]},
    {"choices": [{"message": {"content": json.dumps({"items": []})}}]},
    {"choices": [{"message": {"content": json.dumps({
        "items": [_suggestion(), _suggestion()],
    }, ensure_ascii=False)}}]},
    {"choices": [{"message": {"content": json.dumps({
        "items": [_suggestion(model_id=9999)],
    }, ensure_ascii=False)}}]},
    {"choices": [{"message": {"content": json.dumps({
        "items": [_suggestion(energy_subtype="汽油")],
    }, ensure_ascii=False)}}]},
])
async def test_client_rejects_incomplete_or_invalid_structured_results(response):
    async with OpenAICompatibleClient(
        "secret", "https://api.example.com", "model-x", 10, 0,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    ) as client:
        with pytest.raises(EnrichmentClientError, match="AI 接口返回格式异常"):
            await client.classify([PROMPT])


@pytest.mark.asyncio
async def test_connection_requires_known_example_result():
    response = _completion([{
        "model_id": -1,
        "energy_type": "新能源",
        "energy_subtype": "纯电",
        "confidence": "HIGH",
        "rationale": "固定测试车型为纯电",
        "sources": [],
    }], prompt_tokens=20, completion_tokens=10)
    async with OpenAICompatibleClient(
        "secret", "https://api.example.com", "model-x", 10, 0,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    ) as client:
        result = await client.test_connection()

    assert result.ok is True
    assert result.model == "model-x"
    assert result.elapsed_ms >= 0
    assert result.usage.total_tokens == 30
