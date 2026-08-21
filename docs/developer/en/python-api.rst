Selected Python API
===================

This page names source files that are stable enough to inspect when extending GOD. It does not attempt to publish the whole upstream AgentSociety API.

World engine
------------

- ``agentsociety2.town.map_layout`` — ``build_world_map()``, ``walkable_tiles()``, ``wall_tiles()``, ``room_of()``, ``room_anchor()``, ``all_rooms()``
- ``agentsociety2.town.pathfind`` — ``astar()``, ``nearest_walkable()``, ``neighbors()``
- ``agentsociety2.town.llm_client`` — ``LLMEndpoint``, ``chat()``, ``list_models()``, ``test_endpoint()``, ``extract_json_object()``
- ``agentsociety2.town.agents`` — ``TownAgentConfig``, ``TownActor``, ``run_agent_loop()``, ``decide_once()``, ``build_observation()``, ``build_system_prompt()``
- ``agentsociety2.town.world`` — ``WorldEngine``, ``get_world()``
- ``agentsociety2.town.store`` — ``load_agents()``, ``save_agents()``, ``state_dir()``

Backend routers
---------------

- ``agentsociety2.backend.routers.town`` — the town API, the WebSocket, ``bootstrap_world()``, ``shutdown_world()``
- ``agentsociety2.backend.routers.agent_skills``
- ``agentsociety2.backend.routers.custom``
- ``agentsociety2.backend.routers.modules``
- ``agentsociety2.backend.routers.prefill_params``

Custom agent paths
------------------

- ``agentsociety2.backend.services.custom``
- ``agentsociety2.agent``
- ``agentsociety/custom/agents/``

The vendored AgentSociety core (``agentsociety2.society``, ``agentsociety2.env``, ``agentsociety2.storage``) is still present and importable, but the town does not use it.
