World Engine
============

The world engine lives in ``agentsociety/packages/agentsociety2/agentsociety2/town/``. It has no dependency on the AgentSociety experiment, tick, or replay lifecycle.

Modules
-------

``map_layout.py``
   The fixed map as constant tables. ``build_world_map()`` returns what the frontend draws; ``walkable_tiles()`` returns the collision grid; ``room_of()`` and ``room_anchor()`` map tiles to rooms and back.

``pathfind.py``
   ``astar(start, goal, walkable)`` over four-directional neighbours, plus ``nearest_walkable()`` for snapping an off-grid point.

``llm_client.py``
   Per-agent HTTP client. ``chat()`` speaks either the Ollama ``/api/chat`` shape or the OpenAI-compatible ``/chat/completions`` shape. ``test_endpoint()`` lists models, checks the requested one exists, then sends an eight-token chat. ``extract_json_object()`` pulls the first JSON object out of a chatty reply.

``agents.py``
   ``TownAgentConfig`` is the persisted shape. ``TownActor`` is the runtime entity, shared by AI residents and human players. ``run_agent_loop()`` is one resident's autonomous loop; ``build_observation()`` and ``build_system_prompt()`` shape what its model sees.

``world.py``
   ``WorldEngine`` owns every actor, runs the movement loop, resolves who can hear whom, and manages WebSocket subscribers. ``add_agent()`` / ``remove_agent()`` hot-plug residents by starting and cancelling their tasks.

``store.py``
   Reads and writes ``.god/town/agents.json``. Honours ``GOD_STATE_DIR``.

Timing
------

.. list-table::
   :header-rows: 1

   * - Constant
     - Value
     - Meaning
   * - ``TICK_HZ``
     - 20
     - Movement steps per second
   * - ``BROADCAST_HZ``
     - 10
     - Snapshots pushed per second
   * - ``TILES_PER_SECOND``
     - 3.6
     - Walking speed
   * - ``NEARBY_RADIUS_TILES``
     - 8
     - Sight and hearing radius
   * - ``decision_interval_s``
     - per resident, default 8
     - How often one resident calls its model

The decision contract
---------------------

A resident's model is asked for one JSON object:

.. code-block:: json

   {"action": "goto|say|idle", "room": "cafe", "text": "...", "reason": "..."}

- ``goto`` runs A* to the room anchor and the world loop walks the path.
- ``say`` sets a speech bubble and appends the line to the ``heard`` buffer of everyone nearby.
- ``idle`` keeps the resident where it is.

An unknown room, an unparseable reply, a timeout, or a dead endpoint all land the same way: the error goes on the resident's card and ``WorldEngine.wander()`` sends it to a random room. The world loop is never blocked by a slow model.

Adding a room
-------------

Append an entry to ``ROOMS`` in ``map_layout.py`` with its rectangle, its door tile, and its anchor, then add a corridor rectangle that connects the door to the plaza or to an existing corridor. Restart the backend; the frontend picks up the new layout with no asset work. ``tests/test_town.py`` will fail if the new room is unreachable.
