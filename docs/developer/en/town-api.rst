Town API
========

All routes are mounted under ``/api/v1/town`` by ``agentsociety2.backend.routers.town``.

World
-----

.. list-table::
   :header-rows: 1

   * - Method
     - Path
     - Returns
   * - ``GET``
     - ``/map``
     - Grid size, tile size, plaza, corridors, rooms (rect, interior, door, anchor), wall tiles
   * - ``GET``
     - ``/rooms``
     - ``[{id, name, name_en}]`` including the plaza
   * - ``GET``
     - ``/sprites``
     - Character sprite keys available to the frontend
   * - ``GET``
     - ``/defaults``
     - Prefill values for the add-resident form, read from ``GOD_LLM_*``
   * - ``GET``
     - ``/state``
     - Current snapshot plus the recent event buffer

Residents
---------

.. list-table::
   :header-rows: 1

   * - Method
     - Path
     - Notes
   * - ``GET``
     - ``/agents``
     - Config plus live runtime state. The API key is masked as ``***``.
   * - ``POST``
     - ``/agents``
     - Tests the endpoint first and returns ``400`` if it fails. Pass ``skip_connection_test: true`` to bypass.
   * - ``POST``
     - ``/agents/test-connection``
     - Probes an endpoint without creating anything.
   * - ``PATCH``
     - ``/agents/{id}``
     - Partial update. Omitting ``api_key`` (or sending ``***``) keeps the stored key. Restarts that resident's loop.
   * - ``DELETE``
     - ``/agents/{id}``
     - Removes the resident and cancels its task immediately.
   * - ``POST``
     - ``/agents/{id}/goto``
     - Sends any actor to a room yourself.
   * - ``POST``
     - ``/say``
     - Makes an actor speak.

WebSocket
---------

``GET /api/v1/town/ws``

Server to client:

.. list-table::
   :header-rows: 1

   * - Type
     - Payload
   * - ``map``
     - Sent once on connect
   * - ``snapshot``
     - ``{tick, actors: [...]}`` at 10 Hz
   * - ``events``
     - The recent event buffer, sent once on connect
   * - ``event``
     - A single ``say`` / ``join`` / ``leave`` / ``arrive`` event
   * - ``agent_list``
     - Sent on connect and whenever residents change
   * - ``joined``
     - ``{actor_id}`` after a successful ``join``

Client to server:

.. list-table::
   :header-rows: 1

   * - Type
     - Payload
   * - ``join``
     - ``{name}`` — creates a human actor bound to this socket
   * - ``input``
     - ``{dir: "up"|"down"|"left"|"right"|null}`` — held direction
   * - ``say``
     - ``{text}``
   * - ``leave``
     - Removes the human actor

The human actor is torn down when the socket closes.

Endpoint testing
----------------

.. code-block:: bash

   curl -s localhost:8001/api/v1/town/agents/test-connection \
     -X POST -H 'Content-Type: application/json' \
     -d '{"provider":"ollama","base_url":"http://localhost:11434","model":"qwen2.5:7b"}'

A successful response carries ``ok``, ``latency_ms``, the models the endpoint reports, and the sample reply. A failure carries a human-readable ``error``.
