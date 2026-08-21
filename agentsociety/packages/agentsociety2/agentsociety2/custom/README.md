# 自定义模块开发指南

欢迎使用 AgentSociety2 自定义模块功能！本指南将帮助您创建和使用自定义的 Agent 和环境模块。

## 目录结构

```
custom/
├── agents/              # 自定义 Agent
│   └── examples/        # 官方示例（参考用）
├── envs/                # 自定义环境模块
│   └── examples/        # 官方示例（参考用）
├── skills/              # 自定义 Agent Skills
│   └── examples/        # 官方示例（参考用）
│       └── my-custom-skill/
│           ├── SKILL.md
│           └── scripts/my-custom-skill.py
└── README.md            # 本文档
```

## 快速开始

### 1. 创建自定义 Agent

在 `custom/agents/` 目录下创建新的 `.py` 文件，例如 `my_agent.py`：

```python
from agentsociety2.agent.base import AgentBase
from datetime import datetime

class MyAgent(AgentBase):
    """我的自定义 Agent"""

    @classmethod
    def mcp_description(cls) -> str:
        return """MyAgent: 我的自定义 Agent

这是我的第一个自定义 Agent。
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """回答问题"""
        prompt = f"问题：{message}\n请回答："
        response = await self.acompletion([{"role": "user", "content": prompt}])
        return response.choices[0].message.content or ""

    async def step(self, tick: int, t: datetime) -> str:
        """执行仿真步骤"""
        return f"Agent {self.id} 执行步骤"

    async def dump(self) -> dict:
        """序列化状态"""
        return {"id": self._id, "profile": self._profile}

    async def load(self, dump_data: dict):
        """加载状态"""
        self._id = dump_data.get("id", self._id)
        self._profile = dump_data.get("profile", self._profile)
```

### 2. 创建自定义环境模块

在 `custom/envs/` 目录下创建新的 `.py` 文件，例如 `my_env.py`：

```python
from agentsociety2.env import EnvBase, tool
from datetime import datetime

class MyEnv(EnvBase):
    """我的自定义环境"""

    def __init__(self, config=None):
        super().__init__()
        # 初始化你的环境状态

    @classmethod
    def mcp_description(cls) -> str:
        return """MyEnv: 我的自定义环境

这是我的第一个自定义环境。
"""

    @tool(readonly=True, kind="observe")
    async def get_state(self, agent_id: int) -> dict:
        """获取状态（观察工具）"""
        return {"agent_id": agent_id, "state": "正常"}

    @tool(readonly=False)
    async def do_action(self, agent_id: int, action: str) -> dict:
        """执行操作（修改工具）"""
        return {"agent_id": agent_id, "action": action, "result": "成功"}

    async def step(self, tick: int, t: datetime):
        """环境步骤"""
        self.t = t
```

### 3. 扫描和注册

创建代码后，在 VSCode 中运行命令：

```
AgentSociety: 扫描自定义模块
```

或调用 API：

```bash
curl -X POST http://localhost:8001/api/v1/custom/scan \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

### 4. 测试验证

在 VSCode 中运行命令：

```
AgentSociety: 测试自定义模块
```

或调用 API：

```bash
curl -X POST http://localhost:8001/api/v1/custom/test \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

系统会在内存中运行测试并返回结果。

> 注意：扫描/注册规则（与后端 scanner/registry 一致）
>
> - 只扫描工作区的 `custom/agents/**/*.py` 与 `custom/envs/**/*.py`
> - 路径中包含 `examples/` 的文件会被跳过（示例仅供参考，不参与注册）
> - **类必须在该文件内定义**（不能仅 import 后 re-export），否则不会被接受/注册

## 详解

### Agent 必需方法

所有自定义 Agent 必须实现以下方法：

| 方法 | 说明 |
|------|------|
| `mcp_description()` | 返回模块描述（类方法，**建议覆盖**；`AgentBase` 有默认描述） |
| `ask(message, readonly)` | 回答来自环境的问题 |
| `step(tick, t)` | 执行一个仿真步骤 |
| `dump()` | 序列化 Agent 状态 |
| `load(dump_data)` | 从字典加载状态 |

### 环境模块必需方法

所有自定义环境模块必须实现：

| 方法 | 说明 |
|------|------|
| `mcp_description()` | 返回模块描述（类方法） |
| `step(tick, t)` | 执行环境步骤 |
| 使用 `@tool` 装饰器注册至少一个工具方法 |

### @tool 装饰器参数

```python
@tool(
    readonly=True,           # 是否只读
    kind="observe",          # 工具类型: "observe", "statistics", 或 None
    name="custom_name",      # 自定义工具名（可选）
    description="描述"       # 工具描述（可选）
)
async def my_tool(self, agent_id: int) -> dict:
    """工具方法"""
    pass
```

**工具类型：**
- `kind="observe"`: 观察工具，只能有一个参数（agent_id），必须是 readonly=True
- `kind="statistics"`: 统计工具，只能有 self 参数，必须是 readonly=True
- `kind=None`: 普通工具，可以有多个参数，可以是 readonly=False

