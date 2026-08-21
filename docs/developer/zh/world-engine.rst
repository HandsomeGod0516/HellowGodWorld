世界引擎
========

世界引擎在 ``agentsociety/packages/agentsociety2/agentsociety2/town/``。它不依赖 AgentSociety 的 experiment、tick 或 replay 生命周期。

模块
----

``map_layout.py``
   以常量表形式定义的固定地图。``build_world_map()`` 返回前端要画的东西；``walkable_tiles()`` 返回碰撞网格；``room_of()`` 与 ``room_anchor()`` 在格子与房间之间来回换算。

``pathfind.py``
   ``astar(start, goal, walkable)``，四方向邻居；``nearest_walkable()`` 把网格外的点吸附回来。

``llm_client.py``
   按 Agent 独立的 HTTP 客户端。``chat()`` 同时支持 Ollama 的 ``/api/chat`` 与 OpenAI 兼容的 ``/chat/completions``。``test_endpoint()`` 先列模型、确认目标模型存在，再发一次 8 token 的对话。``extract_json_object()`` 从啰嗦的回复里抠出第一个 JSON 对象。

``agents.py``
   ``TownAgentConfig`` 是持久化结构；``TownActor`` 是运行时实体，AI 居民与人类玩家共用。``run_agent_loop()`` 是一个居民的自主循环；``build_observation()`` 与 ``build_system_prompt()`` 决定它的模型看到什么。

``world.py``
   ``WorldEngine`` 持有所有角色、跑移动循环、判定谁能听见谁、管理 WebSocket 订阅者。``add_agent()`` / ``remove_agent()`` 通过起停任务实现热插拔。

``store.py``
   读写 ``.god/town/agents.json``，支持 ``GOD_STATE_DIR`` 覆盖。

时序
----

.. list-table::
   :header-rows: 1

   * - 常量
     - 值
     - 含义
   * - ``TICK_HZ``
     - 20
     - 每秒移动推进次数
   * - ``BROADCAST_HZ``
     - 10
     - 每秒推送的快照数
   * - ``TILES_PER_SECOND``
     - 3.6
     - 行走速度
   * - ``NEARBY_RADIUS_TILES``
     - 8
     - 视野与听觉半径
   * - ``decision_interval_s``
     - 按居民，默认 8
     - 一个居民多久调一次自己的模型

决策契约
--------

居民的模型被要求输出一个 JSON 对象：

.. code-block:: json

   {"action": "goto|say|idle", "room": "cafe", "text": "...", "reason": "..."}

- ``goto`` 会跑 A* 到房间中心，世界循环把它走过去。
- ``say`` 会冒出一个气泡，并把这句话追加到附近所有人的 ``heard`` 缓冲。
- ``idle`` 让它留在原地。

未知房间、解析不出来的回复、超时、端点挂掉，处理方式都一样：错误写到这个居民的卡片上，``WorldEngine.wander()`` 送它去一个随机房间。世界循环绝不会被一个慢模型卡住。

加一个房间
----------

在 ``map_layout.py`` 的 ``ROOMS`` 里追加一条，写上它的矩形、门格和中心点，再加一条走廊矩形把门连到广场或已有走廊。重启后端，前端就会拿到新布局，不需要处理任何资源。如果新房间走不通，``tests/test_town.py`` 会失败。
