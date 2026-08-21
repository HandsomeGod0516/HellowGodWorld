Quickstart
==========

Start GOD
---------

.. code-block:: bash

   ./scripts/god.sh start

``start`` is idempotent: if a service is already running, the script reuses it.

First-run flow
--------------

On a clean checkout, GOD will:

1. Create ``.env`` from ``.env.example``.
2. Install Python and Node dependencies.
3. Start the backend, which brings up the world loop and restores saved residents.
4. Start the control room and print its URL — ``http://127.0.0.1:5174`` by default.

The town starts empty. Add residents from the control room.

Add a resident
--------------

Pull a model first if you are using Ollama:

.. code-block:: bash

   ollama pull qwen2.5:7b

Then, in the **AI residents** panel, press **Add** and fill in a name, a persona, the provider (``Ollama``), the base URL (``http://localhost:11434``), and the model (``qwen2.5:7b``). Press **Test connection** — a green result with a latency number means both the endpoint and the model are reachable. Choose a starting room and a decision interval, then **Create**.

The resident spawns and starts deciding on its own within a few seconds.

You can do the same from the shell:

.. code-block:: bash

   curl -s localhost:8001/api/v1/town/agents \
     -X POST -H 'Content-Type: application/json' \
     -d '{
           "name": "Mira",
           "persona": "A curious librarian who loves talking about books.",
           "room_id": "library",
           "llm": {"provider": "ollama", "base_url": "http://localhost:11434", "model": "qwen2.5:7b"},
           "decision_interval_s": 8
         }'

Walk in yourself
----------------

Type a name in the top bar and press **Enter town**. Move with WASD or the arrow keys. Anything you say is heard only by residents within eight tiles, and it shows up in their next decision.

Verify
------

.. code-block:: bash

   ./scripts/god.sh status

Healthy output shows the backend and control-room ports as up, plus how many residents are saved.
