# Skill編寫指南

本文件介紹如何為PersonAgent編寫自定義Skill。

## 概述

Skill是PersonAgent的行為模組，每個Skill定義了Agent的一種能力。Skill可以：
- 純Prompt驅動（最簡單，推薦）
- 帶Python指令碼（用於確定性計算）
- 環境路由（codegen模式）

## 快速開始

### 建立一個簡單的Skill

1. 在`custom/skills/`目錄下建立新資料夾：

```
custom/skills/my-skill/
└── SKILL.md
```

2. 編寫SKILL.md：

```markdown
---
name: my-skill
description: 一句話描述功能。什麼時候使用。產生什麼輸出。
outputs:
  - result.json
---

# My Skill

## 何時使用
描述觸發條件。

## 輸入檔案
- `observation.txt`：當前觀察（如果存在）
- `needs.json`：需求狀態（如果存在）

## 執行步驟
1. 首先，用 `workspace_read` 讀取需要的檔案
2. 然後，分析內容並做出決策
3. 最後，用 `workspace_write` 寫入輸出檔案

## 輸出格式
\`\`\`json
{
  "field1": "描述",
  "field2": 0.5
}
\`\`\`

## 示例

**輸入**：
\`\`\`
observation.txt: "在公園遇到了Alice"
\`\`\`

**輸出**：
\`\`\`json
{
  "event": "met Alice at park",
  "emotion": "happy"
}
\`\`\`
```

就這麼簡單！無需程式設計，LLM會根據你的描述自動執行。

## SKILL.md結構詳解

### Frontmatter（必需）

Frontmatter是YAML格式的後設資料塊，位於檔案開頭：

```yaml
---
name: skill-name           # 必需：唯一識別符號
description: 描述           # 必需：用於catalog顯示
inputs:                    # 可選：依賴的輸入檔案列表
  - state/emotion.json
  - state/needs.json
outputs:                   # 可選：輸出檔案列表
  - output1.json
  - output2.txt
requires:                  # 可選：依賴的其他skill
  - needs
  - cognition
priority: 10               # 可選：優先順序（數字越大越優先）
script: scripts/main.py    # 可選：Python指令碼路徑
executor: codegen          # 可選：執行模式
user_invocable: true       # 可選：是否使用者可呼叫
---
```

### Body（必需）

Body是Markdown格式的行為指南，告訴LLM：

1. **何時使用**：觸發條件
2. **輸入**：讀取哪些檔案
3. **做什麼**：執行步驟
4. **輸出**：產生什麼檔案

## 可用的內建工具

Skill的Markdown body中可以指導LLM使用以下工具：

| 工具 | 用途 | 示例 |
|------|------|------|
| `workspace_read` | 讀取檔案 | `workspace_read("observation.txt")` |
| `workspace_write` | 寫入檔案 | `workspace_write("result.json", content)` |
| `workspace_exists` | 檢查檔案存在 | `workspace_exists("needs.json")` |
| `workspace_list` | 列出檔案 | `workspace_list(".")` |
| `codegen` | 執行環境指令 | `codegen("<observe>")` |
| `bash` | 執行命令 | `bash("echo hello")` |
| `grep` | 搜尋內容 | `grep("pattern", ".")` |
| `glob` | 檔案匹配 | `glob("*.json")` |
| `done` | 完成執行 | 表示skill執行完畢 |

## Skill型別

### 型別1：純Prompt驅動（推薦）

大多數Skill不需要程式設計，只需要清晰的描述：

```markdown
---
name: mood-check
description: Check and record current mood based on recent events.
outputs:
  - mood.json
---

# Mood Check

Analyze recent events and determine current mood.

## Input
- `observation.txt`: Current perception
- `emotion.json`: Current emotional state
- `memory.jsonl`: Recent memories (last 5 lines)

## Output
Write `mood.json`:
\`\`\`json
{
  "mood": "happy" | "sad" | "neutral" | "anxious" | "excited",
  "intensity": 0.0-1.0,
  "reason": "Brief explanation"
}
\`\`\`
```

### 型別2：帶Python指令碼

當需要確定性計算時，新增Python指令碼：

```
custom/skills/calculator/
├── SKILL.md
└── scripts/
    └── calc.py
```

SKILL.md:
```yaml
---
name: calculator
description: Perform precise numerical calculations.
script: scripts/calc.py
outputs:
  - result.json
---
```

