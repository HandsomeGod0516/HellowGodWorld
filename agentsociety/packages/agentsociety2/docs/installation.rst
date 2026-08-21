安装
============

系统要求
------------

AgentSociety 2 需要 Python 3.11 或更高版本。

从 PyPI 安装
-----------------

最简单的安装 AgentSociety 2 的方法是使用 pip：

.. code-block:: bash

   pip install agentsociety2

这将安装核心包。如果要安装开发依赖：

.. code-block:: bash

   pip install "agentsociety2[dev]"

对于文档依赖：

.. code-block:: bash

   pip install "agentsociety2[docs]"

安装所有内容：

.. code-block:: bash

   pip install "agentsociety2[all]"

从源码安装
-------------------

要从最新的源代码安装：

.. code-block:: bash

   git clone https://github.com/tsinghua-fib-lab/agentsociety.git
   cd agentsociety/packages/agentsociety2
   pip install -e .

验证安装
-------------------

要验证您的安装，运行：

.. code-block:: python

   import agentsociety2
   print(agentsociety2.__version__)

您应该能看到版本号被打印出来。

配置
-------------

AgentSociety 2 需要 LLM API 凭证。设置以下环境变量：

**必需配置**

.. code-block:: bash

   # Default LLM (Required - for most operations)
   export AGENTSOCIETY_LLM_API_KEY="your-api-key"
   export AGENTSOCIETY_LLM_API_BASE="https://api.openai.com/v1"
   export AGENTSOCIETY_LLM_MODEL="gpt-5.4"

**可选配置**

对于专门的任务，您可以配置单独的 LLM 实例。如果未设置这些选项，
它们将回退到默认 LLM 配置：

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

**数据目录**

.. code-block:: bash

   # Directory for storing agent data, memory and persistence files
   # Default: ./agentsociety_data
   export AGENTSOCIETY_HOME_DIR="/path/to/your/data"

**使用 .env 文件**

您也可以在项目目录中创建 ``.env`` 文件：

.. code-block:: bash

   # 推荐：从仓库模板复制（若你在源码仓库内）
   cp .env.example .env
   # 然后编辑 .env 填入 API Key

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

   **环境变量区分**：

   - ``AGENTSOCIETY_LLM_*``: 全局 LLM API 配置，用于模型调用
   - ``AGENT_*``: Agent 行为配置，如上下文窗口大小、工具循环轮数等

**遥测设置**

AgentSociety 2 会自动禁用所有遥测服务以防止外部连接：

.. code-block:: bash

   # 以下设置由框架自动配置，无需手动设置
   MEM0_TELEMETRY=False
   ANONYMIZED_TELEMETRY=False

这些设置禁用了 mem0 和 ChromaDB 的遥测功能，防止连接到 Posthog/Facebook 等外部服务。

支持的 LLM 提供商
------------------------

AgentSociety 2 使用 `litellm`_，支持许多 LLM 提供商：

- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude)
- Azure OpenAI
- Google (Gemini)
- Cohere
- 以及更多...

查看 `litellm 文档`_ 获取完整列表。

.. _litellm: https://github.com/BerriAI/litellm
.. _litellm 文档: https://docs.litellm.ai/
