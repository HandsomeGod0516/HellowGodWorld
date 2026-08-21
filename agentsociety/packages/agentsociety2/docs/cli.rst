命令列介面
========================================

AgentSociety 2 提供了一個強大的命令列介面（CLI）用於執行實驗。

概述
------------

CLI 是執行 AgentSociety 2 實驗的主要方式。它提供：

* 實驗配置載入和驗證
* 步驟化執行跟蹤
* 進度持久化（pid.json）
* 靈活的日誌配置
* 後臺執行支援

基本用法
------------

.. code-block:: bash

   python -m agentsociety2.society.cli [OPTIONS]

必需引數
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - 引數
     - 說明
   * - ``--config`` <PATH>
     - 初始化配置檔案路徑（init_config.json）
   * - ``--steps`` <PATH>
     - 步驟配置檔案路徑（steps.yaml）

可選引數
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - 引數
     - 預設值
     - 說明
   * - ``--run-dir`` <PATH>
     - 當前目錄
     - 執行輸出目錄路徑
   * - ``--experiment-id`` <TEXT>
     - 無
     - 實驗識別符號
   * - ``--log-level``
     - INFO
     - 日誌級別：DEBUG, INFO, WARNING, ERROR, CRITICAL
   * - ``--log-file`` <PATH>
     - 無
     - 日誌檔案路徑（**後臺執行必需**）

執行實驗
------------

前臺執行（除錯模式）
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config hypothesis_1/experiment_1/init/init_config.json \
       --steps hypothesis_1/experiment_1/init/steps.yaml \
       --run-dir hypothesis_1/experiment_1/run \
       --log-level DEBUG

日誌輸出到控制檯。

後臺執行（生產模式）
~~~~~~~~~~~~~~~~~~~~~~~~~~

**重要**: 後臺執行時必須指定 ``--log-file`` 以捕獲日誌。

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config hypothesis_1/experiment_1/init/init_config.json \
       --steps hypothesis_1/experiment_1/init/steps.yaml \
       --run-dir hypothesis_1/experiment_1/run \
       --experiment-id "1_1" \
       --log-level INFO \
       --log-file hypothesis_1/experiment_1/run/output.log &

檢查實驗狀態
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # 檢查 pid.json 檢視執行狀態
   cat hypothesis_1/experiment_1/run/pid.json

   # 檢視日誌
   tail -f hypothesis_1/experiment_1/run/output.log

停止實驗
~~~~~~~~~~~~~

.. code-block:: bash

   # 查詢程序 ID
   pid=$(jq -r '.pid' hypothesis_1/experiment_1/run/pid.json)

   # 傳送 SIGTERM 訊號
   kill $pid

配置檔案
------------

init_config.json
~~~~~~~~~~~~~~~~~~~~

初始化配置檔案定義實驗的基本設定：

.. code-block:: json

   {
       "agents": [
           {
               "id": 1,
               "profile": {
                   "name": "Alice",
                   "personality": "friendly"
               }
           }
       ],
       "env_modules": [
           {
               "module": "agentsociety2.contrib.env:SimpleSocialSpace",
               "config": {}
           }
       ],
       "env_router": "agentsociety2.env:CodeGenRouter",
       "storage": {
           "db_path": "experiment.db"
       }
   }

steps.yaml
~~~~~~~~~~~~~

步驟配置檔案定義實驗的執行步驟：

.. code-block:: yaml

   steps:
     - type: ask
       content: "Introduce yourself to the group"
       save_artifact: true

     - type: step
       tick: 3600

     - type: intervene
       content: "Make everyone feel better"
       save_artifact: true

步驟型別
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - 型別
     - 說明
   * - ``ask``
     - 只讀查詢，不修改環境
   * - ``intervene``
     - 讀寫操作，可以修改環境
   * - ``step``
     - 執行一個模擬步驟（指定 tick 時長）

輸出檔案
------------

執行實驗後，``run-dir`` 目錄將包含：

.. code-block:: text

   hypothesis_1/experiment_1/run/
   ├── pid.json              # 程序資訊（PID、啟動時間、狀態）
   ├── output.log            # 日誌檔案（如果指定了 --log-file）
   ├── experiment.db         # SQLite 資料庫
   └── artifacts/            # 步驟產物（如果啟用了 save_artifact）
       ├── step_1_ask.json
       ├── step_2_intervene.json
       └── ...

pid.json 格式
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
       "pid": 12345,
       "start_time": "2026-03-20T10:30:00",
       "status": "running",
       "config": {
           "config_path": "/path/to/init_config.json",
           "steps_path": "/path/to/steps.yaml"
       }
   }

遙測配置
------------

AgentSociety 2 自動禁用所有遙測服務以防止外部連線：

* ``MEM0_TELEMETRY=False`` - 禁用 mem0 遙測
* ``ANONYMIZED_TELEMETRY=False`` - 禁用 ChromaDB/Posthog 遙測

這些設定在 CLI 啟動時強制執行，無需手動配置。

日誌級別
------------

可選的日誌級別：

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - 級別
     - 用途
   * - ``DEBUG``
     - 詳細的除錯資訊，包括 LLM 呼叫
   * - ``INFO``
     - 常規執行資訊（預設）
   * - ``WARNING``
     - 警告資訊
   * - ``ERROR``
     - 錯誤資訊
   * - ``CRITICAL``
     - 嚴重錯誤

示例：完整工作流
------------------------

.. code-block:: bash

   # 1. 準備配置檔案
   mkdir -p my_experiment/init my_experiment/run
   # ... 建立 init_config.json 和 steps.yaml ...

   # 2. 前臺測試執行
   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --log-level DEBUG

   # 3. 後臺生產執行
   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --experiment-id "exp_001" \
       --log-level INFO \
       --log-file my_experiment/run/output.log &

   # 4. 監控執行
   tail -f my_experiment/run/output.log

   # 5. 完成後分析結果
   sqlite3 my_experiment/run/sqlite.db "SELECT dataset_id, table_name FROM replay_dataset_catalog;"
