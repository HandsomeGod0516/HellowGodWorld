# AgentSociety2 模組與引數管理文件

## 概述

AgentSociety2 提供了一個完整的模組註冊和引數管理系統，支援內建模組和自定義模組的發現、註冊、查詢和驗證。

## 一、模組管理

### 1.1 模組型別

AgentSociety2 支援兩種型別的模組：

1. **內建模組 (Built-in Modules)**：位於 `agentsociety2/contrib/` 目錄下
   - 環境模組：`contrib/env/` 目錄
   - Agent 模組：`contrib/agent/` 目錄
   - 核心 Agent：`agent/person.py` 中的 `PersonAgent`

2. **自定義模組 (Custom Modules)**：位於工作區的 `custom/` 目錄下
   - Agent：`custom/agents/` 目錄
   - 環境模組：`custom/envs/` 目錄

### 1.2 模組註冊系統

#### 核心元件

```
agentsociety2/registry/
├── __init__.py          # 匯出所有公共介面
├── base.py              # ModuleRegistry 核心類
├── modules.py           # 自動發現和註冊邏輯
└── models.py            # Pydantic 配置模型
```

#### ModuleRegistry (單例模式)

`ModuleRegistry` 是模組註冊的核心，採用單例模式：

```python
from agentsociety2.registry import get_registry

registry = get_registry()
```

**主要方法：**

| 方法 | 說明 |
|------|------|
| `register_env_module(module_type, module_class, is_custom)` | 註冊環境模組 |
| `register_agent_module(agent_type, agent_class, is_custom)` | 註冊 Agent |
| `get_env_module(module_type)` | 獲取環境模組類 |
| `get_agent_module(agent_type)` | 獲取 Agent 類 |
| `list_env_modules()` | 列出所有環境模組 |
| `list_agent_modules()` | 列出所有 Agent |
| `clear_custom_modules()` | 清除所有自定義模組 |
| `get_module_info(module_type, kind)` | 獲取模組詳細資訊 |

### 1.3 自動發現機制

#### 內建模組自動發現

系統在匯入 `agentsociety2.registry` 時會自動發現並註冊內建模組：

```python
# modules.py 中的自動發現邏輯
def _discover_contrib_env_modules() -> Dict[str, Type[EnvBase]]:
    """使用 pkgutil 遍歷 contrib.env 包"""
    # 自動發現所有 EnvBase 子類
    # 類名轉換：SimpleSocialSpace -> simple_social_space

def _discover_contrib_agents() -> Dict[str, Type[AgentBase]]:
    """使用 pkgutil 遍歷 contrib.agent 包"""
    # 自動發現所有 AgentBase 子類
```

#### 自定義模組掃描

```python
from agentsociety2.registry import scan_and_register_custom_modules
from pathlib import Path

scan_result = scan_and_register_custom_modules(
    workspace_path=Path("/path/to/workspace"),
    registry=get_registry(),
)
# 返回：{"agents": [...], "envs": [...], "errors": [...]}
```

**掃描規則：**
- 掃描 `custom/agents/` 和 `custom/envs/` 目錄
- 跳過 `examples/` 子目錄和 `__` 開頭的檔案
- 自動匯入並註冊發現的類
- 自定義模組標記 `_is_custom = True`

### 1.4 模組命名規則

| 類名 | 模組型別標識 |
|------|-------------|
| `SimpleSocialSpace` | `simple_social_space` |
| `PersonAgent` | `person_agent` |
| `LLMDonorAgent` | `llm_donor_agent` |
| `ReputationGameEnv` | `reputation_game_env` |

### 1.5 模組查詢介面

#### API 介面

```
GET /api/v1/custom/classes?workspace_path=/path&include_custom=true
```

返回示例：
```json
{
  "success": true,
  "env_modules": {
    "reputation_game_env": {
      "type": "reputation_game_env",
      "class_name": "ReputationGameEnv",
      "description": "...",
      "is_custom": false,
      "has_prefill": true
    }
  },
  "agents": {
    "llm_donor_agent": {
      "type": "llm_donor_agent",
      "class_name": "LLMDonorAgent",
      "description": "...",
      "is_custom": false,
      "has_prefill": false
    }
  },
  "env_module_count": 16,
  "agent_count": 7
}
```

## 二、引數管理

### 2.1 引數來源

AgentSociety2 中的引數有三個來源：

1. **類定義引數**：從模組類的 `__init__` 方法簽名中提取
2. **生成引數**：透過程式碼生成過程建立的配置
3. **預填充引數 (Prefill Params)**：使用者預先定義的預設引數

### 2.2 引數獲取流程

```
                    ┌─────────────────────┐
                    │   請求模組引數      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  獲取模組類資訊      │
                    │  (類簽名、文件)      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  載入預填充引數      │
                    │  (.agentsociety/     │
                    │   prefill_params.json)│
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  合併引數            │
                    │  (prefill 覆蓋預設)  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  返回完整引數資訊     │
                    └─────────────────────┘
```

### 2.3 預填充引數 (Prefill Params)

#### 檔案位置

```
<workspace>/.agentsociety/prefill_params.json
```

#### 檔案結構

```json
{
  "version": "1.0",
  "env_modules": {
    "reputation_game_env": {
      "config": {
        "Z": 100,
        "BENEFIT": 5,
        "COST": 1,
        "norm_type": "stern_judging"
      }
    },
    "social_media_space": {
      "num_users": 1000
    }
  },
  "agents": {
    "person_agent": {
      "profile": {
        "custom_fields": {
          "learning_frequency": 0.1,
          "risk_tolerance": 0.5
        }
      }
    }
  }
}
```

