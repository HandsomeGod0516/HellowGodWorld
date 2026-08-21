命令
====

在仓库根目录执行。

.. list-table::
   :header-rows: 1

   * - 命令
     - 什么时候用
   * - ``./scripts/god.sh start``
     - 正常的幂等启动路径。
   * - ``./scripts/god.sh setup``
     - 只想安装或检查依赖。
   * - ``./scripts/god.sh restart``
     - 想停掉进程再启动。居民会被恢复。
   * - ``./scripts/god.sh stop``
     - 想停掉 GOD 并释放端口。
   * - ``./scripts/god.sh status``
     - 想看端口、地址、已保存的居民数量。
   * - ``./scripts/god.sh tail``
     - 想跟踪后端与控制台日志。
   * - ``./scripts/god.sh reset``
     - 想停掉并忘掉所有居民。

Windows 上把 ``./scripts/god.sh`` 换成 ``.\scripts\god.cmd``。

几个例子
--------

换端口启动：

.. code-block:: bash

   GOD_BACKEND_PORT=8100 GOD_FRONTEND_PORT=5200 ./scripts/god.sh start

在已经配好的机器上跳过依赖检查：

.. code-block:: bash

   GOD_SKIP_SETUP=1 ./scripts/god.sh start

强制重新同步依赖：

.. code-block:: bash

   GOD_FORCE_SETUP=1 ./scripts/god.sh setup

一边跑 UI 一边跟日志：

.. code-block:: bash

   ./scripts/god.sh tail
