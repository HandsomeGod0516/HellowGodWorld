参与开发
========

从正常的本地路径开始：

.. code-block:: bash

   git clone https://github.com/XiaoLuoLYG/GOD.git
   cd GOD
   ./scripts/god.sh start

提 PR 之前
----------

跑与你的改动相关的检查：

.. code-block:: bash

   git diff --check
   npm run build --prefix agentsociety/frontend
   cd agentsociety
   uv run pytest -q packages/agentsociety2/tests

改了地图布局的话，``test_town.py`` 是最关键的那个 —— 它会断言每个房间仍然能走到广场，以及墙仍然不可走：

.. code-block:: bash

   cd agentsociety
   uv run pytest -q packages/agentsociety2/tests/test_town.py

产物卫生
--------

不要把运行时数据带进公开 PR。不要 stage ``.god/``、``.live/``、``.superpowers/``、``.DS_Store``。

更多细节
--------

架设步骤与功能说明见仓库根目录的 ``README.md``。
