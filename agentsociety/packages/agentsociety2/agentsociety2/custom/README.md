# 自定義模組開發指南

歡迎使用 AgentSociety2 自定義模組功能！本指南將幫助您建立和使用自定義的 Agent 和環境模組。

## 目錄結構

```
custom/
├── agents/              # 自定義 Agent
│   └── examples/        # 官方示例（參考用）
├── envs/                # 自定義環境模組
│   └── examples/        # 官方示例（參考用）
├── skills/              # 自定義 Agent Skills
│   └── examples/        # 官方示例（參考用）
│       └── my-custom-skill/
│           ├── SKILL.md
│           └── scripts/my-custom-skill.py
└── README.md            # 本文件
```

## 快速開始

### 1. 建立自定義 Agent

在 `custom/agents/` 目錄下建立新的 `.py` 檔案，例如 `my_agent.py`：

```python
from agentsociety2.agent.base import AgentBase
from datetime import datetime

class MyAgent(AgentBase):
    """我的自定義 Agent"""

    @classmethod
    def mcp_description(cls) -> str:
        return """MyAgent: 我的自定義 Agent

這是我的第一個自定義 Agent。
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """回答問題"""
        prompt = f"問題：{message}\n請回答："
        response = await self.acompletion([{"role": "user", "content": prompt}])
        return response.choices[0].message.content or ""

    async def step(self, tick: int, t: datetime) -> str:
        """執行模擬步驟"""
        return f"Agent {self.id} 執行步驟"

    async def dump(self) -> dict:
        """序列化狀態"""
        return {"id": self._id, "profile": self._profile}

    async def load(self, dump_data: dict):
        """載入狀態"""
        self._id = dump_data.get("id", self._id)
        self._profile = dump_data.get("profile", self._profile)
```

### 2. 建立自定義環境模組

在 `custom/envs/` 目錄下建立新的 `.py` 檔案，例如 `my_env.py`：

```python
from agentsociety2.env import EnvBase, tool
from datetime import datetime

class MyEnv(EnvBase):
    """我的自定義環境"""

    def __init__(self, config=None):
        super().__init__()
        # 初始化你的環境狀態

    @classmethod
    def mcp_description(cls) -> str:
        return """MyEnv: 我的自定義環境

這是我的第一個自定義環境。
"""

    @tool(readonly=True, kind="observe")
    async def get_state(self, agent_id: int) -> dict:
        """獲取狀態（觀察工具）"""
        return {"agent_id": agent_id, "state": "正常"}

    @tool(readonly=False)
    async def do_action(self, agent_id: int, action: str) -> dict:
        """執行操作（修改工具）"""
        return {"agent_id": agent_id, "action": action, "result": "成功"}

    async def step(self, tick: int, t: datetime):
        """環境步驟"""
        self.t = t
```

### 3. 掃描和註冊

建立程式碼後，在 VSCode 中執行命令：

```
AgentSociety: 掃描自定義模組
```

或呼叫 API：

```bash
curl -X POST http://localhost:8001/api/v1/custom/scan \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

### 4. 測試驗證

在 VSCode 中執行命令：

```
AgentSociety: 測試自定義模組
```

或呼叫 API：

```bash
curl -X POST http://localhost:8001/api/v1/custom/test \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

系統會在記憶體中執行測試並返回結果。

> 注意：掃描/註冊規則（與後端 scanner/registry 一致）
>
> - 只掃描工作區的 `custom/agents/**/*.py` 與 `custom/envs/**/*.py`
> - 路徑中包含 `examples/` 的檔案會被跳過（示例僅供參考，不參與註冊）
> - **類必須在該檔案內定義**（不能僅 import 後 re-export），否則不會被接受/註冊

## 詳解

### Agent 必需方法

所有自定義 Agent 必須實現以下方法：

| 方法 | 說明 |
|------|------|
| `mcp_description()` | 返回模組描述（類方法，**建議覆蓋**；`AgentBase` 有預設描述） |
| `ask(message, readonly)` | 回答來自環境的問題 |
| `step(tick, t)` | 執行一個模擬步驟 |
| `dump()` | 序列化 Agent 狀態 |
| `load(dump_data)` | 從字典載入狀態 |

### 環境模組必需方法

所有自定義環境模組必須實現：

| 方法 | 說明 |
|------|------|
| `mcp_description()` | 返回模組描述（類方法） |
| `step(tick, t)` | 執行環境步驟 |
| 使用 `@tool` 裝飾器註冊至少一個工具方法 |

### @tool 裝飾器引數

```python
@tool(
    readonly=True,           # 是否只讀
    kind="observe",          # 工具型別: "observe", "statistics", 或 None
    name="custom_name",      # 自定義工具名（可選）
    description="描述"       # 工具描述（可選）
)
async def my_tool(self, agent_id: int) -> dict:
    """工具方法"""
    pass
```

**工具型別：**
- `kind="observe"`: 觀察工具，只能有一個引數（agent_id），必須是 readonly=True
- `kind="statistics"`: 統計工具，只能有 self 引數，必須是 readonly=True
- `kind=None`: 普通工具，可以有多個引數，可以是 readonly=False

## API 端點

