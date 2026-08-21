Configuration
=============

GOD keeps machine settings in ``.env`` and per-resident model settings with the residents themselves.

``.env``
--------

``.env`` is created from ``.env.example`` and ignored by Git. Nothing in it is required to start.

.. list-table::
   :header-rows: 1

   * - Variable
     - Purpose
     - Default
   * - ``GOD_LLM_PROVIDER``
     - Prefills the provider field of the add-resident form (``ollama`` or ``openai``)
     - ``ollama``
   * - ``GOD_LLM_API_BASE``
     - Prefills the API base URL
     - ``http://localhost:11434``
   * - ``GOD_LLM_MODEL``
     - Prefills the model name
     - empty
   * - ``GOD_LLM_API_KEY``
     - Marks the form as having a key available
     - empty
   * - ``GOD_BACKEND_HOST``
     - Backend bind host
     - ``127.0.0.1``
   * - ``GOD_BACKEND_PORT``
     - Backend port
     - ``8001``
   * - ``GOD_FRONTEND_PORT``
     - Control-room port
     - ``5174``
   * - ``GOD_SKIP_SETUP``
     - Skip dependency checks on start
     - ``0``

These values only prefill the form. The endpoint a resident actually uses is stored on that resident.

``.god/town/agents.json``
-------------------------

Every resident you add is written here: name, sprite, persona, starting room, endpoint (including the API key), decision interval, and whether it is paused. The backend loads this file on startup and puts everyone back in the town.

To wipe it:

.. code-block:: bash

   ./scripts/god.sh reset

Changing the map
----------------

The map is a constant table in ``agentsociety2/town/map_layout.py``. Edit ``ROOMS``, ``PLAZA``, or ``CORRIDORS``, restart the backend, and reload the page — the frontend draws whatever the backend reports. Run the test suite afterwards; ``test_town.py`` asserts every room can still reach the plaza.

Path-proxy serving
------------------

Under code-server the control room is served from ``/proxy/<port>/``. ``scripts/god.sh`` detects this and sets ``VITE_BASE`` accordingly; override it explicitly with ``VITE_BASE=/proxy/5174/`` if detection fails.
