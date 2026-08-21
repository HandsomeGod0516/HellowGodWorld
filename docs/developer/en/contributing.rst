Contributing
============

Start from the normal local path:

.. code-block:: bash

   git clone https://github.com/XiaoLuoLYG/GOD.git
   cd GOD
   ./scripts/god.sh start

Before a PR
-----------

Run the checks relevant to your change:

.. code-block:: bash

   git diff --check
   npm run build --prefix agentsociety/frontend
   cd agentsociety
   uv run pytest -q packages/agentsociety2/tests

For map layout changes, ``test_town.py`` is the one that matters — it asserts every room still reaches the plaza and that walls stay non-walkable:

.. code-block:: bash

   cd agentsociety
   uv run pytest -q packages/agentsociety2/tests/test_town.py

Artifact hygiene
----------------

Keep runtime data out of public PRs. Do not stage ``.god/``, ``.live/``, ``.superpowers/``, or ``.DS_Store``.

More detail
-----------

See ``README.md`` at the repo root for setup and feature notes.
