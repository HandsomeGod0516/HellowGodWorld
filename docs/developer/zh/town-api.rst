小镇 API
========

所有路由由 ``agentsociety2.backend.routers.town`` 挂在 ``/api/v1/town`` 下。

世界
----

.. list-table::
   :header-rows: 1

   * - 方法
     - 路径
     - 返回
   * - ``GET``
     - ``/map``
     - 网格尺寸、格子大小、广场、走廊、房间（矩形、内部、门、中心）、墙格
   * - ``GET``
     - ``/rooms``
     - ``[{id, name, name_en}]``，含广场
   * - ``GET``
     - ``/sprites``
     - 前端可用的角色形象
   * - ``GET``
     - ``/defaults``
     - 新增居民表单的预填值，读自 ``GOD_LLM_*``
   * - ``GET``
     - ``/state``
     - 当前快照与最近事件缓冲

居民
----

.. list-table::
   :header-rows: 1

   * - 方法
     - 路径
     - 说明
   * - ``GET``
     - ``/agents``
     - 配置加实时状态。API Key 以 ``***`` 脱敏。
   * - ``POST``
     - ``/agents``
     - 先测端点，失败返回 ``400``。传 ``skip_connection_test: true`` 可跳过。
   * - ``POST``
     - ``/agents/test-connection``
     - 只测端点，不创建任何东西。
   * - ``PATCH``
     - ``/agents/{id}``
     - 部分更新。不传 ``api_key``（或传 ``***``）表示保留原 Key。会重启这个居民的循环。
   * - ``DELETE``
     - ``/agents/{id}``
     - 移除居民并立刻取消它的任务。
   * - ``POST``
     - ``/agents/{id}/goto``
     - 你亲自把某个角色派去某个房间。
   * - ``POST``
     - ``/say``
     - 让某个角色说话。

WebSocket
---------

``GET /api/v1/town/ws``

服务端到客户端：

.. list-table::
   :header-rows: 1

   * - 类型
     - 内容
   * - ``map``
     - 连接时推一次
   * - ``snapshot``
     - ``{tick, actors: [...]}``，10 Hz
   * - ``events``
     - 最近事件缓冲，连接时推一次
   * - ``event``
     - 单条 ``say`` / ``join`` / ``leave`` / ``arrive`` 事件
   * - ``agent_list``
     - 连接时以及居民发生变化时推送
   * - ``joined``
     - ``join`` 成功后返回 ``{actor_id}``

客户端到服务端：

.. list-table::
   :header-rows: 1

   * - 类型
     - 内容
   * - ``join``
     - ``{name}`` —— 创建一个绑定在这条连接上的人类角色
   * - ``input``
     - ``{dir: "up"|"down"|"left"|"right"|null}`` —— 当前按住的方向
   * - ``say``
     - ``{text}``
   * - ``leave``
     - 移除人类角色

连接关闭时人类角色会被清掉。

测端点
------

.. code-block:: bash

   curl -s localhost:8001/api/v1/town/agents/test-connection \
     -X POST -H 'Content-Type: application/json' \
     -d '{"provider":"ollama","base_url":"http://localhost:11434","model":"qwen2.5:7b"}'

成功的响应带 ``ok``、``latency_ms``、端点上报的模型列表和样例回复。失败则带一条人类可读的 ``error``。
