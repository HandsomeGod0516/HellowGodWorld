#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日報生成器輔助指令碼

功能：
1. 收集今日記憶檔案內容
2. 解析待辦事項狀態
3. 生成格式化日報

使用方式：
    python report_helper.py --date 2026-03-06
    python report_helper.py  # 預設使用今天日期
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 嘗試從 jiuwenclaw.utils 匯入，如果失敗則使用環境變數或硬編碼路徑
try:
    from jiuwenclaw.utils import get_agent_root_dir
    _has_jiuwenclaw = True
except ImportError:
    _has_jiuwenclaw = False

# 修復 Windows 控制檯編碼問題
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def get_workspace_dir() -> Path:
    """獲取 Agent 根目錄（含 memory/、sessions/、skills/）。

    優先順序：
    1. jiuwenclaw.utils.get_agent_root_dir()（如果可用）
    2. JIUWENCLAW_DATA_DIR 環境變數 / agent
    3. JIUWENCLAW_AGENT_ROOT 環境變數
    4. 預設 ~/.jiuwenclaw/agent
    """
    if _has_jiuwenclaw:
        return get_agent_root_dir()

    # 檢查多例項環境變數
    env_workspace = os.getenv("JIUWENCLAW_DATA_DIR")
    if env_workspace:
        return Path(env_workspace) / "agent"

    # 檢查舊的 AGENT_ROOT 環境變數
    if "JIUWENCLAW_AGENT_ROOT" in os.environ:
        return Path(os.environ["JIUWENCLAW_AGENT_ROOT"])

    # 預設路徑
    home_agent = Path.home() / ".jiuwenclaw" / "agent"
    if home_agent.is_dir():
        return home_agent

    # 開發模式：包內 resources/agent
    script_dir = Path(__file__).resolve()
    pkg_agent = script_dir.parent.parent.parent  # daily-report -> skills -> agent
    if (pkg_agent / "memory").is_dir() or (pkg_agent / "skills").is_dir():
        return pkg_agent

    return home_agent


def read_file_safe(file_path: Path) -> str:
    """安全讀取檔案內容"""
    if not file_path.exists():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[讀取檔案失敗: {e}]"


def parse_todo_status(content: str) -> dict[str, list[dict]]:
    """解析待辦事項狀態

    支援兩種格式：
    1. Markdown checkbox: - [x] 任務 / - [ ] 任務
    2. YAML frontmatter: status: completed/running/waiting
    """
    result = {
        "completed": [],
        "running": [],
        "waiting": [],
        "cancelled": []
    }

    if not content:
        return result

    lines = content.split("\n")

    # 解析 Markdown checkbox 格式
    checkbox_pattern = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+)$")

    # 解析帶狀態標記的格式 (如: 1. [status:completed] 任務描述)
    status_pattern = re.compile(r"^\s*\d+\.\s*\[status:(\w+)\]\s*(.+)$", re.IGNORECASE)

    # 解析 YAML 格式的狀態
    yaml_status_pattern = re.compile(r"status:\s*(\w+)", re.IGNORECASE)

    current_task = None
    current_status = "waiting"

    for line in lines:
        # 嘗試 checkbox 格式
        checkbox_match = checkbox_pattern.match(line)
        if checkbox_match:
            checked = checkbox_match.group(1).lower() == "x"
            task_desc = checkbox_match.group(2).strip()
            status = "completed" if checked else "waiting"
            result[status].append({
                "description": task_desc,
                "status": status
            })
            continue

        # 嘗試狀態標記格式
        status_match = status_pattern.match(line)
        if status_match:
            status = status_match.group(1).lower()
            task_desc = status_match.group(2).strip()
            if status in result:
                result[status].append({
                    "description": task_desc,
                    "status": status
                })
            continue

        # 嘗試提取 YAML 狀態
        yaml_match = yaml_status_pattern.search(line)
        if yaml_match:
            current_status = yaml_match.group(1).lower()

        # 解析普通任務行 (如: 1. 任務描述)
        task_match = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if task_match:
            task_desc = task_match.group(1).strip()
            if task_desc and current_status in result:
                result[current_status].append({
                    "description": task_desc,
                    "status": current_status
                })
                current_status = "waiting"  # 重置狀態

    return result


def extract_work_summary(content: str) -> list[str]:
    """從記憶檔案中提取工作摘要

    提取要點：
    - 以 - 或 * 開頭的列表項
    - 以 ## 標題分隔的內容
    """
    summaries = []

    if not content:
        return summaries

    lines = content.split("\n")
    in_section = False
    current_section = []

    for line in lines:
        stripped = line.strip()

        # 跳過空行和註釋
        if not stripped or stripped.startswith("<!--"):
            continue

        # 檢測標題
        if stripped.startswith("##"):
            if in_section and current_section:
                summaries.append("\n".join(current_section))
                current_section = []
            in_section = True
            continue

        # 收集列表項
        if stripped.startswith("-") or stripped.startswith("*"):
            item = stripped.lstrip("-* ").strip()
            if item:
                current_section.append(f"- {item}")

    # 新增最後一個 section
    if current_section:
        summaries.append("\n".join(current_section))

    return summaries