calc.py:
```python
"""Calculator skill script."""
import argparse
import json
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", default="{}")
    ns = parser.parse_args()
    args = json.loads(ns.args_json)

    # 計算邏輯
    expression = args.get("expression", "0")
    try:
        result = eval(expression)  # 注意：實際使用時需要安全處理
        output = {"ok": True, "result": result}
    except Exception as e:
        output = {"ok": False, "error": str(e)}

    # 寫入輸出
    (Path.cwd() / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2)
    )
    print(json.dumps(output))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### 型別3：環境路由（codegen）

當Skill需要與環境互動時，使用codegen模式：

```yaml
---
name: interact
description: Interact with the simulated environment.
executor: codegen
---
```

## 最佳實踐

### 1. 保持單一職責

每個Skill只做一件事：

- ✅ `needs`: 管理生理需求
- ✅ `cognition`: 生成情緒和意圖
- ❌ `needs_and_emotion`: 做太多事情

### 2. 明確宣告輸出

```yaml
outputs:
  - needs.json        # 好：檔名明確
  - current_need.txt  # 好：檔名明確
```

### 3. 處理缺失檔案

Skill應該優雅處理輸入檔案不存在的情況：

```markdown
## Input Files
- `observation.txt`: Current observation (skip if missing)
- `needs.json`: Need state (use defaults if missing)
```

### 4. 提供示例

示例幫助LLM理解預期行為：

```markdown
## Example

**Input**:
\`\`\`
observation.txt: "You see a café across the street."
\`\`\`

**Output** (intention.json):
\`\`\`json
{
  "intention": "Visit the café for lunch",
  "priority": 2,
  "reasoning": "I'm feeling hungry and there's a café nearby."
}
\`\`\`
```

### 5. 避免冗餘描述

不需要告訴LLM"仔細思考"或"認真分析"，它會自然地做這些。

## 檔案結構約定

推薦使用以下目錄結構：

```
custom/skills/my-skill/
├── SKILL.md          # 必需：skill定義
├── scripts/          # 可選：Python指令碼
│   └── main.py
├── templates/        # 可選：模板檔案
│   └── prompt.jinja2
└── tests/            # 可選：測試
    └── test_skill.py
```

## 除錯技巧

1. **檢視workspace檔案**：檢查輸出檔案是否正確生成
2. **檢查tool_calls.jsonl**：檢視LLM呼叫了哪些工具
3. **簡化描述**：如果Skill行為異常，嘗試簡化描述
4. **新增示例**：示例通常能顯著改善LLM理解

## 示例：完整的Skill

```markdown
---
name: social-reflection
description: Reflect on recent social interactions and update relationship state.
outputs:
  - social_reflection.json
requires:
  - relationship
---

# Social Reflection

Reflect on recent social interactions and how they affect relationships.

## When to Use
- After a significant social interaction
- When feeling uncertain about a relationship
- Before making social decisions

## Input Files
- `observation.txt`: Current perception (may contain social events)
- `relationships.json`: Current relationship state
- `memory.jsonl`: Recent memories (last 10 lines)
- `emotion.json`: Current emotional state

## Execution Steps

1. Read `relationships.json` to understand current state
2. Read recent memories for social interaction context
3. Consider current emotional state
4. Reflect on how recent events affect relationships
5. Write reflection to `social_reflection.json`

## Output Format

\`\`\`json
{
  "reflection": "What I learned about my relationships",
  "relationship_changes": [
    {
      "agent_id": "2",
      "change": "increased trust",
      "reason": "Alice helped me when I was in trouble"
    }
  ],
  "social_goals": [
    "Spend more time with Alice",
    "Resolve conflict with Bob"
  ]
}
\`\`\`

## Example

**Input**:
- observation.txt: "Alice smiled and offered to help with my project."
- relationships.json: Agent 2 (Alice) is an acquaintance with trust 0.3

**Output**:
\`\`\`json
{
  "reflection": "Alice showed genuine kindness by offering help.",
  "relationship_changes": [
    {
      "agent_id": "2",
      "change": "increased trust and affection",
      "reason": "Alice's offer to help demonstrates reliability"
    }
  ],
  "social_goals": [
    "Accept Alice's help and build friendship"
  ]
}
\`\`\`
```

## 常見問題

**Q: Skill之間如何通訊？**

A: 透過workspace檔案。一個Skill寫入檔案，另一個Skill讀取。

**Q: 如何控制Skill執行順序？**

A: 使用`requires`欄位宣告依賴。Agent會在啟用時考慮這些依賴。

**Q: Skill可以呼叫其他Skill嗎？**

A: 不直接呼叫。透過workspace檔案松耦合，LLM決定何時啟用哪個Skill。

**Q: 如何測試Skill？**

A: 建立測試workspace，放置輸入檔案，執行agent，檢查輸出檔案。

## 進階主題

### 狀態管理

如果Skill需要維護狀態，寫入JSON檔案：

```json
{
  "state": "active",
  "progress": 0.5,
  "history": ["event1", "event2"]
}
```

### 與環境互動

使用`codegen`工具與環境互動：

```markdown
## Environment Actions

1. Observe: `codegen("<observe>")`
2. Move: `codegen("Move to {location}")`
3. Speak: `codegen("Say '{message}' to {target}")`
```

### 時間感知

Skill可以接收時間資訊：

```markdown
The `tick` and `time` fields are auto-injected by the framework.
Use them for time-dependent logic.
```
