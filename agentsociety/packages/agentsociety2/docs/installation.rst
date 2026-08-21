安裝
============

系統要求
------------

AgentSociety 2 需要 Python 3.11 或更高版本。

從 PyPI 安裝
-----------------

最簡單的安裝 AgentSociety 2 的方法是使用 pip：

.. code-block:: bash

   pip install agentsociety2

這將安裝核心包。如果要安裝開發依賴：

.. code-block:: bash

   pip install "agentsociety2[dev]"

對於文件依賴：

.. code-block:: bash

   pip install "agentsociety2[docs]"

安裝所有內容：

.. code-block:: bash

   pip install "agentsociety2[all]"

從原始碼安裝
-------------------

要從最新的原始碼安裝：

.. code-block:: bash

   git clone https://github.com/tsinghua-fib-lab/agentsociety.git
   cd agentsociety/packages/agentsociety2
   pip install -e .

驗證安裝
-------------------

要驗證您的安裝，執行：

.. code-block:: python

   import agentsociety2
   print(agentsociety2.__version__)

您應該能看到版本號被列印出來。

配置
-------------

AgentSociety 2 需要 LLM API 憑證。設定以下環境變數：

**必需配置**

.. code-block:: bash

   # Default LLM (Required - for most operations)
   export AGENTSOCIETY_LLM_API_KEY="your-api-key"
   export AGENTSOCIETY_LLM_API_BASE="https://api.openai.com/v1"
   export AGENTSOCIETY_LLM_MODEL="gpt-5.4"

**可選配置**

對於專門的任務，您可以配置單獨的 LLM 例項。如果未設定這些選項，
它們將回退到預設 LLM 配置：

.. code-block:: bash

   # Coder LLM (for code-related tasks)
   # Falls back to: AGENTSOCIETY_LLM_API_KEY, AGENTSOCIETY_LLM_API_BASE
   export AGENTSOCIETY_CODER_LLM_API_KEY="your-coder-api-key"      # Optional
   export AGENTSOCIETY_CODER_LLM_API_BASE="https://api.openai.com/v1"  # Optional
   export AGENTSOCIETY_CODER_LLM_MODEL="gpt-5.4"                    # Optional

   # Nano LLM (for high-frequency, low-latency operations)
   # Falls back to: AGENTSOCIETY_LLM_API_KEY, AGENTSOCIETY_LLM_API_BASE
   export AGENTSOCIETY_NANO_LLM_API_KEY="your-nano-api-key"        # Optional
   export AGENTSOCIETY_NANO_LLM_API_BASE="https://api.openai.com/v1"  # Optional
   export AGENTSOCIETY_NANO_LLM_MODEL="gpt-5.4-nano"                # Optional

   # Embedding model (for text embedding and semantic search)
   # Falls back to: AGENTSOCIETY_LLM_API_KEY, AGENTSOCIETY_LLM_API_BASE
   export AGENTSOCIETY_EMBEDDING_API_KEY="your-embedding-api-key"  # Optional
   export AGENTSOCIETY_EMBEDDING_API_BASE="https://api.openai.com/v1"  # Optional
   export AGENTSOCIETY_EMBEDDING_MODEL="text-embedding-3-large"   # Optional
   export AGENTSOCIETY_EMBEDDING_DIMS="1024"                      # Optional

**資料目錄**

.. code-block:: bash

   # Directory for storing agent data, memory and persistence files
   # Default: ./agentsociety_data
   export AGENTSOCIETY_HOME_DIR="/path/to/your/data"

**使用 .env 檔案**

您也可以在專案目錄中建立 ``.env`` 檔案：

.. code-block:: bash

   # 推薦：從倉庫模板複製（若你在原始碼倉庫內）
   cp .env.example .env
   # 然後編輯 .env 填入 API Key

.. code-block:: bash

   # Required - LLM API Configuration
   AGENTSOCIETY_LLM_API_KEY=your-api-key
   AGENTSOCIETY_LLM_API_BASE=https://api.openai.com/v1
   AGENTSOCIETY_LLM_MODEL=gpt-5.4

   # Optional - Agent Behavior Configuration
   AGENT_MODEL=gpt-5.4              # Override model for agents
   AGENT_CONTEXT_WINDOW=200000          # Model context window
   AGENT_MAX_TOOL_ROUNDS=24             # Max tool loop rounds

   # Optional - Specialized LLM instances (fallback to default)
   AGENTSOCIETY_CODER_LLM_MODEL=gpt-5.4
   AGENTSOCIETY_NANO_LLM_MODEL=gpt-5.4-nano
   AGENTSOCIETY_EMBEDDING_MODEL=text-embedding-3-large
   AGENTSOCIETY_EMBEDDING_DIMS=1024
   AGENTSOCIETY_HOME_DIR=./agentsociety_data

.. note::

   **環境變數區分**：

   - ``AGENTSOCIETY_LLM_*``: 全域性 LLM API 配置，用於模型呼叫
   - ``AGENT_*``: Agent 行為配置，如上下文視窗大小、工具迴圈輪數等

**遙測設定**

AgentSociety 2 會自動禁用所有遙測服務以防止外部連線：

.. code-block:: bash

   # 以下設定由框架自動配置，無需手動設定
   MEM0_TELEMETRY=False
   ANONYMIZED_TELEMETRY=False

這些設定禁用了 mem0 和 ChromaDB 的遙測功能，防止連線到 Posthog/Facebook 等外部服務。

支援的 LLM 提供商
------------------------

AgentSociety 2 使用 `litellm`_，支援許多 LLM 提供商：

- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude)
- Azure OpenAI
- Google (Gemini)
- Cohere
- 以及更多...

檢視 `litellm 文件`_ 獲取完整列表。

.. _litellm: https://github.com/BerriAI/litellm
.. _litellm 文件: https://docs.litellm.ai/