def find_latest_todo_file(workspace_dir: Path) -> Path | None:
    """查詢最新的 todo.md 檔案"""
    session_dir = workspace_dir / "session"

    if not session_dir.exists():
        return None

    todo_files = list(session_dir.rglob("todo.md"))

    if not todo_files:
        return None

    # 按修改時間排序，返回最新的
    todo_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return todo_files[0]


def generate_report(
    date_str: str,
    memory_content: str,
    todo_data: dict[str, list[dict]],
    long_term_memory: str = ""
) -> dict[str, Any]:
    """生成日報資料"""

    # 統計任務數量
    completed_count = len(todo_data["completed"])
    running_count = len(todo_data["running"])
    waiting_count = len(todo_data["waiting"])

    # 提取工作摘要
    work_summaries = extract_work_summary(memory_content)

    # 生成日報
    report = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "statistics": {
            "completed": completed_count,
            "running": running_count,
            "waiting": waiting_count
        },
        "tasks": {
            "completed": [t["description"] for t in todo_data["completed"]],
            "running": [t["description"] for t in todo_data["running"]],
            "waiting": [t["description"] for t in todo_data["waiting"]]
        },
        "work_summary": work_summaries,
        "long_term_context": long_term_memory[:500] if long_term_memory else ""  # 擷取前500字元
    }

    return report


def format_report_markdown(report: dict[str, Any]) -> str:
    """將日報資料格式化為 Markdown"""

    lines = [
        f"# 📋 工作日報 - {report['date']}",
        "",
        "## 📊 今日概覽",
        f"- 完成任務: {report['statistics']['completed']} 項",
        f"- 進行中: {report['statistics']['running']} 項",
        f"- 待處理: {report['statistics']['waiting']} 項",
        ""
    ]

    # 已完成任務
    if report["tasks"]["completed"]:
        lines.append("## ✅ 已完成任務")
        for task in report["tasks"]["completed"]:
            lines.append(f"- {task}")
        lines.append("")

    # 進行中任務
    if report["tasks"]["running"]:
        lines.append("## 🔄 進行中任務")
        for task in report["tasks"]["running"]:
            lines.append(f"- {task}")
        lines.append("")

    # 今日工作記錄
    lines.append("## 📝 今日工作記錄")
    if report["work_summary"]:
        for summary in report["work_summary"]:
            lines.append(summary)
    else:
        lines.append("- 暫無工作記錄")
    lines.append("")

    # 明日計劃
    lines.append("## 🔜 明日計劃")
    if report["tasks"]["waiting"]:
        for task in report["tasks"]["waiting"]:
            lines.append(f"- {task}")
    else:
        lines.append("- 待補充")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="日報生成器輔助指令碼")
    parser.add_argument(
        "--date",
        default=None,
        help="指定日期 (YYYY-MM-DD)，預設使用今天"
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "all"],
        default="all",
        help="輸出格式: json, markdown, all (預設: all)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="輸出檔案路徑，不指定則輸出到標準輸出"
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="指定 workspace 目錄路徑"
    )

    args = parser.parse_args()

    # 確定日期
    if args.date:
        try:
            date_str = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            print(f"錯誤: 日期格式不正確，請使用 YYYY-MM-DD 格式", file=sys.stderr)
            sys.exit(1)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # 獲取 workspace 目錄
    if args.workspace:
        workspace_dir = Path(args.workspace)
    else:
        workspace_dir = get_workspace_dir()

    print(f"使用 workspace 目錄: {workspace_dir}", file=sys.stderr)

    # 收集資料
    memory_dir = workspace_dir / "memory"

    # 1. 讀取今日記憶
    today_memory_file = memory_dir / f"{date_str}.md"
    memory_content = read_file_safe(today_memory_file)

    # 2. 讀取長期記憶
    long_term_memory_file = memory_dir / "MEMORY.md"
    long_term_memory = read_file_safe(long_term_memory_file)

    # 3. 查詢並讀取最新的 todo.md
    todo_file = find_latest_todo_file(workspace_dir)
    todo_content = ""
    if todo_file:
        todo_content = read_file_safe(todo_file)
        print(f"讀取待辦檔案: {todo_file}", file=sys.stderr)

    # 4. 解析待辦狀態
    todo_data = parse_todo_status(todo_content)

    # 5. 生成日報資料
    report = generate_report(
        date_str=date_str,
        memory_content=memory_content,
        todo_data=todo_data,
        long_term_memory=long_term_memory
    )

    # 6. 格式化輸出
    output_content = ""

    if args.format in ["json", "all"]:
        json_output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.format == "json":
            output_content = json_output
        else:
            output_content += f"=== JSON 資料 ===\n{json_output}\n\n"

    if args.format in ["markdown", "all"]:
        md_output = format_report_markdown(report)
        if args.format == "markdown":
            output_content = md_output
        else:
            output_content += f"=== Markdown 日報 ===\n{md_output}"

    # 7. 輸出結果
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_content, encoding="utf-8")
        print(f"日報已儲存到: {output_path}", file=sys.stderr)
    else:
        print(output_content)


if __name__ == "__main__":
    main()
