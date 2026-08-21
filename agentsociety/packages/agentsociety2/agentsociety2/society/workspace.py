"""工作區管理 CLI - 用於初始化和管理 AgentSociety2 工作區

提供以下功能：
1. 初始化自定義模組模板 (custom/)
2. 建立使用者資料目錄 (user_data/)
3. 建立文獻目錄結構 (papers/)
4. 建立 .agentsociety 目錄結構和配置檔案
5. 下載必要的資料檔案（地圖等）
6. 生成模組描述 JSON 檔案
"""

import argparse
import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
import aiohttp


def get_custom_template_path() -> Path:
    """獲取自定義模組模板路徑"""
    # 從 agentsociety2.custom 包中獲取路徑
    from agentsociety2.custom import __file__ as custom_init
    return Path(custom_init).parent


def init_custom_modules(target_dir: Path, force: bool = False) -> dict:
    """
    初始化自定義模組目錄，複製模板檔案

    Args:
        target_dir: 目標工作區根目錄
        force: 是否強制覆蓋已存在的檔案

    Returns:
        包含操作結果的字典
    """
    result = {
        "success": False,
        "message": "",
        "created": [],
        "skipped": [],
        "errors": []
    }

    try:
        custom_dir = target_dir / "custom"
        template_path = get_custom_template_path()

        if not template_path.exists():
            result["errors"].append(f"Template path not found: {template_path}")
            result["message"] = f"Template not found at {template_path}"
            return result

        # 建立 custom 目錄結構
        (custom_dir / "agents").mkdir(parents=True, exist_ok=True)
        result["created"].append("custom/agents/")

        (custom_dir / "envs").mkdir(parents=True, exist_ok=True)
        result["created"].append("custom/envs/")

        (custom_dir / "skills").mkdir(parents=True, exist_ok=True)
        result["created"].append("custom/skills/")

        # 複製示例檔案到 custom/agents/ 和 custom/envs/
        # 這些是直接可用的示例，不是放在 examples 子目錄中
        agents_src = template_path / "agents" / "examples"
        if agents_src.exists():
            for py_file in agents_src.glob("*.py"):
                dst = custom_dir / "agents" / py_file.name
                if not dst.exists() or force:
                    shutil.copy2(py_file, dst)
                    result["created"].append(f"custom/agents/{py_file.name}")

        envs_src = template_path / "envs" / "examples"
        if envs_src.exists():
            for py_file in envs_src.glob("*.py"):
                dst = custom_dir / "envs" / py_file.name
                if not dst.exists() or force:
                    shutil.copy2(py_file, dst)
                    result["created"].append(f"custom/envs/{py_file.name}")

        # 複製 skills 示例
        skills_src = template_path / "skills" / "examples"
        if skills_src.exists():
            for skill_dir in skills_src.iterdir():
                if skill_dir.is_dir():
                    dst = custom_dir / "skills" / skill_dir.name
                    if not dst.exists() or force:
                        shutil.copytree(str(skill_dir), str(dst), dirs_exist_ok=True)
                        result["created"].append(f"custom/skills/{skill_dir.name}/")

        # 複製 __init__.py 檔案
        init_files = [
            (template_path / "__init__.py", custom_dir / "__init__.py"),
            (template_path / "agents" / "__init__.py", custom_dir / "agents" / "__init__.py"),
            (template_path / "envs" / "__init__.py", custom_dir / "envs" / "__init__.py"),
        ]

        for src, dst in init_files:
            if src.exists():
                if not dst.exists() or force:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    result["created"].append(str(dst.relative_to(target_dir)))
                else:
                    result["skipped"].append(f"{dst.relative_to(target_dir)} (already exists)")

        # 建立 custom/README.md（工作區專用版本）
        custom_readme = custom_dir / "README.md"
        if not custom_readme.exists() or force:
            custom_readme_content = """# Custom Modules

本目錄用於存放自定義的 Agent 和環境模組。

## 目錄結構

- `agents/` - 自定義 Agent 類
- `envs/` - 自定義環境模組
- `skills/` - 自定義 Agent Skills（給 Agent 新增行為能力）

## 開發指南

### 建立自定義 Agent

1. 在 `agents/` 目錄下建立新的 `.py` 檔案
2. 繼承自 `AgentBase`
3. 實現 `mcp_description()` 類方法
4. 實現必需方法：`ask()`, `step()`, `dump()`, `load()`

```python
from agentsociety2.agent.base import AgentBase
from datetime import datetime, timezone

class MyAgent(AgentBase):
    @classmethod
    def mcp_description(cls) -> str:
        return \"\"\"MyAgent: 我的自定義 Agent\"\"\"

    async def ask(self, message: str, readonly: bool = True) -> str:
        return f"Answer to: {message}"

    async def step(self, tick: int, t: datetime) -> str:
        return f"Step {tick}"

    async def dump(self) -> dict:
        return {"id": self._id}

    async def load(self, dump_data: dict):
        self._id = dump_data.get("id", self._id)
```

### 建立自定義環境模組

1. 在 `envs/` 目錄下建立新的 `.py` 檔案
2. 繼承自 `EnvBase`
3. 使用 `@tool` 裝飾器註冊工具方法

```python
from agentsociety2.env import EnvBase, tool

class MyEnv(EnvBase):
    @classmethod
    def mcp_description(cls) -> str:
        return \"\"\"MyEnv: 我的自定義環境\"\"\"

    @tool(readonly=True, kind="observe")
    async def get_state(self, agent_id: int) -> dict:
        return {"agent_id": agent_id}

    @tool(readonly=False)
    async def do_action(self, agent_id: int, action: str) -> dict:
        return {"agent_id": agent_id, "action": action}

    async def step(self, tick: int, t: datetime):
        self.t = t
```

### 建立自定義 Agent Skill

1. 在 `skills/` 目錄下建立新目錄（如 `my-skill/`）
2. 新增 `SKILL.md`（行為規範，必需）和可選的 `scripts/<skill_name>.py`（subprocess 模式）

```
skills/my-skill/
├── SKILL.md
├── _order.txt          # 可選，定義載入優先順序
└── scripts/
    └── my-skill.py     # （可選）subprocess 指令碼：--args-json + 寫入 AGENT_WORK_DIR
```

```python
# skills/my-skill/scripts/my-skill.py
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", default="{}")
    ns = parser.parse_args()
    args = json.loads(ns.args_json or "{}")
    result = {"ok": True, "summary": f"MySkill: executed (tick={args.get('tick')})"}
    Path("my_skill_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 註冊和測試

1. 在 VSCode 中執行"掃描自定義模組"命令
2. 執行"測試自定義模組"驗證功能
3. 使用"掃描 Agent Skills"發現新的 skill

## 示例

本目錄已包含示例檔案：
- `agents/` 目錄下有 Agent 示例
- `envs/` 目錄下有環境模組示例
- `skills/` 目錄用於放置自定義 Agent Skill

這些示例可以直接執行測試，也可以作為開發參考。
"""
            with open(custom_readme, "w", encoding="utf-8") as f:
                f.write(custom_readme_content)
            result["created"].append("custom/README.md")

        result["success"] = True
        result["message"] = f"Custom modules initialized at {custom_dir}"

    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to initialize custom modules: {e}"

    return result


