Commands
========

Run commands from the repo root.

.. list-table::
   :header-rows: 1

   * - Command
     - Use it when
   * - ``./scripts/god.sh start``
     - You want the normal idempotent startup path.
   * - ``./scripts/god.sh setup``
     - You only want to install or check dependencies.
   * - ``./scripts/god.sh restart``
     - You want to stop processes and start again. Residents are restored.
   * - ``./scripts/god.sh stop``
     - You want to stop GOD and release its ports.
   * - ``./scripts/god.sh status``
     - You want ports, URLs, and how many residents are saved.
   * - ``./scripts/god.sh tail``
     - You want to follow the backend and control-room logs.
   * - ``./scripts/god.sh reset``
     - You want to stop and forget every resident.

On Windows, replace ``./scripts/god.sh`` with ``.\scripts\god.cmd``.

Useful examples
---------------

Start on different ports:

.. code-block:: bash

   GOD_BACKEND_PORT=8100 GOD_FRONTEND_PORT=5200 ./scripts/god.sh start

Skip dependency checks on a machine you have already set up:

.. code-block:: bash

   GOD_SKIP_SETUP=1 ./scripts/god.sh start

Force a dependency re-sync:

.. code-block:: bash

   GOD_FORCE_SETUP=1 ./scripts/god.sh setup

Follow logs while another terminal runs the UI:

.. code-block:: bash

   ./scripts/god.sh tail