#### API 介面

```
# 獲取所有預填充引數
GET /api/v1/prefill-params?workspace_path=/path

# 獲取特定類的預填充引數
GET /api/v1/prefill-params/env_module/reputation_game_env?workspace_path=/path
GET /api/v1/prefill-params/agent/person_agent?workspace_path=/path
```

### 2.4 引數驗證

#### 驗證指令碼

``extension/skills/agentsociety-experiment-config/scripts/validate_config.py`` 用於對生成的配置做端到端校驗。

**驗證內容：**
1. 載入 `init_config.json`
2. 解析 `SIM_SETTINGS.json`
3. 例項化每個環境模組類（嚴格驗證）
4. 例項化每個 Agent 類（嚴格驗證）
5. 報告初始化失敗的詳細錯誤

## 三、配置模型

### 3.1 核心配置模型

```python
# 環境模組配置
class EnvModuleConfig(BaseModel):
    module_type: str              # 模組型別標識
    kwargs: Dict[str, Any]        # 初始化引數

# Agent 配置
class AgentConfig(BaseModel):
    agent_id: int                 # Agent ID
    agent_type: str               # Agent 型別
    kwargs: Dict[str, Any]        # 初始化引數（包含 id、profile 等）

# 初始化配置
class InitConfig(BaseModel):
    env_modules: List[EnvModuleConfig]
    agents: List[AgentConfig]
```

### 3.2 建立例項請求

```python
class CreateInstanceRequest(BaseModel):
    instance_id: str
    env_modules: List[EnvModuleInitConfig]
    agents: List[AgentInitConfig]
    start_t: datetime
    tick: int = 600
```

## 四、自定義模組開發

### 4.1 目錄結構

```
<workspace>/
├── custom/
│   ├── agents/
│   │   └── my_agent.py          # 自定義 Agent
│   └── envs/
│       └── my_env.py            # 自定義環境模組
└── .agentsociety/
    ├── agent_classes/            # 生成的 Agent 類 JSON
    └── env_modules/              # 生成的環境模組 JSON
```

### 4.2 自定義 Agent 示例

```python
# custom/agents/my_agent.py
from agentsociety2.agent.base import AgentBase
from agentsociety2.env.base import EnvBase

class MyCustomAgent(AgentBase):
    """我的自定義 Agent"""

    def __init__(
        self,
        id: int,
        profile: dict,
        custom_param: str = "default",  # 自定義引數
    ):
        super().__init__(id=id, profile=profile)
        self.custom_param = custom_param
```

### 4.3 自定義環境模組示例

```python
# custom/envs/my_env.py
from agentsociety2.env.base import EnvBase

class MyCustomEnv(EnvBase):
    """我的自定義環境模組"""

    def __init__(
        self,
        config_param: int = 100,  # 自定義引數
    ):
        super().__init__()
        self.config_param = config_param

    @tool(readonly=True, kind="observe")
    def get_state(self, agent_id: int) -> str:
        """返回環境狀態"""
        return f"Current state: {self.config_param}"
```

### 4.4 掃描和註冊

```bash
# 掃描自定義模組
curl -X POST http://localhost:8001/api/v1/custom/scan \
  -d '{"workspace_path": "/path/to/workspace"}'

# 重新掃描（清除舊的自定義模組）
curl -X POST http://localhost:8001/api/v1/custom/rescan \
  -d '{"workspace_path": "/path/to/workspace"}'
```

## 五、使用示例

### 5.1 獲取模組資訊

```python
from agentsociety2.registry import get_registry

registry = get_registry()

# 獲取環境模組資訊
env_info = registry.get_module_info("reputation_game_env", "env_module")
print(env_info)
# {
#     "success": True,
#     "type": "reputation_game_env",
#     "class_name": "ReputationGameEnv",
#     "description": "...",
#     "parameters": {...},
#     "is_custom": False
# }

# 獲取 Agent 資訊
agent_info = registry.get_module_info("person_agent", "agent")
```

### 5.2 例項化模組

```python
from agentsociety2.registry import get_env_module_class

# 獲取類
EnvClass = get_env_module_class("reputation_game_env")

# 例項化
env_instance = EnvClass(config={"Z": 100, "BENEFIT": 5})
```

### 5.3 列出所有模組

```python
from agentsociety2.registry import (
    get_registered_env_modules,
    get_registered_agent_modules,
)

# 列出環境模組
for module_type, module_class in get_registered_env_modules():
    print(f"{module_type}: {module_class.__name__}")

# 列出 Agent
for agent_type, agent_class in get_registered_agent_modules():
    print(f"{agent_type}: {agent_class.__name__}")
```

### 5.4 使用預填充引數

```python
import json
from pathlib import Path

# 讀取預填充引數
prefill_file = Path("/workspace/.agentsociety/prefill_params.json")
prefill_params = json.loads(prefill_file.read_text())

# 獲取特定模組的預填充引數
env_prefill = prefill_params["env_modules"].get("reputation_game_env", {})
print(env_prefill)  # {"config": {"Z": 100, ...}}
```

## 六、總結

AgentSociety2 的模組和引數管理系統提供了：

1. **自動發現機制**：自動發現內建和自定義模組
2. **統一登錄檔**：單例模式的 ModuleRegistry
3. **多源引數管理**：類定義、生成引數、預填充引數
4. **靈活的查詢介面**：命令列指令碼和 API 介面
5. **嚴格的驗證**：透過例項化驗證配置有效性
