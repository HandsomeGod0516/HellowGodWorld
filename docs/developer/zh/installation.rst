安装
====

环境要求
--------

GOD 目前需要：

- macOS/Linux：Python 3.11 或更新版本、Node.js 与 ``npm``、``uv``、以及 ``screen``。
- Windows：PowerShell 5.1+ 与 ``winget``。PowerShell 入口会自动安装缺失的 Git、Node.js LTS/npm 和 ``uv``；Python 运行时由 ``uv`` 提供。
- 一个给居民用的模型端点。本地 `Ollama <https://ollama.com>`_ 最简单，任何 OpenAI 兼容服务也可以。

macOS：

.. code-block:: bash

   brew install python node uv screen

克隆
----

.. code-block:: bash

   git clone https://github.com/XiaoLuoLYG/GOD.git
   cd GOD

用启动来完成安装
----------------

推荐的安装方式就是启动方式：

.. code-block:: bash

   ./scripts/god.sh start

Windows PowerShell 用：

.. code-block:: powershell

   .\scripts\god.cmd start

首次运行时脚本会从 ``.env.example`` 生成 ``.env``、安装后端与控制台依赖、拉起两个服务并打印控制台地址。``.env`` 里没有必填项 —— 居民自带模型配置。

只装依赖
--------

只检查/安装依赖，不启动服务：

.. code-block:: bash

   ./scripts/god.sh setup

依赖已就绪、想跳过检查：

.. code-block:: bash

   GOD_SKIP_SETUP=1 ./scripts/god.sh start

只在本地的文件
--------------

不要提交本地运行状态：

- ``.env``
- ``.god/``（日志、pid、以及 ``town/agents.json``）
- ``.live/``