async def download_map_file(target_dir: Path, timeout: int = 300) -> dict:
    """
    下載地圖檔案到工作區

    Args:
        target_dir: 目標工作區根目錄
        timeout: 超時時間（秒）

    Returns:
        包含操作結果的字典
    """
    result = {
        "success": False,
        "message": "",
        "file_path": "",
        "errors": []
    }

    map_url = "https://tsinghua-agentsociety.oss-cn-beijing.aliyuncs.com/data/map/beijing_map.pb"
    data_dir = target_dir / ".agentsociety" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    map_file_path = data_dir / "beijing_map.pb"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(map_url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                response.raise_for_status()

                with open(map_file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)

        result["success"] = True
        result["message"] = f"Map file downloaded to {map_file_path}"
        result["file_path"] = str(map_file_path.resolve())

    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to download map file: {e}"
        # 即使下載失敗，也返回預期的檔案路徑
        result["file_path"] = str(map_file_path.resolve())

    return result


def create_module_info_files(target_dir: Path) -> dict:
    """
    建立模組描述 JSON 檔案到 .agentsociety/ 目錄

    Args:
        target_dir: 目標工作區根目錄

    Returns:
        包含操作結果的字典
    """
    result = {
        "success": False,
        "message": "",
        "created": [],
        "errors": []
    }

    try:
        from agentsociety2.registry import discover_and_register_builtin_modules, get_registry

        # 確保內建模組已載入
        registry = get_registry()
        if not registry._builtin_loaded:
            discover_and_register_builtin_modules(registry)

        # 建立目錄
        agents_dir = target_dir / ".agentsociety" / "agent_classes"
        env_dir = target_dir / ".agentsociety" / "env_modules"
        agents_dir.mkdir(parents=True, exist_ok=True)
        env_dir.mkdir(parents=True, exist_ok=True)

        # 建立 agent_classes/*.json
        for module_type, agent_class in registry.list_agent_modules():
            try:
                if hasattr(agent_class, "mcp_description"):
                    description = agent_class.mcp_description()
                else:
                    description = agent_class.__doc__ or "No description available"
            except Exception:
                description = agent_class.__doc__ or "No description available"

            agent_info = {
                "type": module_type,
                "class_name": agent_class.__name__,
                "description": description,
                "is_custom": getattr(agent_class, "_is_custom", False),
            }

            with open(agents_dir / f"{module_type}.json", "w", encoding="utf-8") as f:
                json.dump(agent_info, f, ensure_ascii=False, indent=2)
            result["created"].append(f".agentsociety/agent_classes/{module_type}.json")

        # 建立 env_modules/*.json
        for module_type, env_class in registry.list_env_modules():
            try:
                if hasattr(env_class, "mcp_description"):
                    description = env_class.mcp_description()
                else:
                    description = env_class.__doc__ or "No description available"
            except Exception:
                description = env_class.__doc__ or "No description available"

            module_info = {
                "type": module_type,
                "class_name": env_class.__name__,
                "description": description,
                "is_custom": getattr(env_class, "_is_custom", False),
            }

            with open(env_dir / f"{module_type}.json", "w", encoding="utf-8") as f:
                json.dump(module_info, f, ensure_ascii=False, indent=2)
            result["created"].append(f".agentsociety/env_modules/{module_type}.json")

        result["success"] = True
        result["message"] = "Module info files created"

    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to create module info files: {e}"

    return result


def create_path_md(target_dir: Path) -> dict:
    """
    建立 .agentsociety/path.md 工作區路徑記憶檔案

    Args:
        target_dir: 目標工作區根目錄

    Returns:
        包含操作結果的字典
    """
    result = {
        "success": False,
        "message": "",
        "errors": []
    }

    path_md_content = """# Workspace Path Memory

This file records descriptions of high-value file paths and their meanings to help the Agent run with long-term memory.

## High-Value Files

- `TOPIC.md`: The core research topic and goals for the current simulation experiment. Always read this file first to understand your mission.
- `.agentsociety/agent_classes/*.json`: JSON files containing detailed information about all supported agent classes, including their types and capabilities.
- `.agentsociety/env_modules/*.json`: JSON files containing detailed information about all supported environment modules that can be used to build simulation worlds.
- `.agentsociety/prefill_params.json`: Pre-filled parameters for modules to avoid repetitive input.

## Ignore Files

- `papers/`: The directory for storing literature search results or user-uploaded literature files. You SHOULD NOT read this directory directly, but use the `load_literature` tool to load the literature files.

## Progressive Context Loading

Instead of using specialized discovery tools, you should:
1. Read `.agentsociety/path.md` to understand the workspace structure.
2. List these directories to see available components.
3. Read specific JSON files as needed to gather detailed information about agent classes or environment modules.

## Custom Modules

- `custom/agents/`: Custom agent classes created by the user.
- `custom/envs/`: Custom environment modules created by the user.
"""

    dot_agentsociety_dir = target_dir / ".agentsociety"
    dot_agentsociety_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(dot_agentsociety_dir / "path.md", "w", encoding="utf-8") as f:
            f.write(path_md_content)
        result["success"] = True
        result["message"] = "path.md created"
    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to create path.md: {e}"

    return result


def create_prefill_params(target_dir: Path, map_file_path: str) -> dict:
    """
    建立 .agentsociety/prefill_params.json 預填充引數檔案

    Args:
        target_dir: 目標工作區根目錄
        map_file_path: 地圖檔案路徑

    Returns:
        包含操作結果的字典
    """
    result = {
        "success": False,
        "message": "",
        "errors": []
    }

    dot_agentsociety_dir = target_dir / ".agentsociety"
    data_dir = dot_agentsociety_dir / "data"

    prefill_data = {
        "version": "1.0",
        "env_modules": {
            "mobility_space": {
                "file_path": map_file_path,
                "home_dir": str(data_dir.resolve()),
            },
        },
        "agents": {},
    }

    try:
        with open(dot_agentsociety_dir / "prefill_params.json", "w", encoding="utf-8") as f:
            json.dump(prefill_data, f, ensure_ascii=False, indent=2)
        result["success"] = True
        result["message"] = "prefill_params.json created"
    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to create prefill_params.json: {e}"

    return result


async def init_workspace(target_dir: Path, topic: str = "", components: list[str] = None, force: bool = False) -> dict:
    """
    初始化工作區，建立所需的目錄結構

    Args:
        target_dir: 目標工作區根目錄
        topic: 研究主題
        components: 要建立的元件列表，可選值: "custom", "user_data", "papers", "agentsociety"
        force: 是否強制覆蓋已存在的檔案

    Returns:
        包含操作結果的字典
    """
    if components is None:
        components = ["custom", "user_data", "papers", "agentsociety"]

    result = {
        "success": False,
        "message": "",
        "created": [],
        "skipped": [],
        "errors": []
    }

    try:
        target_dir.mkdir(parents=True, exist_ok=True)

        # 建立 TOPIC.md
        if topic:
            topic_file = target_dir / "TOPIC.md"
            if not topic_file.exists() or force:
                topic_content = f"""# Research Topic

{topic}

## Description

[Describe your research topic here]

## Hypotheses

[Generated hypotheses will appear here]
"""
                with open(topic_file, "w", encoding="utf-8") as f:
                    f.write(topic_content)
                result["created"].append("TOPIC.md")

        # 建立 user_data 目錄
        if "user_data" in components:
            user_data_dir = target_dir / "user_data"
            if not user_data_dir.exists():
                user_data_dir.mkdir(parents=True, exist_ok=True)
                result["created"].append("user_data/")
            else:
                result["skipped"].append("user_data/ (already exists)")

        # 建立 papers 目錄
        if "papers" in components:
            papers_dir = target_dir / "papers"
            if not papers_dir.exists():
                papers_dir.mkdir(parents=True, exist_ok=True)

                # 建立 literature_index.json
                index_file = papers_dir / "literature_index.json"
                if not index_file.exists():
                    index_data = {
                        "version": "1.0",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "entries": []
                    }
                    index_file.write_text(json.dumps(index_data, indent=2, ensure_ascii=False))
                    result["created"].append("papers/literature_index.json")
            else:
                result["skipped"].append("papers/ (already exists)")

        # 建立 custom 目錄
        if "custom" in components:
            custom_result = init_custom_modules(target_dir, force=force)
            result["created"].extend(custom_result["created"])
            result["skipped"].extend(custom_result["skipped"])
            result["errors"].extend(custom_result["errors"])

        # 建立 .agentsociety 目錄結構和檔案
        if "agentsociety" in components:
            # 建立目錄
            (target_dir / ".agentsociety" / "agent_classes").mkdir(parents=True, exist_ok=True)
            (target_dir / ".agentsociety" / "env_modules").mkdir(parents=True, exist_ok=True)
            (target_dir / ".agentsociety" / "data").mkdir(parents=True, exist_ok=True)
            (
                target_dir
                / ".agentsociety"
                / "custom_env_skill"
                / "runs"
            ).mkdir(parents=True, exist_ok=True)

            # 下載地圖檔案
            download_result = await download_map_file(target_dir)
            if download_result["success"]:
                result["created"].append(".agentsociety/data/beijing_map.pb")
            else:
                result["errors"].append(f"Map download failed: {download_result['message']}")
                # 即使下載失敗也繼續，建立空的 data 目錄記錄
                result["skipped"].append(".agentsociety/data/beijing_map.pb (download failed)")

            map_file_path = download_result["file_path"]

            # 建立模組資訊檔案
            module_info_result = create_module_info_files(target_dir)
            result["created"].extend(module_info_result["created"])
            result["errors"].extend(module_info_result["errors"])

            # 建立 path.md
            path_md_result = create_path_md(target_dir)
            if path_md_result["success"]:
                result["created"].append(".agentsociety/path.md")
            result["errors"].extend(path_md_result["errors"])

            # 建立 prefill_params.json
            prefill_result = create_prefill_params(target_dir, map_file_path)
            if prefill_result["success"]:
                result["created"].append(".agentsociety/prefill_params.json")
            result["errors"].extend(prefill_result["errors"])

        if not result["errors"] or any("failed" in e.lower() for e in result["errors"]):
            # 只有沒有嚴重錯誤時才標記為成功
            result["success"] = True
            result["message"] = f"Workspace initialized at {target_dir}"

    except Exception as e:
        result["errors"].append(str(e))
        result["message"] = f"Failed to initialize workspace: {e}"

    return result


def main():
    """工作區管理 CLI 入口"""
    parser = argparse.ArgumentParser(
        description="AgentSociety2 Workspace Management"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init 命令
    init_parser = subparsers.add_parser("init", help="Initialize workspace components")
    init_parser.add_argument(
        "--target-dir",
        type=str,
        default=".",
        help="Target workspace directory (default: current directory)"
    )
    init_parser.add_argument(
        "--topic",
        type=str,
        default="",
        help="Research topic"
    )
    init_parser.add_argument(
        "--components",
        type=str,
        default="custom,user_data,papers,agentsociety",
        help="Components to create: custom, user_data, papers, agentsociety (default: all)"
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing files"
    )
    init_parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON"
    )

    # init-custom 命令
    custom_parser = subparsers.add_parser("init-custom", help="Initialize custom modules only")
    custom_parser.add_argument(
        "--target-dir",
        type=str,
        default=".",
        help="Target workspace directory (default: current directory)"
    )
    custom_parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing files"
    )
    custom_parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    target_dir = Path(args.target_dir).resolve()

    if args.command == "init":
        components = [c.strip() for c in args.components.split(",")]
        # 使用 asyncio 執行非同步函式
        result = asyncio.run(init_workspace(target_dir, topic=args.topic, components=components, force=args.force))
    elif args.command == "init-custom":
        result = init_custom_modules(target_dir, force=args.force)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["success"]:
            print(f"✓ {result['message']}")
            if result["created"]:
                print("\nCreated:")
                for item in result["created"]:
                    print(f"  - {item}")
            if result["skipped"]:
                print("\nSkipped:")
                for item in result["skipped"]:
                    print(f"  - {item}")
        else:
            print(f"✗ {result['message']}", file=__import__("sys").stderr)
            if result["errors"]:
                print("\nErrors:")
                for error in result["errors"]:
                    print(f"  - {error}")
            exit(1)


if __name__ == "__main__":
    main()
