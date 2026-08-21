"""按 Agent 独立配置的 LLM 客户端。

刻意不走 ``agentsociety2.config.get_llm_router``：那是进程级单例 Router，
并且强制校验全局 API key，无法让每个小人各自指向不同的 ollama / OpenAI 兼容端点。
这里直接用 httpx 打 HTTP，配置随 Agent 走，可随时增删。
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

Provider = Literal["ollama", "openai"]

DEFAULT_TIMEOUT_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 10.0
PROBE_MAX_TOKENS = 8


class LLMEndpoint(BaseModel):
    """一个小人的模型端点配置。"""

    provider: Provider = "ollama"
    base_url: str = Field("http://localhost:11434", description="ollama 根地址或 OpenAI 兼容的 /v1 地址")
    model: str = Field(..., min_length=1)
    api_key: str | None = None
    temperature: float = Field(0.8, ge=0.0, le=2.0)

    def normalized_base(self) -> str:
        return self.base_url.rstrip("/")


class EndpointTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    models: list[str] = Field(default_factory=list)
    sample_reply: str | None = None
    error: str | None = None


def _headers(endpoint: LLMEndpoint) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    return headers


def _describe_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        body = error.response.text.strip()
        detail = body[:300] if body else error.response.reason_phrase
        return f"HTTP {error.response.status_code}: {detail}"
    if isinstance(error, httpx.ConnectError):
        return f"无法连接：{error}"
    if isinstance(error, httpx.TimeoutException):
        return f"请求超时：{error}"
    return f"{type(error).__name__}: {error}"


async def chat(
    endpoint: LLMEndpoint,
    messages: list[dict[str, str]],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_tokens: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """发一轮对话，返回助手回复的纯文本。异常直接抛出，由调用方处理。"""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        if endpoint.provider == "ollama":
            payload: dict[str, Any] = {
                "model": endpoint.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": endpoint.temperature},
            }
            if max_tokens is not None:
                payload["options"]["num_predict"] = max_tokens
            response = await http.post(
                f"{endpoint.normalized_base()}/api/chat",
                json=payload,
                headers=_headers(endpoint),
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            return str(data.get("message", {}).get("content", "")).strip()

        payload = {
            "model": endpoint.model,
            "messages": messages,
            "temperature": endpoint.temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = await http.post(
            f"{endpoint.normalized_base()}/chat/completions",
            json=payload,
            headers=_headers(endpoint),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "")).strip()
    finally:
        if owns_client:
            await http.aclose()


async def list_models(endpoint: LLMEndpoint, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> list[str]:
    """探测端点上可用的模型名。失败时抛出。"""
    async with httpx.AsyncClient(timeout=timeout) as http:
        if endpoint.provider == "ollama":
            response = await http.get(
                f"{endpoint.normalized_base()}/api/tags",
                headers=_headers(endpoint),
            )
            response.raise_for_status()
            entries = response.json().get("models") or []
            return [str(item.get("name")) for item in entries if item.get("name")]

        response = await http.get(
            f"{endpoint.normalized_base()}/models",
            headers=_headers(endpoint),
        )
        response.raise_for_status()
        entries = response.json().get("data") or []
        return [str(item.get("id")) for item in entries if item.get("id")]


async def test_endpoint(endpoint: LLMEndpoint) -> EndpointTestResult:
    """先列模型确认端点活着，再发一次极短对话确认模型真能回话。"""
    started = time.monotonic()
    models: list[str] = []
    try:
        models = await list_models(endpoint)
    except Exception as error:  # noqa: BLE001 - 面向用户的连通性检查
        return EndpointTestResult(ok=False, models=[], error=_describe_error(error))

    if models and endpoint.model not in models:
        # ollama 的 tag 可能带 :latest 后缀，宽松匹配一次再判定。
        loose = {name.split(":", 1)[0] for name in models}
        if endpoint.model.split(":", 1)[0] not in loose:
            return EndpointTestResult(
                ok=False,
                models=models,
                error=f"端点上没有模型 {endpoint.model}；可用：{', '.join(models[:10])}",
            )

    try:
        reply = await chat(
            endpoint,
            [{"role": "user", "content": "ping"}],
            timeout=PROBE_TIMEOUT_SECONDS * 3,
            max_tokens=PROBE_MAX_TOKENS,
        )
    except Exception as error:  # noqa: BLE001
        return EndpointTestResult(ok=False, models=models, error=_describe_error(error))

    latency_ms = int((time.monotonic() - started) * 1000)
    return EndpointTestResult(ok=True, latency_ms=latency_ms, models=models, sample_reply=reply)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型回复里抠出第一个 JSON 对象；小模型常会包在 ``` 或废话里。"""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None
