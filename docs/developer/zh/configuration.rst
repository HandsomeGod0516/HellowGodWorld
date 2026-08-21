配置
====

GOD 把机器设置放在 ``.env``，把模型设置放在每个居民自己身上。

``.env``
--------

``.env`` 从 ``.env.example`` 生成，并被 Git 忽略。里面没有启动必填项。

.. list-table::
   :header-rows: 1

   * - 变量
     - 用途
     - 默认值
   * - ``GOD_LLM_PROVIDER``
     - 预填新增居民表单的接口类型（``ollama`` 或 ``openai``）
     - ``ollama``
   * - ``GOD_LLM_API_BASE``
     - 预填 API 地址
     - ``http://localhost:11434``
   * - ``GOD_LLM_MODEL``
     - 预填模型名
     - 空
   * - ``GOD_LLM_API_KEY``
     - 标记表单已有可用的 Key
     - 空
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
     - 启动时跳过依赖检查
     - ``0``

这些值只用来预填表单。居民实际使用的端点存在它自己身上。

``.god/town/agents.json``
-------------------------

你加的每个居民都写在这里：名字、形象、人物设定、初始房间、端点（含 API Key）、决策间隔、是否暂停。后端启动时读这个文件，把所有人放回小镇。

清空它：

.. code-block:: bash

   ./scripts/god.sh reset

改地图
------

地图就是 ``agentsociety2/town/map_layout.py`` 里的常量表。改 ``ROOMS``、``PLAZA`` 或 ``CORRIDORS``，重启后端并刷新页面 —— 前端画的就是后端报上来的东西。改完记得跑测试，``test_town.py`` 会断言每个房间仍然能走到广场。

路径代理下的访问
----------------

在 code-server 下控制台是从 ``/proxy/<port>/`` 提供的。``scripts/god.sh`` 会检测并设置 ``VITE_BASE``；检测失败时用 ``VITE_BASE=/proxy/5174/`` 显式指定。
