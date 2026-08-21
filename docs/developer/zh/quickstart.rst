快速开始
========

启动 GOD
--------

.. code-block:: bash

   ./scripts/god.sh start

``start`` 是幂等的：服务已经在跑就会复用。

首次运行流程
------------

在一份干净的检出上，GOD 会：

1. 从 ``.env.example`` 生成 ``.env``。
2. 安装 Python 与 Node 依赖。
3. 启动后端 —— 世界循环随之运转，并恢复已保存的居民。
4. 启动控制台并打印地址 —— 默认是 ``http://127.0.0.1:5174``。

小镇一开始是空的，从控制台把居民加进去。

加一个居民
----------

如果用 Ollama，先拉个模型：

.. code-block:: bash

   ollama pull qwen2.5:7b

然后在「AI 居民」面板点 **新增**，填名字、人物设定、接口类型（``Ollama``）、API 地址（``http://localhost:11434``）、模型（``qwen2.5:7b``）。点 **测试连接** —— 带延迟数字的绿色结果说明端点和模型都通。选好初始房间和决策间隔，点 **创建**。

居民会出现，并在几秒内开始自己做决定。

命令行也能做同样的事：

.. code-block:: bash

   curl -s localhost:8001/api/v1/town/agents \
     -X POST -H 'Content-Type: application/json' \
     -d '{
           "name": "小满",
           "persona": "一个好奇的图书管理员，喜欢聊书。",
           "room_id": "library",
           "llm": {"provider": "ollama", "base_url": "http://localhost:11434", "model": "qwen2.5:7b"},
           "decision_interval_s": 8
         }'

你自己走进去
------------

在顶栏填个名字，点 **进入小镇**。用 WASD 或方向键移动。你说的话只有八格内的居民听得到，并会进入它们的下一轮决策。

验证
----

.. code-block:: bash

   ./scripts/god.sh status

正常输出会显示后端与控制台端口都是 up，以及已保存的居民数量。