## API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/custom/scan` | POST | 扫描并注册自定义模块 |
| `/api/v1/custom/test` | POST | 测试自定义模块 |
| `/api/v1/custom/clean` | POST | 清理自定义模块配置 |
| `/api/v1/custom/list` | GET | 列出已注册的自定义模块 |
| `/api/v1/custom/status` | GET | 获取模块状态概览 |

## VSCode 命令

| 命令 | 功能 |
|------|------|
| `agentsociety.scanCustomModules` | 扫描自定义模块 |
| `agentsociety.testCustomModules` | 测试自定义模块 |
| `agentsociety.cleanCustomModules` | 清理自定义模块配置 |

## 创建自定义 Agent Skill

每个 PersonAgent 就是一个独立的 Claude-like agent——它从 skill catalog 中选择技能、读取指令、调用工具完成任务。**skill 作者不需要了解 PersonAgent 的内部实现**，只需写一个 `SKILL.md`。

### 目录结构

```
custom/skills/my-skill/
├── SKILL.md              # YAML frontmatter + 行为指令（必需）
└── scripts/my-skill.py   # （可选）subprocess 脚本
```

### SKILL.md frontmatter

只需要三个字段：

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | 是 | skill 唯一标识 |
| `description` | 是 | 一句话描述（出现在 agent 的 skill catalog 里） |
| `script` | 否 | subprocess 脚本路径（如 `scripts/my-skill.py`） |
| `requires` | 否 | 依赖列表（activate 时自动激活依赖） |

```yaml
---
name: my-skill
description: One-line description of what this skill does.
---
```

### 两种模式

#### Prompt-only（推荐）

不声明 `script`。agent `activate_skill` 后，`SKILL.md` 正文作为行为指令注入上下文，agent 用内置工具（`bash` / `codegen` / `workspace_*` / `glob` / `grep`）完成任务。

#### Subprocess

声明 `script: scripts/my-skill.py`。agent 调用 `execute_skill` 时框架以子进程运行脚本：

- 参数：`--args-json '{...}'`（由 LLM 决定传什么）
- 环境变量：`SKILL_NAME` / `SKILL_DIR` / `AGENT_WORK_DIR`
- 产物：写入 `AGENT_WORK_DIR`（每个 agent 独立目录）

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

### Agent 可用的工具（skill 作者须知）

当 agent 激活你的 skill 后，它能用以下工具执行你的指令：

| 工具 | 用途 |
|------|------|
| `codegen` | 向仿真环境发送指令（观察、行动等） |
| `bash` | 在 agent workspace 运行 shell 命令 |
| `workspace_read/write/list` | 读写 agent workspace 文件 |
| `glob` / `grep` | 在 workspace 搜索文件 |
| `execute_skill` | 运行另一个 skill 的 subprocess 脚本 |
| `activate_skill` | 加载另一个 skill 的指令 |

### 扫描注册

```bash
curl -X POST http://localhost:8001/api/v1/agent-skills/scan \
  -H "Content-Type: application/json" \
  -d '{"workspace_path": "/path/to/workspace"}'
```

## 示例

查看 `custom/agents/examples/`、`custom/envs/examples/` 和 `custom/skills/examples/` 获取更多示例：

- `simple_agent.py` - 基础 Agent 示例
- `advanced_agent.py` - 带记忆和情绪的 Agent
- `simple_env.py` - 基础环境示例
- `advanced_env.py` - 资源管理环境
- `my-custom-skill/` - Agent Skill 示例

## 常见问题

### Q: 我的模块为什么没有被扫描到？

A: 检查以下几点：
1. 文件是否在 `custom/agents/` 或 `custom/envs/` 目录（不是 `examples/` 子目录）
2. 是否正确继承 `AgentBase` 或 `EnvBase`
3. 是否实现了所有必需方法
4. 文件名不要以 `__` 开头

### Q: 如何调试自定义模块？

A: 使用"测试自定义模块"命令，系统会在内存中：
1. 动态导入自定义模块
2. 运行测试验证
3. 显示详细的测试输出

### Q: 如何清理自定义模块？

A: 运行"清理自定义模块"命令，会删除所有 `is_custom=true` 的 JSON 配置。

### Q: 自定义模块可以和内置模块一起使用吗？

A: 可以！扫描后，自定义模块会与内置模块一起出现在可用列表中。

## 最佳实践

1. **命名规范**
   - Agent 类名以 `Agent` 结尾
   - 环境类名以 `Env` 结尾
   - 文件名使用小写和下划线

2. **错误处理**
   - 在 `ask()` 方法中捕获异常
   - 返回有意义的错误信息

3. **状态管理**
   - 使用 `dump()` 和 `load()` 正确保存/恢复状态
   - 记录重要的状态变化

4. **工具设计**
   - 观察工具用 `kind="observe"`
   - 统计工具用 `kind="statistics"`
   - 操作工具用 `kind=None` 和 `readonly=False`

## 技术支持

如有问题，请查看：
- 示例代码：`custom/*/examples/`
- API 文档：`http://localhost:8001/docs`
