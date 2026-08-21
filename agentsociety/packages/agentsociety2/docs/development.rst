開發指南
=================

本指南面向 AgentSociety 2 的貢獻者。

設定開發環境
-----------------------------------

1. Fork 並克隆倉庫：

.. code-block:: bash

   git clone https://github.com/your-username/agentsociety.git
   cd agentsociety

2. 以開發模式安裝：

.. code-block:: bash

   # Using uv (recommended)
   uv sync

   # Or using pip
   pip install -e "packages/agentsociety2[dev]"

3. 安裝 pre-commit hooks：

.. code-block:: bash

   cd packages/agentsociety2
   pre-commit install

執行測試
-------------

.. code-block:: bash

   cd packages/agentsociety2
   pytest

使用覆蓋率：

.. code-block:: bash

   pytest --cov=agentsociety2 --cov-report=html

程式碼風格
----------

AgentSociety 2 使用 `ruff`_ 進行檢查和格式化：

.. code-block:: bash

   # Check code
   ruff check .

   # Format code
   ruff format .

我們還使用 `mypy`_ 進行型別檢查：

.. code-block:: bash

   mypy agentsociety2/

.. _ruff: https://github.com/astral-sh/ruff
.. _mypy: https://github.com/python/mypy

專案結構
-----------------

.. code-block:: text

   agentsociety2/
   ├── agent/           # Agent implementations
   ├── backend/         # FastAPI backend service (REST API)
   ├── code_executor/   # Code execution in Docker
   ├── config/          # Configuration and LLM routing
   ├── contrib/         # Contributed agents and environments
   ├── custom/          # Custom module templates
   ├── env/             # Environment modules and routers
   ├── logger/          # Enhanced logging with file support
   ├── registry/        # Module registry for custom components
   ├── skills/          # Research skills (literature, experiment, hypothesis, etc.)
   ├── society/         # Society helper utilities and CLI
   └── storage/         # Replay storage system

研究技能模組
-------------------------

``skills/`` 模組提供 LLM 原生的研究工作流：

* **literature**: 學術文獻搜尋和管理
* **experiment**: 實驗配置和執行
* **hypothesis**: 假設生成和管理
* **web_research**: 使用 Miro 進行網路研究
* **paper**: 使用 EasyPaper 生成學術論文
* **analysis**: 資料分析和報告
* **agent**: 智慧體處理、選擇、生成和過濾

後端 API
-------------------------

``backend/`` 模組提供 FastAPI REST API：

* **Routers**:
  * ``/api/v1/experiments`` - 實驗管理
  * ``/api/v1/modules`` - 模組管理
  * ``/api/v1/replay`` - 回放資料訪問
  * ``/api/v1/custom`` - 自定義模組註冊

* **Service Layer**: 業務邏輯層

啟動後端：

.. code-block:: bash

   python -m agentsociety2.backend.run

   # 訪問 http://localhost:8001/docs 檢視 API 文件

日誌系統
-------------------------

增強的日誌系統支援：

* **彩色控制檯輸出**: 按日誌級別著色
* **檔案日誌**: 使用 ``add_file_handler()`` 寫入日誌檔案
* **LiteLLM 整合**: 自定義回撥日誌記錄 LLM 呼叫

.. code-block:: python

   from agentsociety2.logger import get_logger, set_logger_level, add_file_handler

   # 設定日誌級別
   set_logger_level("DEBUG")

   # 新增檔案處理器
   add_file_handler("output.log", level="INFO")

   logger = get_logger()
   logger.info("Hello, AgentSociety!")

貢獻
------------

請參閱 :doc:`contributing` 瞭解貢獻指南。

構建文件
----------------------

.. code-block:: bash

   cd packages/agentsociety2/docs
   make html

構建的文件將位於 ``_build/html/``。

要在編輯時進行實時預覽：

.. code-block:: bash

   make livehtml

釋出流程
---------------

1. 更新 ``pyproject.toml`` 中的版本
2. 更新 ``CHANGELOG.md``
3. 提交更改
4. 建立標籤：``git tag agentsociety2-vX.Y.Z``
5. 推送：``git push --tags``
6. GitHub Actions 將構建併發布到 PyPI
