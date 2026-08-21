# ruff: noqa: E402

"""
FastAPI backend service for GOD.

關聯檔案：
- @packages/agentsociety2/agentsociety2/backend/run.py - 服務啟動指令碼
- @extension/src/services/backendManager.ts - VSCode外掛後端程序管理
- @extension/src/apiClient.ts - VSCode外掛API客戶端

路由註冊：
- @packages/agentsociety2/agentsociety2/backend/routers/town.py - /api/v1/town（即時小鎮）
- @packages/agentsociety2/agentsociety2/backend/routers/prefill_params.py - /api/v1/prefill-params
- @packages/agentsociety2/agentsociety2/backend/routers/custom.py - /api/v1/custom
- @packages/agentsociety2/agentsociety2/backend/routers/modules.py - /api/v1/modules
- @packages/agentsociety2/agentsociety2/backend/routers/agent_skills.py - /api/v1/agent-skills
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from agentsociety2.backend.routers import (
    prefill_params,
    custom,
    modules,
    agent_skills,
    town,
)

# 載入環境變數
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")


# 配置標準 logging
def _setup_logging():
    """配置後端服務日誌。

    讀取環境變數 ``BACKEND_LOG_LEVEL``，並初始化 root logger 與相關模組 logger。
    """
    log_level = os.getenv("BACKEND_LOG_LEVEL", "info")
    # 將 uvicorn 的 "trace" 對映到 Python logging 的 "DEBUG"
    python_log_level = "DEBUG" if log_level.lower() == "trace" else log_level.upper()
    level = getattr(logging, python_log_level, logging.INFO)

    # 配置根 logger（如果還沒有配置過）
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            force=True,  # Python 3.8+ 支援，強制重新配置
        )
    else:
        # 如果已經配置過，只更新日誌等級
        root_logger.setLevel(level)

    # 設定 agentsociety2 相關模組的日誌等級
    agentsociety_logger = logging.getLogger("agentsociety2")
    agentsociety_logger.setLevel(level)

    return agentsociety_logger


_setup_logging()
from agentsociety2.logger import get_logger

logger = get_logger()

APP_TITLE = "GOD Backend API"
APP_DESCRIPTION = (
    "Backend API service for the GOD live pixel town: world state, AI residents, and player input."
)
APP_VERSION = "0.2.0"


def _split_csv_env(value: str | None) -> list[str]:
    return [item.strip().rstrip("/") for item in (value or "").split(",") if item.strip()]


def _cors_allow_origins() -> list[str]:
    configured = _split_csv_env(os.getenv("GOD_CORS_ALLOW_ORIGINS"))
    if configured:
        return configured

    frontend_port = (
        os.getenv("GOD_FRONTEND_PORT")
        or os.getenv("AGENTSOCIETY_FRONTEND_PORT")
        or "5174"
    )
    return [
        f"http://127.0.0.1:{frontend_port}",
        f"http://localhost:{frontend_port}",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 應用生命週期管理（啟動/關閉鉤子）。"""
    # 啟動時執行
    logger.info("GOD Backend Service 啟動中...")
    logger.info(f"專案根目錄: {_project_root}")

    # 即時小鎮的世界迴圈隨後端一起常駐執行。
    await town.bootstrap_world()

    yield

    # 關閉時執行
    await town.shutdown_world()
    logger.info("GOD Backend Service 關閉中...")


# 建立FastAPI應用
app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)

# 配置 CORS：credentials 不能安全地配合 "*"，釋出預設只開放本地控制檯。
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# 註冊路由（僅保留必要的API）
app.include_router(prefill_params.router)
app.include_router(custom.router)
app.include_router(modules.router)
app.include_router(agent_skills.router)
app.include_router(town.router)


@app.get("/")
async def root():
    """:returns: 後端服務基本資訊與 endpoints 列表。"""
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "running",
        "endpoints": {
            "town": "/api/v1/town/*",
            "prefill_params": "/api/v1/prefill-params",
            "custom": "/api/v1/custom/*",
            "modules": "/api/v1/modules/*",
            "agent_skills": "/api/v1/agent-skills/*",
        },
    }


@app.get("/health")
async def health_check():
    """:returns: 健康狀態。"""
    return {"status": "healthy"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全域性異常處理器。

    :param request: FastAPI 請求物件（用於擴充套件日誌上下文）。
    :param exc: 未捕獲異常。
    :returns: 標準化的 500 JSON 響應。
    """
    logger.error(f"未處理的異常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal Server Error",
            "detail": str(exc),
        },
    )


if __name__ == "__main__":
    import uvicorn
    import argparse

    # 解析命令列引數
    parser = argparse.ArgumentParser(
        description="啟動 GOD Backend API 服務"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="設定日誌等級 (critical, error, warning, info, debug, trace)",
    )
    args = parser.parse_args()

    # 從環境變數讀取配置，命令列引數優先
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8001"))
    log_level = args.log_level or os.getenv("BACKEND_LOG_LEVEL", "info")

    # 如果命令列引數設定了日誌等級，更新環境變數並重新配置日誌
    if args.log_level:
        os.environ["BACKEND_LOG_LEVEL"] = args.log_level
        _setup_logging()

    logger.info(f"啟動伺服器: http://{host}:{port}")
    logger.info(f"日誌等級: {log_level}")
    uvicorn.run(
        "agentsociety2.backend.app:app",
        host=host,
        port=port,
        reload=False,  # 生產環境設為False
        log_level=log_level,
        ws="wsproto",
    )
