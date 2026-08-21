為 AgentSociety 2 貢獻
===============================

感謝您對貢獻 AgentSociety 2 的興趣！

貢獻方式
------------------

* **報告錯誤**: 使用可重現的示例提交問題
* **建議功能**: 分享您的改進想法
* **提交程式碼**: 使用您的更改提交拉取請求
* **改進文件**: 幫助使文件更清晰
* **分享示例**: 向集合新增有用的示例

報告錯誤
--------------

報告錯誤時，請包括：

* Python 版本
* AgentSociety 2 版本
* 最小的可重現示例
* 預期行為與實際行為
* 任何錯誤訊息或回溯

有關詳情，請參閱錯誤報告模板。

建議功能
-------------------

歡迎功能建議！請：

* 清楚地描述用例
* 解釋為什麼它有用
* 考慮它是否適合專案範圍
* 願意接受討論

提交拉取請求
-------------------------

提交 PR 之前：

1. 檢查現有問題以獲取相關討論
2. Fork 倉庫
3. 為您的工作建立分支
4. 使用清晰的提交訊息進行更改
5. 如需要，更新文件
6. 提交拉取請求

PR 指南
~~~~~~~~~~~~~

* 保持更改專注和原子化
* 遵循現有程式碼風格
* 為新函式/類新增文件字串
* 更新相關文件
* 確保 CI 透過

程式碼審查流程
~~~~~~~~~~~~~~~~~~~

所有 PR 都會經過程式碼審查：

* 維護者將審查您的更改
* 解決任何反饋或請求
* 批准後，PR 將被合併
* 大型更改可能需要多次迭代

開發設定
------------------

.. code-block:: bash

   # Clone your fork
   git clone https://github.com/your-username/agentsociety.git
   cd agentsociety

   # Install in development mode
   uv sync
   pip install -e "packages/agentsociety2[dev]"

   # Install pre-commit hooks
   cd packages/agentsociety2
   pre-commit install

新增新功能
-------------------

新增新功能時：

1. 首先開啟一個問題進行討論
2. 實現功能
3. 更新文件
4. 如有幫助，新增示例

示例結構
~~~~~~~~~~~~~~~~~

.. code-block:: text

   tests/
   ├── test_agent.py
   ├── test_env.py
   └── test_storage.py

   agentsociety2/
   ├── new_module/
   │   ├── __init__.py
   │   ├── core.py
   │   └── utils.py
   └── new_module/
       ├── __init__.py
       └── implementation.py

文件標準
------------------------

文件字串應遵循 Google 風格：

.. code-block:: python

   def example_function(param1: str, param2: int) -> bool:
       """Brief description of the function.

       Longer description with more details.

       Args:
           param1: Description of param1
           param2: Description of param2

       Returns:
           Description of return value

       Raises:
           ValueError: If something goes wrong
       """
       pass

社群指南
--------------------

* 尊重和建設性
* 歡迎新的貢獻者
* 關注對社群最有利的事情
* 對其他社群成員表現出同理心

獲取幫助
------------

* **GitHub Issues**: 用於錯誤和功能請求
* **GitHub Discussions**: 用於問題和想法
* **Documentation**: 首先檢視文件

許可證
-------

透過貢獻 AgentSociety 2，您同意您的貢獻將根據 Apache License 2.0 獲得許可。
