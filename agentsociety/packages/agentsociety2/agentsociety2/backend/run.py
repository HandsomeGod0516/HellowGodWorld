#!/usr/bin/env python
"""
啟動後端服務的便捷指令碼

關聯檔案：
- @packages/agentsociety2/agentsociety2/backend/app.py - FastAPI應用主入口
- @extension/src/services/backendManager.ts - VSCode外掛後端管理器（呼叫此指令碼啟動）

環境變數（.env）：
- BACKEND_HOST - 服務監聽地址（預設: 0.0.0.0）
- BACKEND_PORT - 服務監聽埠（預設: 8001）
- BACKEND_LOG_LEVEL - 日誌等級（預設: info）
"""

if __name__ == "__main__":
    import uvicorn
    import os
    import argparse
    from dotenv import load_dotenv

    # 解析命令列引數
    parser = argparse.ArgumentParser(
        description="啟動 AI Social Scientist Backend API 服務"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="設定日誌等級 (critical, error, warning, info, debug, trace)",
    )
    args = parser.parse_args()

    # 載入環境變數檔案
    load_dotenv()

    # 從環境變數讀取配置，命令列引數優先
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8001"))
    log_level = args.log_level or os.getenv("BACKEND_LOG_LEVEL", "info")

    # 如果命令列引數設定了日誌等級，更新環境變數以便 app.py 使用
    if args.log_level:
        os.environ["BACKEND_LOG_LEVEL"] = args.log_level

    print("啟動 AI Social Scientist Backend API 服務...")
    print(f"服務地址: http://{host}:{port}")
    print(f"API文件: http://{host}:{port}/docs")
    print(f"健康檢查: http://{host}:{port}/health")
    print(f"日誌等級: {log_level}")
    print("-" * 60)

    uvicorn.run(
        "agentsociety2.backend.app:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level,
        ws="wsproto",
    )
