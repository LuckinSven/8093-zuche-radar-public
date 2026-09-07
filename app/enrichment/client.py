"""只暴露安全错误的 OpenAI 兼容 Chat Completions 客户端。"""

import json
from time import perf_counter

import httpx
from pydantic import ValidationError

from app.enrichment.domain import (
    CompletionBatch,
    ConnectionTestResult,
    EnergySuggestion,
    ModelPrompt,
    TokenUsage,
)


SYSTEM_PROMPT = """你负责识别神州租车车型的能源类型。只根据车型名称和原始描述判断；不知道时返回未知，不得猜测。
必须只返回一个 JSON 对象，格式为 {"items":[{"model_id":1,"energy_type":"燃油|新能源|未知","energy_subtype":"汽油|柴油|纯电|插电混动|增程|油电混动|其他|未知","confidence":"HIGH|MEDIUM|LOW","rationale":"简短中文依据","sources":[]}]}。
燃油只可搭配汽油、柴油、油电混动、其他、未知；新能源只可搭配纯电、插电混动、增程、其他、未知；未知只能搭配未知。每个输入 model_id 必须且只能返回一次。"""


class EnrichmentClientError(RuntimeError):
    """可安全显示给用户的 AI 接口错误。"""

    def __init__(self, public_message: str, retryable: bool) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.retryable = retryable


class OpenAICompatibleClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        max_retries: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def __aenter__(self) -> "OpenAICompatibleClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def classify(
        self,
        models: list[ModelPrompt],
        focused: bool = False,
    ) -> CompletionBatch:
        if not models:
            raise EnrichmentClientError("没有需要识别的车型", retryable=False)
        payload = {
            "model": self.model,
            "messages": _build_messages(models, focused),
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max(1200, len(models) * 220),
        }
        data, request_count = await self._post(payload)
        return _parse_completion(
            data,
            expected_ids={item.model_id for item in models},
            request_count=request_count,
        )

    async def test_connection(self) -> ConnectionTestResult:
        started = perf_counter()
        result = await self.classify([ModelPrompt(
            model_id=-1,
            name="特斯拉 Model 3 纯电车型（连接测试）",
            description="纯电动汽车",
        )], focused=True)
        suggestion = result.items[0]
        if not (
            suggestion.energy_type == "新能源"
            and suggestion.energy_subtype == "纯电"
            and suggestion.confidence == "HIGH"
        ):
            raise EnrichmentClientError(
                "AI 接口可访问，但固定车型测试结果不符合要求",
                retryable=False,
            )
        return ConnectionTestResult(
            ok=True,
            elapsed_ms=max(0, round((perf_counter() - started) * 1000)),
            model=self.model,
            usage=result.usage,
        )

    async def _post(self, payload: dict) -> tuple[dict, int]:
        request_count = 0
        for attempt in range(self.max_retries + 1):
            request_count += 1
            try:
                response = await self.client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if attempt < self.max_retries:
                    continue
                raise EnrichmentClientError("AI 接口网络请求失败", retryable=True) from error

            if response.status_code >= 400:
                retryable = (
                    response.status_code in {408, 429}
                    or response.status_code >= 500
                )
                if retryable and attempt < self.max_retries:
                    continue
                raise EnrichmentClientError(
                    _http_error_message(response.status_code),
                    retryable=retryable,
                )
            try:
                data = response.json()
            except ValueError as error:
                raise EnrichmentClientError(
                    "AI 接口返回格式异常",
                    retryable=True,
                ) from error
            if not isinstance(data, dict):
                raise EnrichmentClientError("AI 接口返回格式异常", retryable=True)
            return data, request_count
        raise EnrichmentClientError("AI 接口网络请求失败", retryable=True)


def _build_messages(models: list[ModelPrompt], focused: bool) -> list[dict[str, str]]:
    mode = "AI 二次识别，请仔细核对所有线索" if focused else "单车型初步判断"
    content = json.dumps(
        {"mode": mode, "models": [item.model_dump() for item in models]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _parse_completion(data: dict, expected_ids: set[int], request_count: int) -> CompletionBatch:
    try:
        choices = data["choices"]
        content = choices[0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        decoded = json.loads(content)
        raw_items = decoded["items"]
        if not isinstance(raw_items, list):
            raise ValueError
        items = [EnergySuggestion.model_validate(item) for item in raw_items]
        returned_ids = [item.model_id for item in items]
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != expected_ids:
            raise ValueError
        usage_data = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        return CompletionBatch(items=items, usage=usage, request_count=request_count)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
        raise EnrichmentClientError("AI 接口返回格式异常", retryable=True) from error


def _http_error_message(status_code: int) -> str:
    if status_code in {401, 403}:
        return "AI 接口认证失败，请检查 API Key"
    if status_code == 404:
        return "AI 接口地址或模型不存在"
    if status_code == 429:
        return "AI 接口请求过于频繁或额度不足"
    if status_code in {408} or status_code >= 500:
        return "AI 接口暂时不可用"
    return "AI 接口拒绝了当前请求，请检查兼容配置"
