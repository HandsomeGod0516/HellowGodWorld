概览
====

GOD 是 Govern、Observe、Direct 的缩写。它在你的机器上跑一座小小的像素小镇，往里放 AI 居民，你也可以自己走进去跟它们说话。

GOD 是什么
----------

- 一个 **世界循环**：以 20 Hz 推进所有角色，以 10 Hz 广播快照。后端一启动它就在跑，不需要任何东西来触发一步。
- 一张 **固定地图**，由代码生成：中央广场加四周六个房间。没有图集、没有地图包、没有生成步骤。
- **按居民独立的模型端点。** 每个 AI 自带接口类型、API 地址、模型、API Key、随机度和决策间隔。一个居民可以跑本地 Ollama，另一个同时对接远端的 OpenAI 兼容服务。
- **热插拔的居民。** 新加的几秒内开始思考，删掉的立刻消失。配置持久化在 ``.god/town/agents.json``。
- **一个人类角色。** 任何浏览器都能加入同一个世界，用 WASD 移动，跟附近的人说话。

运行形态
--------

1. 操作者在浏览器打开控制台。
2. React/Vite 前端向本地 FastAPI 后端开一条 WebSocket，先收到地图，然后是快照与事件。
3. 世界引擎推进移动，并把说话内容投递给附近的角色。
4. 每个 AI 居民跑一个独立的 ``asyncio`` 任务，用普通 HTTP 调自己的模型端点，然后执行拿回来的决定。
5. 人类输入走同一条 WebSocket，人类角色在同一张碰撞网格上移动。

主要目录
--------

``scripts/god.sh``
   一条命令完成装依赖、启动、重启、查状态、跟日志、重置。

``agentsociety/frontend/src/pages/Town``
   控制台：Phaser 画布、居民面板、新增/编辑表单、事件日志、WASD 输入。

``agentsociety/packages/agentsociety2/agentsociety2/town``
   世界引擎：地图布局、A* 寻路、按 Agent 独立的 LLM 客户端、决策循环、持久化。

``agentsociety/packages/agentsociety2/agentsociety2/backend/routers/town.py``
   ``/api/v1/town`` 的 HTTP 接口与 WebSocket。

``agentsociety/custom/skills``
   可选的技能目录，由 ``/skills`` 页面展示。

``jiuwenclaw``
   内置的进程外智能体运行时。小镇不依赖它。
