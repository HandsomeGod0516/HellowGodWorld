Installation
============

Prerequisites
-------------

GOD currently expects:

- macOS/Linux: Python 3.11 or newer, Node.js and ``npm``, ``uv``, and ``screen``.
- Windows: PowerShell 5.1+ and ``winget``. The PowerShell entrypoint auto-installs missing Git, Node.js LTS/npm, and ``uv``; ``uv`` supplies the managed Python runtime.
- A model endpoint to point residents at. Local `Ollama <https://ollama.com>`_ is the simplest; any OpenAI-compatible server works too.

On macOS:

.. code-block:: bash

   brew install python node uv screen

Clone
-----

.. code-block:: bash

   git clone https://github.com/XiaoLuoLYG/GOD.git
   cd GOD

Install by starting
-------------------

The recommended install path is the same as the start path:

.. code-block:: bash

   ./scripts/god.sh start

On Windows PowerShell, use:

.. code-block:: powershell

   .\scripts\god.cmd start

On first run the script creates ``.env`` from ``.env.example``, installs backend and control-room dependencies, starts both services, and prints the control-room URL. Nothing in ``.env`` is required — residents carry their own model settings.

Install only
------------

To check or install dependencies without starting the stack:

.. code-block:: bash

   ./scripts/god.sh setup

If the dependencies are already in place and you want to skip the checks:

.. code-block:: bash

   GOD_SKIP_SETUP=1 ./scripts/god.sh start

Local-only files
----------------

Do not commit local runtime state:

- ``.env``
- ``.god/`` (logs, pids, and ``town/agents.json``)
- ``.live/``
