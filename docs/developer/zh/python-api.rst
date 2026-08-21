部分 Python API
===============

这一页列出扩展 GOD 时值得直接看的源文件，并不打算发布完整的上游 AgentSociety API。

世界引擎
--------

- ``agentsociety2.town.map_layout`` —— ``build_world_map()``、``walkable_tiles()``、``wall_tiles()``、``room_of()``、``room_anchor()``、``all_rooms()``
- ``agentsociety2.town.pathfind`` —— ``astar()``、``nearest_walkable()``、``neighbors()``
- ``agentsociety2.town.llm_client`` —— ``LLMEndpoint``、``chat()``、``list_models()``、``test_endpoint()``、``extract_json_object()``
- ``agentsociety2.town.agents`` —— ``TownAgentConfig``、``TownActor``、``run_agent_loop()``、``decide_once()``、``build_observation()``、``build_system_prompt()``
- ``agentsociety2.town.world`` —— ``WorldEngine``、``get_world()``
- ``agentsociety2.town.store`` —— ``load_agents()``、``save_agents()``、``state_dir()``

后端路由
--------

- ``agentsociety2.backend.routers.town`` —— 小镇接口、WebSocket、``bootstrap_world()``、``shutdown_world()``
- ``agentsociety2.backend.routers.agent_skills``
- ``agentsociety2.backend.routers.custom``
- ``agentsociety2.backend.routers.modules``
- ``agentsociety2.backend.routers.prefill_params``

自定义 Agent 路径
-----------------

- ``agentsociety2.backend.services.custom``
- ``agentsociety2.agent``
- ``agentsociety/custom/agents/``

内置的 AgentSociety 核心（``agentsociety2.society``、``agentsociety2.env``、``agentsociety2.storage``）仍然存在且可导入，但小镇不使用它。
