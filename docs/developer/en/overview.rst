Overview
========

GOD stands for Govern, Observe, Direct. It runs a small pixel town on your machine, fills it with AI residents, and lets you walk in and talk to them.

What GOD is
-----------

- A **world loop** that advances every actor at 20 Hz and broadcasts snapshots at 10 Hz. It runs from the moment the backend starts; nothing has to trigger a step.
- A **fixed map** generated in code: a central plaza with six rooms around it. No tilesets, no map packages, no generation step.
- **Per-resident model endpoints.** Each AI carries its own provider, base URL, model, API key, temperature, and decision interval. One resident can run on local Ollama while another talks to a remote OpenAI-compatible server.
- **Hot-pluggable residents.** Add one and it starts thinking within seconds; delete one and it is gone immediately. Configuration is persisted to ``.god/town/agents.json``.
- **A human actor.** Any browser can join the same world, move with WASD, and speak to whoever is nearby.

Runtime shape
-------------

1. The operator opens the control room in the browser.
2. The React/Vite frontend opens a WebSocket to the local FastAPI backend and receives the map, then snapshots and events.
3. The world engine advances movement and delivers speech to nearby actors.
4. Each AI resident runs an independent ``asyncio`` task, calling its own model endpoint over plain HTTP and applying the decision it gets back.
5. Human input arrives on the same WebSocket and moves the human actor through the same collision grid.

Primary repo areas
------------------

``scripts/god.sh``
   One-command setup, start, restart, status, log tailing, and reset.

``agentsociety/frontend/src/pages/Town``
   Control room: Phaser canvas, resident panel, add/edit form, event log, WASD input.

``agentsociety/packages/agentsociety2/agentsociety2/town``
   World engine: map layout, A* pathfinding, per-agent LLM client, decision loops, persistence.

``agentsociety/packages/agentsociety2/agentsociety2/backend/routers/town.py``
   The ``/api/v1/town`` HTTP API and the WebSocket endpoint.

``agentsociety/custom/skills``
   Optional skill catalog surfaced by the ``/skills`` page.

``jiuwenclaw``
   Integrated out-of-process agent runtime. The town does not depend on it.
