"""AI 客户端基类"""
import asyncio
import hashlib
import json
import ssl
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from app.logger import get_logger
from app.services.ai_config import AIClientConfig, default_config

logger = get_logger(__name__)

# 全局 HTTP 客户端池
_http_client_pool: Dict[str, httpx.AsyncClient] = {}
_global_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    """获取全局信号量"""
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(max_concurrent)
    return _global_semaphore


class BaseAIClient(ABC):
    """AI HTTP 客户端基类"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        config: Optional[AIClientConfig] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.config = config or default_config
        self.http_client = self._get_or_create_client()

    def _get_client_key(self) -> str:
        """生成客户端唯一键"""
        key_hash = hashlib.md5(self.api_key.encode()).hexdigest()[:8]
        return f"{self.__class__.__name__}_{self.base_url}_{key_hash}"

    def _get_or_create_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        client_key = self._get_client_key()

        if client_key in _http_client_pool:
            client = _http_client_pool[client_key]
            if not client.is_closed:
                return client
            del _http_client_pool[client_key]

        http_cfg = self.config.http
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=http_cfg.connect_timeout,
                read=http_cfg.read_timeout,
                write=http_cfg.write_timeout,
                pool=http_cfg.pool_timeout,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=http_cfg.max_keepalive_connections,
                max_connections=http_cfg.max_connections,
                keepalive_expiry=http_cfg.keepalive_expiry,
            ),
        )
        _http_client_pool[client_key] = client
        logger.info(f"✅ 创建 HTTP 客户端: {client_key}")
        return client

    @abstractmethod
    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        pass

    def _raise_sse_error_if_present(self, body: str, response: httpx.Response) -> None:
        """兼容部分 OpenAI 兼容上游用 HTTP 200 返回 SSE error 行的情况。"""
        if "data:" not in body or '"error"' not in body:
            return

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            error = payload.get("error") if isinstance(payload, dict) else None
            if not isinstance(error, dict):
                continue

            message = str(error.get("message") or "AI upstream error")
            error_type = str(error.get("type") or "")
            code = str(error.get("code") or "")
            lower_text = f"{message} {error_type} {code}".lower()
            status_code = 429 if (
                "rate_limit" in lower_text
                or "rate limit" in lower_text
                or "no available tokens" in lower_text
            ) else 502
            synthetic_response = httpx.Response(
                status_code=status_code,
                request=response.request,
                content=json.dumps(error, ensure_ascii=False).encode("utf-8"),
            )
            raise httpx.HTTPStatusError(
                f"AI upstream SSE error: {message}",
                request=response.request,
                response=synthetic_response,
            )

    def _parse_openai_sse_body(self, body: str, response: httpx.Response) -> Optional[Dict[str, Any]]:
        """兼容部分 OpenAI 兼容上游在非流式请求中仍返回 SSE chunk 的情况。"""
        if "data:" not in body:
            return None

        content_parts: list[str] = []
        finish_reason: Optional[str] = None
        usage: Dict[str, Any] = {}
        tool_calls_buffer: Dict[int, Dict[str, Any]] = {}
        saw_sse_chunk = False

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str:
                continue
            saw_sse_chunk = True
            if data_str == "[DONE]":
                break

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or "AI upstream error")
                error_type = str(error.get("type") or "")
                code = str(error.get("code") or "")
                lower_text = f"{message} {error_type} {code}".lower()
                status_code = 429 if (
                    "rate_limit" in lower_text
                    or "rate limit" in lower_text
                    or "no available tokens" in lower_text
                ) else 502
                synthetic_response = httpx.Response(
                    status_code=status_code,
                    request=response.request,
                    content=json.dumps(error, ensure_ascii=False).encode("utf-8"),
                )
                raise httpx.HTTPStatusError(
                    f"AI upstream SSE error: {message}",
                    request=response.request,
                    response=synthetic_response,
                )

            if not isinstance(payload, dict):
                continue

            if isinstance(payload.get("usage"), dict):
                usage = payload["usage"]

            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue

            choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}

            content = delta.get("content")
            if content is None:
                content = message.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)

            tool_calls = delta.get("tool_calls") or message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    index = int(tool_call.get("index") or len(tool_calls_buffer))
                    existing = tool_calls_buffer.setdefault(index, dict(tool_call))
                    if existing is tool_call:
                        continue
                    function_delta = tool_call.get("function")
                    if isinstance(function_delta, dict):
                        existing_function = existing.setdefault("function", {})
                        if function_delta.get("name"):
                            existing_function["name"] = function_delta["name"]
                        if function_delta.get("arguments"):
                            existing_function["arguments"] = (
                                str(existing_function.get("arguments") or "") + str(function_delta["arguments"])
                            )

        if not saw_sse_chunk:
            return None
        if not content_parts and not tool_calls_buffer:
            return None

        message: Dict[str, Any] = {"content": "".join(content_parts)}
        if tool_calls_buffer:
            message["tool_calls"] = list(tool_calls_buffer.values())

        return {
            "choices": [
                {
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        payload: Dict[str, Any],
        stream: bool = False,
    ) -> Any:
        """带重试的 HTTP 请求"""
        url = f"{self.base_url}{endpoint}"
        headers = self._build_headers()
        retry_cfg = self.config.retry
        rate_cfg = self.config.rate_limit

        semaphore = _get_semaphore(rate_cfg.max_concurrent_requests)

        async with semaphore:
            await asyncio.sleep(rate_cfg.request_delay)

            for attempt in range(retry_cfg.max_retries):
                try:
                    if attempt > 0:
                        delay = min(
                            retry_cfg.base_delay * (retry_cfg.exponential_base ** attempt),
                            retry_cfg.max_delay,
                        )
                        logger.warning(f"⚠️ 重试 {attempt + 1}/{retry_cfg.max_retries}，等待 {delay}s")
                        await asyncio.sleep(delay)

                    if stream:
                        return self.http_client.stream(method, url, headers=headers, json=payload)

                    response = await self.http_client.request(method, url, headers=headers, json=payload)
                    response.raise_for_status()

                    body = response.text
                    if not body or not body.strip():
                        raise httpx.DecodingError("API 返回空响应体")
                    import json as _json
                    try:
                        return _json.loads(body)
                    except _json.JSONDecodeError as je:
                        sse_data = self._parse_openai_sse_body(body, response)
                        if sse_data is not None:
                            return sse_data
                        self._raise_sse_error_if_present(body, response)
                        logger.error(f"❌ JSON解析失败, status={response.status_code}, body前200字符={body[:200]}")
                        raise httpx.DecodingError(f"JSON解析失败: {je}") from je

                except httpx.HTTPStatusError as e:
                    if e.response.status_code in retry_cfg.non_retryable_status_codes:
                        raise
                    if attempt == retry_cfg.max_retries - 1:
                        raise
                except (
                    httpx.TransportError,
                    httpx.DecodingError,
                    ssl.SSLError,
                    OSError,
                ) as e:
                    logger.warning(
                        "⚠️ AI HTTP 请求异常: %s: %s",
                        type(e).__name__,
                        str(e)[:300],
                    )
                    if attempt == retry_cfg.max_retries - 1:
                        raise

    @abstractmethod
    async def chat_completion(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """聊天补全"""
        pass

    @abstractmethod
    async def chat_completion_stream(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """流式聊天补全"""
        pass


async def cleanup_all_clients():
    """清理所有 HTTP 客户端"""
    for key, client in list(_http_client_pool.items()):
        if not client.is_closed:
            await client.aclose()
    _http_client_pool.clear()
    logger.info("✅ HTTP 客户端池已清理")
