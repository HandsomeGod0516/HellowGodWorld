"""Backend API 模組 - 提供 FastAPI 後端服務。

本模組為 AI Social Scientist VSCode 擴充套件提供 HTTP API 服務。

路由模組
--------

- **prefill_params**: 引數預填充 API
- **experiments**: 實驗管理 API
- **replay**: 回放資料 API
- **custom**: 自定義模組掃描 API
- **modules**: 模組註冊 API
- **agent_skills**: Agent Skills 管理 API

啟動服務::

    python -m agentsociety2.backend.app

或::

    uvicorn agentsociety2.backend.app:app --host 0.0.0.0 --port 8001
"""