| 端點 | 方法 | 功能 |
|------|------|------|
| `/api/v1/custom/scan` | POST | 掃描並註冊自定義模組 |
| `/api/v1/custom/test` | POST | 測試自定義模組 |
| `/api/v1/custom/clean` | POST | 清理自定義模組配置 |
| `/api/v1/custom/list` | GET | 列出已註冊的自定義模組 |
| `/api/v1/custom/status` | GET | 獲取模組狀態概覽 |

## VSCode 命令

| 命令 | 功能 |
|------|------|
| `agentsociety.scanCustomModules` | 掃描自定義模組 |
| `agentsociety.testCustomModules` | 測試自定義模組 |
| `agentsociety.cleanCustomModules` | 清理自定義模組配置 |

## 建立自定義 Agent Skill

每個 PersonAgent 就是一個獨立的 Claude-like agent——它從 skill catalog 中選擇技能、讀取指令、呼叫工具完成任務。**skill 作者不需要了解 PersonAgent 的內部實現**，只需寫一個 `SKILL.md`。

### 目錄結構

```
custom/skills/my-skill/
├── SKILL.md              # YAML frontmatter + 行為指令（必需）
└── scripts/my-skill.py   # （可選）subprocess 指令碼
```

### SKILL.md frontmatter

只需要三個欄位：

| 欄位 | 必需 | 說明 |
|------|------|------|
| `name` | 是 | skill 唯一標識 |
| `description` | 是 | 一句話描述（出現在 agent 的 skill catalog 裡） |
| `script` | 否 | subprocess 指令碼路徑（如 `scripts/my-skill.py`） |
| `requires` | 否 | 依賴列表（activate 時自動啟用依賴） |

```yaml
---
name: my-skill
description: One-line description of what this skill does.
---
```

### 兩種模式

#### Prompt-only（推薦）

不宣告 `script`。agent `activate_skill` 後，`SKILL.md` 正文作為行為指令注入上下文，agent 用內建工具（`bash` / `codegen` / `workspace_*` / `glob` / `grep`）完成任務。

#### Subprocess

宣告 `script: scripts/my-skill.py`。agent 呼叫 `execute_skill` 時框架以子程序執行指令碼：

- 引數：`--args-json '{...}'`（由 LLM 決定傳什麼）
- 環境變數：`SKILL_NAME` / `SKILL_DIR` / `AGENT_WORK_DIR`
- 產物：寫入 `AGENT_WORK_DIR`（每個 agent 獨立目錄）

```python
import argparse, json
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", default="{}")
    args = json.loads(parser.parse_args().args_json or "{}")

    result = {"ok": True, "summary": f"ran (tick={args.get('tick')})"}
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### Agent 可用的工具（skill 作者須知）

當 agent 啟用你的 skill 後，它能用以下工具執行你的指令：

| 工具 | 用途 |
|------|------|
| `codegen` | 向模擬環境傳送指令（觀察、行動等） |
| `bash` | 在 agent workspace 執行 shell 命令 |
| `workspace_read/write/list` | 讀寫 agent workspace 檔案 |
| `glob` / `grep` | 在 workspace 搜尋檔案 |
| `execute_skill` | 執行另一個 skill 的 subprocess 指令碼 |
| `activate_skill` | 載入另一個 skill 的指令 |

### 掃描註冊

```bash
curl -X POST http://localhost:8001/api/v1/agent-skills/scan \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

## 示例

檢視 `custom/agents/examples/`、`custom/envs/examples/` 和 `custom/skills/examples/` 獲取更多示例：

- `simple_agent.py` - 基礎 Agent 示例
- `advanced_agent.py` - 帶記憶和情緒的 Agent
- `simple_env.py` - 基礎環境示例
- `advanced_env.py` - 資源管理環境
- `my-custom-skill/` - Agent Skill 示例

## 常見問題

### Q: 我的模組為什麼沒有被掃描到？

A: 檢查以下幾點：
1. 檔案是否在 `custom/agents/` 或 `custom/envs/` 目錄（不是 `examples/` 子目錄）
2. 是否正確繼承 `AgentBase` 或 `EnvBase`
3. 是否實現了所有必需方法
4. 檔名不要以 `__` 開頭

### Q: 如何除錯自定義模組？

A: 使用"測試自定義模組"命令，系統會在記憶體中：
1. 動態匯入自定義模組
2. 執行測試驗證
3. 顯示詳細的測試輸出

### Q: 如何清理自定義模組？

A: 執行"清理自定義模組"命令，會刪除所有 `is_custom=true` 的 JSON 配置。

### Q: 自定義模組可以和內建模組一起使用嗎？

A: 可以！掃描後，自定義模組會與內建模組一起出現在可用列表中。

## 最佳實踐

1. **命名規範**
   - Agent 類名以 `Agent` 結尾
   - 環境類名以 `Env` 結尾
   - 檔名使用小寫和下劃線

2. **錯誤處理**
   - 在 `ask()` 方法中捕獲異常
   - 返回有意義的錯誤資訊

3. **狀態管理**
   - 使用 `dump()` 和 `load()` 正確儲存/恢復狀態
   - 記錄重要的狀態變化

4. **工具設計**
   - 觀察工具用 `kind="observe"`
   - 統計工具用 `kind="statistics"`
   - 操作工具用 `kind=None` 和 `readonly=False`

## 技術支援

如有問題，請檢視：
- 示例程式碼：`custom/*/examples/`
- API 文件：`http://localhost:8001/docs`
