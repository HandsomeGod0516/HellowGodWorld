Environment Variables
=====================

GOD reads a small set of ``GOD_*`` variables. Everything else that used to be configured globally now lives on individual residents.

Startup and ports
-----------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Purpose
     - Default
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
     - Skip dependency install/check on start
     - ``0``
   * - ``GOD_FORCE_SETUP``
     - Re-sync dependencies even if they look present
     - ``0``
   * - ``GOD_ENV_FILE``
     - Alternate ``.env`` location
     - ``<repo>/.env``
   * - ``GOD_STATE_DIR``
     - Where ``logs/``, ``pids/``, and ``town/agents.json`` live
     - ``<repo>/.god``
   * - ``BACKEND_LOG_LEVEL``
     - Uvicorn/backend log level
     - ``info``

Form prefill
------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Purpose
     - Default
   * - ``GOD_LLM_PROVIDER``
     - ``ollama`` or ``openai``
     - ``ollama``
   * - ``GOD_LLM_API_BASE``
     - Prefilled API base URL
     - ``http://localhost:11434``
   * - ``GOD_LLM_MODEL``
     - Prefilled model name
     - empty
   * - ``GOD_LLM_API_KEY``
     - Presence marks a key as available
     - empty

These are surfaced by ``GET /api/v1/town/defaults`` and only affect the add-resident form. A resident always uses the endpoint stored on itself.

Frontend serving
----------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Purpose
   * - ``VITE_BASE``
     - ``/`` or ``/proxy/<port>/``. ``scripts/god.sh`` sets this automatically under code-server.
   * - ``VITE_HOST``
     - Vite bind host. Set to ``0.0.0.0`` automatically when a path-proxy base is active.
   * - ``VITE_HMR_PROTOCOL`` / ``VITE_HMR_CLIENT_PORT``
     - HMR settings derived from ``VSCODE_PROXY_URI``.
