# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServerClient - Gateway 與 AgentServer 的 WebSocket 客戶端."""

from __future__ import annotations

import logging
import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

from jiuwenclaw.common.e2a.constants import E2A_WIRE_SERVER_PUSH_KEY
from jiuwenclaw.common.e2a.models import E2AEnvelope
from jiuwenclaw.common.e2a.wire_codec import (
    parse_agent_server_wire_chunk,
    parse_agent_server_wire_unary,
)
from jiuwenclaw.common.schema.agent import AgentResponse, AgentResponseChunk


logger = logging.getLogger(__name__)
_STREAM_TRAILING_MESSAGE_GRACE_SECONDS = 0.7
_UNARY_REQUEST_TIMEOUT_SECONDS = 600.0
_WS_MAX_SIZE = 8 * 2**20


def _wire_request_id_key(request_id: Any) -> str:
    """與 AgentServer 回包 ``request_id`` 對齊：統一為 str，避免 JSON 數字/字串導致佇列鍵不一致。"""
    if request_id is None:
        return ""
    return str(request_id)


def _to_json(data: Any) -> str:
    """將任意物件序列化為日誌友好的 JSON 字串."""
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(data)


def _build_ws_origin(uri: str) -> str | None:
    """將 ws/wss URI 轉為標準瀏覽器 Origin。"""
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None

    if not parsed.netloc:
        return None

    scheme = "https" if parsed.scheme == "wss" else "http"
    return f"{scheme}://{parsed.netloc}"


