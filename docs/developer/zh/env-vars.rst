环境变量
========

GOD 只读一小组 ``GOD_*`` 变量。其它过去全局配置的东西，现在都挂在各个居民身上。

启动与端口
----------

.. list-table::
   :header-rows: 1

   * - 变量
     - 用途
     - 默认值
   * - ``GOD_BACKEND_HOST``
     - 后端绑定地址
     - ``127.0.0.1``
   * - ``GOD_BACKEND_PORT``
     - 后端端口
     - ``8001``
   * - ``GOD_FRONTEND_PORT``
     - 控制台端口
     - ``5174``
   * - ``GOD_SKIP_SETUP``
     - 启动时跳过依赖安装/检查
     - ``0``
   * - ``GOD_FORCE_SETUP``
     - 即使依赖看起来已装也重新同步
     - ``0``
   * - ``GOD_ENV_FILE``
     - 另一个 ``.env`` 位置
     - ``<repo>/.env``
   * - ``GOD_STATE_DIR``
     - ``logs/``、``pids/``、``town/agents.json`` 所在目录
     - ``<repo>/.god``
   * - ``BACKEND_LOG_LEVEL``
     - 后端日志等级
     - ``info``

表单预填
--------

.. list-table::
   :header-rows: 1

   * - 变量
     - 用途
     - 默认值
   * - ``GOD_LLM_PROVIDER``
     - ``ollama`` 或 ``openai``
     - ``ollama``
   * - ``GOD_LLM_API_BASE``
     - 预填的 API 地址
     - ``http://localhost:11434``
   * - ``GOD_LLM_MODEL``
     - 预填的模型名
     - 空
   * - ``GOD_LLM_API_KEY``
     - 有值即表示已有可用 Key
     - 空

这些由 ``GET /api/v1/town/defaults`` 暴露，只影响新增居民表单。居民永远用存在它自己身上的端点。

前端服务
--------

.. list-table::
   :header-rows: 1

   * - 变量
     - 用途
   * - ``VITE_BASE``
     - ``/`` 或 ``/proxy/<port>/``。在 code-server 下 ``scripts/god.sh`` 会自动设置。
   * - ``VITE_HOST``
     - Vite 绑定地址。启用路径代理 base 时会自动设为 ``0.0.0.0``。
   * - ``VITE_HMR_PROTOCOL`` / ``VITE_HMR_CLIENT_PORT``
     - 从 ``VSCODE_PROXY_URI`` 推导出的 HMR 设置。