class AgentServerClient(ABC):
    """AgentServer WebSocket 客戶端介面."""

    @abstractmethod
    async def connect(self, uri: str) -> None:
        """建立與 AgentServer 的 WebSocket 連線."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """斷開連線."""
        ...

    @abstractmethod
    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """快取或更新服務端配置快照，供自定義 client 後續使用."""
        ...

    @abstractmethod
    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        """傳送 E2A 信封，等待完整響應."""
        ...

    @abstractmethod
    async def send_request_stream(
        self, envelope: E2AEnvelope
    ) -> AsyncIterator[AgentResponseChunk]:
        """傳送 E2A 信封，流式接收響應."""
        ...


def _e2a_to_wire(envelope: E2AEnvelope) -> dict[str, Any]:
    """E2AEnvelope → WebSocket JSON（與 AgentServer from_dict 對齊）。"""
    return envelope.to_dict()


class WebSocketAgentServerClient(AgentServerClient):
    """
    基於 websockets 的 AgentServer WebSocket 客戶端實現。

    協議約定：
    - 傳送：JSON 物件為 E2AEnvelope.to_dict()（含 protocol_version、method、channel、params、is_stream 等）。
    - 接收（非流式）：一條 **E2AResponse** 線 JSON（或過渡期 legacy AgentResponse 形），解析為 AgentResponse。
    - 接收（流式）：多條 E2AResponse 線 JSON（或 legacy chunk），解析為 AgentResponseChunk。
    """

    def __init__(self, *, ping_interval: float | None = 30.0, ping_timeout: float | None = 300.0) -> None:
        self._uri: str | None = None
        self._ws: Any = None
        self._lock = asyncio.Lock()
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server_ready: bool = False
        # 訊息分發機制：根據 request_id 路由到對應佇列
        self._message_queues: dict[str, asyncio.Queue] = {}
        self._queue_lock = asyncio.Lock()  # 保護佇列操作的鎖
        self._cancelled_request_ids: set[str] = set()  # 已取消但等待清理的 request_id
        self._receiver_task: asyncio.Task | None = None
        self._running = False
        # AgentServer send_push：旁路投遞，勿進入與 request_id 繫結的 RPC 等待佇列
        self._on_server_push: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def set_server_push_handler(
        self, handler: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """註冊 Agent 主動推送處理回撥（metadata 含 ``E2A_WIRE_SERVER_PUSH_KEY`` 的幀）。"""
        self._on_server_push = handler

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """預設 WebSocket client 不處理服務端配置快取，留給擴充套件 client 自行實現."""
        return None

    @property
    def server_ready(self) -> bool:
        """AgentServer 是否已傳送 connection.ack 確認就緒."""
        return self._server_ready

    async def connect(self, uri: str) -> None:
        if self._ws is not None:
            await self.disconnect()
        logger.info("[WebSocketAgentServerClient] 正在連線: %s", uri)
        self._uri = uri
        self._server_ready = False
        origin = _build_ws_origin(uri)
        try:
            from websockets.legacy.client import connect as legacy_connect
            connect_fn = legacy_connect
        except ImportError:
            import websockets
            connect_fn = websockets.connect
        self._ws = await connect_fn(
            uri,
            origin=origin,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5.0,
            max_size=_WS_MAX_SIZE,
        )
        logger.info("[WebSocketAgentServerClient] 已連線: %s", uri)

        # 讀取 AgentServer 的 connection.ack 事件
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            logger.info("[WebSocketAgentServerClient] connect 首幀(raw): %s", raw)
            data = json.loads(raw)
            logger.info("[WebSocketAgentServerClient] connect 首幀(parsed): %s", _to_json(data))
            if data.get("type") == "event" and data.get("event") == "connection.ack":
                self._server_ready = True
                logger.info("[WebSocketAgentServerClient] 收到 connection.ack，AgentServer 已就緒")
            else:
                logger.warning(
                    "[WebSocketAgentServerClient] 首幀非 connection.ack: %s",
                    data.get("type"),
                )
        except asyncio.TimeoutError:
            logger.warning("[WebSocketAgentServerClient] 等待 connection.ack 超時")
        except Exception as e:
            logger.warning("[WebSocketAgentServerClient] 讀取 connection.ack 失敗: %s", e)

        # 啟動訊息接收和分發任務
        self._running = True
        self._receiver_task = asyncio.create_task(self._message_receiver_loop())
        logger.info("[WebSocketAgentServerClient] 訊息接收任務已啟動")

    async def _message_receiver_loop(self) -> None:
        """後臺任務：從 WebSocket 接收訊息並根據 request_id 分發到對應佇列."""
        try:
            while self._running and self._ws is not None:
                try:
                    raw = await self._ws.recv()
                    data = json.loads(raw)
                    meta = data.get("metadata")
                    if isinstance(meta, dict) and meta.get(E2A_WIRE_SERVER_PUSH_KEY):
                        if self._on_server_push is not None:
                            asyncio.create_task(self._on_server_push(data))
                        else:
                            logger.warning(
                                "[WebSocketAgentServerClient] 收到 server_push 但未註冊 handler，已丟棄: "
                                "request_id=%s",
                                data.get("request_id"),
                            )
                        continue
                    request_id = _wire_request_id_key(data.get("request_id"))

                    # 使用鎖保護佇列訪問，避免競態條件
                    async with self._queue_lock:
                        # 檢查是否是已取消的請求，靜默丟棄訊息
                        if request_id in self._cancelled_request_ids:
                            logger.debug(
                                "[WebSocketAgentServerClient] 收到已取消請求的殘餘訊息，已丟棄: request_id=%s",
                                request_id
                            )
                            continue

                        if request_id and request_id in self._message_queues:
                            await self._message_queues[request_id].put(data)
                        else:
                            # 沒有對應的佇列（非預期情況）
                            logger.debug(
                                "[WebSocketAgentServerClient] 收到無目標佇列的訊息: request_id=%s",
                                request_id
                            )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception("[WebSocketAgentServerClient] 訊息接收迴圈異常: %s", e)
                    await asyncio.sleep(0.1)  # 避免快速迴圈
        finally:
            logger.info("[WebSocketAgentServerClient] 訊息接收任務已停止")

    async def disconnect(self) -> None:
        # 停止接收任務
        self._running = False
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        # 清理所有佇列
        self._message_queues.clear()

        # 關閉 WebSocket
        if self._ws is None:
            return
        try:
            await self._ws.close()
        except Exception as e:
            logger.warning("關閉 AgentServer WebSocket 時異常: %s", e)
        finally:
            self._ws = None
            self._uri = None
        logger.info("[WebSocketAgentServerClient] 已斷開")

    def _ensure_connected(self) -> None:
        if self._ws is None:
            raise RuntimeError("未連線 AgentServer，請先呼叫 connect(uri)")

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        self._ensure_connected()
        # 非流式 API 必須與 AgentServer 的 unary 路徑一致；忽略信封上誤帶的 is_stream=True。
        envelope.is_stream = False
        rid = _wire_request_id_key(envelope.request_id)
        logger.info(
            "[E2A][out][nostream] request_id=%s channel=%s method=%s is_stream=%s",
            rid,
            envelope.channel,
            envelope.method,
            envelope.is_stream,
        )
        logger.debug(
            "[WebSocketAgentServerClient] 傳送請求(非流式) E2A: %s",
            _to_json(envelope.to_dict()),
        )

        if rid in self._message_queues:
            raise RuntimeError(
                f"WebSocketAgentServerClient: duplicate in-flight request_id={rid!r}; "
                "refusing to register queue (would mis-route responses, e.g. stream chunks to unary waiters)."
            )

        # 建立該請求的訊息佇列
        queue = asyncio.Queue()
        self._message_queues[rid] = queue

        try:
            # 傳送請求
            async with self._lock:
                payload = _e2a_to_wire(envelope)
                logger.info("[WebSocketAgentServerClient] 傳送請求(非流式) payload: %s", _to_json(payload))
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

            try:
                data = await asyncio.wait_for(queue.get(), timeout=_UNARY_REQUEST_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as e:
                logger.warning(
                    "[WebSocketAgentServerClient] 非流式請求超時: request_id=%s timeout=%ss",
                    rid,
                    _UNARY_REQUEST_TIMEOUT_SECONDS,
                )
                raise RuntimeError(
                    f"AgentServer 非流式請求超時 (request_id={rid}, timeout={_UNARY_REQUEST_TIMEOUT_SECONDS}s)"
                ) from e
            logger.info("[WebSocketAgentServerClient] 收到響應(非流式) raw: %s", json.dumps(data, ensure_ascii=False))
            resp = parse_agent_server_wire_unary(data)
            logger.info("[WebSocketAgentServerClient] 收到完整響應 AgentResponse: %s", _to_json(asdict(resp)))
            return resp
        finally:
            # 清理佇列
            await self._drain_and_remove_queue(rid)

    async def send_request_stream(
        self, envelope: E2AEnvelope
    ) -> AsyncIterator[AgentResponseChunk]:
        self._ensure_connected()
        envelope.is_stream = True
        rid = _wire_request_id_key(envelope.request_id)
        logger.info(
            "[E2A][out][stream] request_id=%s channel=%s method=%s is_stream=%s",
            rid,
            envelope.channel,
            envelope.method,
            envelope.is_stream,
        )
        logger.debug(
            "[WebSocketAgentServerClient] 傳送請求(流式) E2A: %s",
            _to_json(envelope.to_dict()),
        )

        if rid in self._message_queues:
            raise RuntimeError(
                f"WebSocketAgentServerClient: duplicate in-flight request_id={rid!r}; "
                "refusing to register queue (would mis-route responses, e.g. stream chunks to unary waiters)."
            )

        # 建立該請求的訊息佇列
        queue = asyncio.Queue()
        self._message_queues[rid] = queue

        try:
            # 傳送請求
            async with self._lock:
                payload = _e2a_to_wire(envelope)
                logger.info("[WebSocketAgentServerClient] 傳送請求(流式) payload: %s", _to_json(payload))
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

            # 從佇列中接收流式響應
            chunk_count = 0
            saw_complete = False
            while True:
                if saw_complete:
                    try:
                        data = await asyncio.wait_for(
                            queue.get(),
                            timeout=_STREAM_TRAILING_MESSAGE_GRACE_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        break
                else:
                    data = await queue.get()
                logger.info("[WebSocketAgentServerClient] 收到流式事件 raw: %s", json.dumps(data, ensure_ascii=False))
                chunk = parse_agent_server_wire_chunk(data)
                chunk_count += 1
                logger.info(
                    "[WebSocketAgentServerClient] 收到流式 chunk #%s AgentResponseChunk: %s",
                    chunk_count, _to_json(asdict(chunk)),
                )
                yield chunk
                if chunk.is_complete:
                    saw_complete = True
            logger.info("[WebSocketAgentServerClient] 流式響應結束: request_id=%s 共 %s 個 chunk", rid, chunk_count)
        except asyncio.CancelledError:
            logger.info("[WebSocketAgentServerClient] 流式接收被取消: request_id=%s", rid)
            raise
        finally:
            # 清理佇列
            await self._drain_and_remove_queue(rid)

    async def _drain_and_remove_queue(self, rid: str) -> None:
        """清空佇列中的殘餘訊息並移除佇列，同時標記 request_id 為已取消狀態.

        標記為已取消後，後續到達的殘餘訊息會被 _message_receiver_loop 靜默丟棄。
        使用鎖保護，確保操作的原子性。
        """
        async with self._queue_lock:
            queue = self._message_queues.get(rid)
            if queue is None:
                return
            # 1. 先標記為已取消，阻止後續訊息進入佇列
            self._cancelled_request_ids.add(rid)
            # 2. 刪除佇列註冊
            del self._message_queues[rid]
            # 3. 清空佇列中的殘餘訊息（非阻塞）
            drained_count = 0
            while True:
                try:
                    queue.get_nowait()
                    drained_count += 1
                except asyncio.QueueEmpty:
                    break
            logger.debug(
                "[WebSocketAgentServerClient] 佇列已清空並移除: request_id=%s 清理訊息數=%d",
                rid,
                drained_count,
            )
            # 4. 非同步延遲清理已取消標記（給 AgentServer 一點時間傳送殘餘訊息）
            asyncio.create_task(self._delayed_cleanup_cancelled_request_id(rid))

    async def _delayed_cleanup_cancelled_request_id(self, rid: str) -> None:
        """延遲清理已取消的 request_id 標記.

        等待一段時間後清理，確保 AgentServer 的殘餘訊息能夠被靜默丟棄而不列印日誌。
        """
        # 等待足夠時間讓 AgentServer 的殘餘訊息被接收和丟棄
        await asyncio.sleep(2.0)  # 2秒應該足夠
        async with self._queue_lock:
            self._cancelled_request_ids.discard(rid)
            logger.debug(
                "[WebSocketAgentServerClient] 已取消標記已清理: request_id=%s",
                rid,
            )


# ---------------------------------------------------------------------------
# Mock AgentServer（協議相容，供示例或測試使用）
# ---------------------------------------------------------------------------


async def mock_agent_server_handler(ws: Any) -> None:
    """
    協議相容的 Mock AgentServer：按 is_stream 回 E2AResponse 線 JSON（與生產 AgentServer 一致）。
    """
    import websockets

    from jiuwenclaw.common.e2a.wire_codec import (
        encode_agent_chunk_for_wire,
        encode_agent_response_for_wire,
    )

    try:
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            req_id = data.get("request_id", "")
            ch_id = data.get("channel") or data.get("channel_id", "")
            params = data.get("params", {})
            is_stream = data.get("is_stream", False)
            params_str = json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)

            if is_stream:
                for i, part in enumerate(["流式-1 ", "流式-2 ", "流式-3(完)"]):
                    chunk = AgentResponseChunk(
                        request_id=req_id,
                        channel_id=ch_id,
                        payload={"content": part},
                        is_complete=i == 2,
                    )
                    wire = encode_agent_chunk_for_wire(
                        chunk, response_id=req_id, sequence=i
                    )
                    await ws.send(json.dumps(wire, ensure_ascii=False))
            else:
                meta = data.get("metadata") or data.get("channel_context")
                if meta is not None and not isinstance(meta, dict):
                    meta = None
                resp = AgentResponse(
                    request_id=req_id,
                    channel_id=ch_id,
                    ok=True,
                    payload={"content": f"Echo: {params_str}"},
                    metadata=dict(meta) if isinstance(meta, dict) else None,
                )
                wire = encode_agent_response_for_wire(resp, response_id=req_id)
                await ws.send(json.dumps(wire, ensure_ascii=False))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        logger.exception("[MockAgentServer] 處理異常: %s", e)


async def run_mock_agent_server(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> Any:
    """
    啟動 Mock AgentServer（使用 mock_agent_server_handler），監聽 host:port。
    返回 Server，呼叫方需在結束時 server.close(); await server.wait_closed()。
    websockets 14+ 使用 legacy.server.serve，與 legacy 客戶端一致，避免 InvalidMessage。
    """
    try:
        from websockets.legacy.server import serve as legacy_serve
        server = await legacy_serve(mock_agent_server_handler, host, port)
    except ImportError:
        import websockets
        server = await websockets.serve(mock_agent_server_handler, host, port)
    logger.info("[MockAgentServer] 已啟動: ws://%s:%s", host, port)
    return server


# ---------------------------------------------------------------------------
# 自驗證：記憶體 Mock 服務端 + main
# ---------------------------------------------------------------------------


async def _run_verification() -> None:
    """用記憶體 Mock 服務端驗證 WebSocketAgentServerClient 的 connect/send_request/send_request_stream."""
    from jiuwenclaw.common.e2a.gateway_normalize import e2a_from_agent_fields

    port = 18765
    uri = f"ws://127.0.0.1:{port}"
    server = await run_mock_agent_server("127.0.0.1", port)
    logger.info("[main] Mock AgentServer 已啟動: %s", uri)

    client = WebSocketAgentServerClient()
    try:
        await client.connect(uri)

        # 1. 非流式請求
        req1 = e2a_from_agent_fields(
            request_id="req-1",
            channel_id="ch-1",
            session_id="sess-1",
            params={"message": "你好"},
        )
        resp1 = await client.send_request(req1)
        assert resp1.request_id == "req-1"
        assert resp1.ok is True
        assert "Echo:" in str(resp1.payload)
        logger.info("[main] 非流式驗證透過: payload=%s", resp1.payload)

        # 2. 流式請求
        req2 = e2a_from_agent_fields(
            request_id="req-2",
            channel_id="ch-1",
            session_id="sess-1",
            params={"message": "流式測試"},
        )
        chunks = []
        async for ch in client.send_request_stream(req2):
            chunks.append(ch)
        assert len(chunks) == 3
        assert chunks[-1].is_complete
        full_content = "".join(c.payload.get("content", "") for c in chunks if c.payload)
        logger.info("[main] 流式驗證透過: 共 %s 個 chunk, 拼接內容=%r", len(chunks), full_content)
    finally:
        await client.disconnect()
        server.close()
        await server.wait_closed()
    logger.info("[main] 驗證完成，功能正常")


def main() -> None:
    """入口：配置日誌並執行自驗證."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(_run_verification())


if __name__ == "__main__":
    main()
