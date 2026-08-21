#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日報/週報/月報生成入口指令碼（獨立版）

使用方式：
    python run_report.py daily [date]           # 生成日報
    python run_report.py weekly [end_date]      # 生成周報
    python run_report.py monthly [year] [month] # 生成月報
"""

import argparse
import io
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# 嘗試從 jiuwenclaw.utils 匯入，如果失敗則使用環境變數或硬編碼路徑
try:
    from jiuwenclaw.utils import get_agent_root_dir, get_env_file
    _has_jiuwenclaw = True
except ImportError:
    _has_jiuwenclaw = False

# 載入配置環境變數
try:
    from dotenv import load_dotenv

    if _has_jiuwenclaw:
        _cfg_env = get_env_file()
    else:
        env_workspace = os.getenv("JIUWENCLAW_DATA_DIR")
        if env_workspace:
            _cfg_env = Path(env_workspace) / "config" / ".env"
        else:
            _cfg_env = Path.home() / ".jiuwenclaw" / "config" / ".env"
    if _cfg_env.exists():
        load_dotenv(_cfg_env)
except ImportError:
    pass  # dotenv 未安裝時跳過

# 修復 Windows 編碼問題 - 必須在所有輸出之前
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    # 強制設定 stdout/stderr 為 UTF-8
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import imaplib
    import email
    from email.header import decode_header
    IMAP_AVAILABLE = True
    # 註冊ID命令（163郵箱需要）
    imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED')
except ImportError:
    IMAP_AVAILABLE = False
    imaplib = None

# 指令碼與路徑：Git 用倉庫根；記憶/會話/報告用 Agent 資料目錄
SKILL_DIR = Path(__file__).parent
PACKAGE_ROOT = SKILL_DIR.parent.parent.parent.parent
REPO_ROOT = PACKAGE_ROOT.parent

# Agent 根目錄：優先使用 jiuwenclaw.utils，其次環境變數，最後硬編碼
if _has_jiuwenclaw:
    AGENT_ROOT = get_agent_root_dir()
else:
    env_workspace = os.getenv("JIUWENCLAW_DATA_DIR")
    if env_workspace:
        AGENT_ROOT = Path(env_workspace) / "agent"
    else:
        AGENT_ROOT = Path(os.environ.get("JIUWENCLAW_AGENT_ROOT", str(Path.home() / ".jiuwenclaw" / "agent")))

# 配置環境檔案路徑
if _has_jiuwenclaw:
    CONFIG_ENV = get_env_file()
else:
    env_workspace = os.getenv("JIUWENCLAW_DATA_DIR")
    if env_workspace:
        CONFIG_ENV = Path(env_workspace) / "config" / ".env"
    else:
        CONFIG_ENV = Path.home() / ".jiuwenclaw" / "config" / ".env"

# 報告用「日曆日/當前年月」與專案 cron 預設時區一致（避免 naive datetime）
_REPORT_TZ = ZoneInfo("Asia/Shanghai")


def collect_git_stats(date: str = None) -> dict:
    """採集 Git 提交統計"""
    if date is None:
        date = datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")

    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log",
             f"--since={date} 00:00:00",
             f"--until={date} 23:59:59",
             "--format=%H|%s|%an|%ai",
             "--numstat"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30
        )

        commits = []
        total_insertions = 0
        total_deletions = 0

        if result.stdout:
            current_commit = None
            for line in result.stdout.strip().split("\n"):
                if "|" in line and len(line.split("|")) >= 4:
                    parts = line.split("|")
                    if current_commit:
                        commits.append(current_commit)
                    current_commit = {
                        "hash": parts[0][:8],
                        "message": parts[1],
                        "author": parts[2],
                        "insertions": 0,
                        "deletions": 0
                    }
                elif current_commit and "\t" in line:
                    stat_parts = line.split("\t")
                    if len(stat_parts) >= 2:
                        try:
                            ins = int(stat_parts[0]) if stat_parts[0] != "-" else 0
                            dels = int(stat_parts[1]) if stat_parts[1] != "-" else 0
                            current_commit["insertions"] += ins
                            current_commit["deletions"] += dels
                            total_insertions += ins
                            total_deletions += dels
                        except ValueError:
                            pass

            if current_commit:
                commits.append(current_commit)

        return {
            "total_commits": len(commits),
            "total_insertions": total_insertions,
            "total_deletions": total_deletions,
            "commits": commits
        }
    except Exception as e:
        return {"error": str(e)}


def collect_email_stats(date: str = None) -> dict:
    """採集郵箱統計"""
    if not IMAP_AVAILABLE:
        return {"error": "IMAP module not available"}

    # 直接從 .env 檔案讀取配置
    env_file = CONFIG_ENV
    email_address = ""
    email_token = ""
    email_provider = "163"

    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key == "EMAIL_ADDRESS":
                        email_address = value.strip('"')
                    elif key == "EMAIL_TOKEN":
                        email_token = value.strip('"')
                    elif key == "EMAIL_PROVIDER":
                        email_provider = value.strip('"')

    # 也嘗試從環境變數獲取（作為備用）
    if not email_address:
        email_address = os.environ.get("EMAIL_ADDRESS", "")
    if not email_token:
        email_token = os.environ.get("EMAIL_TOKEN", "")
    if not email_provider:
        email_provider = os.environ.get("EMAIL_PROVIDER", "163")

    if not email_address or not email_token:
        return {"error": "Email credentials not configured"}

    # 網易郵箱 IMAP 伺服器
    IMAP_SERVERS = {
        "163": "imap.163.com",
        "126": "imap.126.com",
        "yeah": "imap.yeah.net",
    }

    server = IMAP_SERVERS.get(email_provider, "imap.163.com")

    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email_address, email_token)

        # 163郵箱需要在登入後傳送ID資訊
        try:
            args = '("name" "python-imap" "version" "1.0" "vendor" "python")'
            mail._simple_command("ID", args)
        except:
            pass

        # 使用 STATUS 命令獲取郵件統計（繞過 SELECT 的 Unsafe Login 限制）
        total_emails = 0
        unread = 0

        try:
            status, data = mail.status("INBOX", "(MESSAGES UNSEEN)")
            if status == "OK" and data:
                # 解析 STATUS 響應: b'"INBOX" (MESSAGES 39 UNSEEN 32)'
                import re
                response = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
                messages_match = re.search(r'MESSAGES\s+(\d+)', response)
                unseen_match = re.search(r'UNSEEN\s+(\d+)', response)
                if messages_match:
                    total_emails = int(messages_match.group(1))
                if unseen_match:
                    unread = int(unseen_match.group(1))
        except Exception as e:
            pass

        mail.logout()

        return {
            "received_today": total_emails,
            "unread": unread,
            "date": date if date else datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")
        }
    except Exception as e:
        # 返回預設值而不是錯誤
        return {
            "received_today": 0,
            "unread": 0,
            "date": date if date else datetime.now(_REPORT_TZ).strftime("%Y-%m-%d"),
            "error": str(e)[:50]  # 截斷錯誤資訊
        }


def collect_email_content(limit: int = 20, days: int = 30) -> list:
    """讀取郵箱中的郵件內容

    Args:
        limit: 最多讀取郵件數量
        days: 只讀取最近N天內的郵件

    Returns:
        郵件列表，每個元素包含 subject, from, date, body_preview
    """
    if not IMAP_AVAILABLE:
        return []

    # 直接從 .env 檔案讀取配置
    env_file = CONFIG_ENV
    email_address = ""
    email_token = ""
    email_provider = "163"

    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key == "EMAIL_ADDRESS":
                        email_address = value.strip('"')
                    elif key == "EMAIL_TOKEN":
                        email_token = value.strip('"')
                    elif key == "EMAIL_PROVIDER":
                        email_provider = value.strip('"')

    if not email_address or not email_token:
        return []

    IMAP_SERVERS = {
        "163": "imap.163.com",
        "126": "imap.126.com",
        "yeah": "imap.yeah.net",
    }

    server = IMAP_SERVERS.get(email_provider, "imap.163.com")
    emails = []

    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email_address, email_token)

        # 傳送ID命令（163郵箱必須）
        args = '("name" "python" "version" "1.0" "vendor" "python-imap")'
        mail._simple_command("ID", args)

        # 選擇收件箱
        typ, dat = mail.select("INBOX")
        if typ != "OK":
            mail.logout()
            return []

        # 搜尋最近N天的郵件
        since_date = (datetime.now(_REPORT_TZ) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, msg_ids = mail.search(None, f'(SINCE {since_date})')

        if typ != "OK" or not msg_ids[0]:
            mail.logout()
            return []

        ids = msg_ids[0].split()[-limit:]  # 獲取最新的N封

        for msg_id in reversed(ids):  # 從最新開始
            try:
                typ, msg_data = mail.fetch(msg_id, "(RFC822)")
                if typ != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # 解碼主題
                subject = msg["Subject"] or "(無主題)"
                if subject:
                    decoded = decode_header(subject)
                    subject = ""
                    for part, encoding in decoded:
                        if isinstance(part, bytes):
                            subject += part.decode(encoding or "utf-8", errors="ignore")
                        else:
                            subject += part

                # 解碼發件人
                from_addr = msg.get("From", "")
                if from_addr:
                    decoded = decode_header(from_addr)
                    from_addr = ""
                    for part, encoding in decoded:
                        if isinstance(part, bytes):
                            from_addr += part.decode(encoding or "utf-8", errors="ignore")
                        else:
                            from_addr += part

                # 日期
                date_str = msg.get("Date", "")

                # 提取正文
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type == "text/plain":
                            payload = part.get_payload(decode=True)
                            charset = part.get_content_charset() or "utf-8"
                            body = payload.decode(charset, errors="ignore")
                            break
                        elif content_type == "text/html" and not body:
                            payload = part.get_payload(decode=True)
                            charset = part.get_content_charset() or "utf-8"
                            html_body = payload.decode(charset, errors="ignore")
                            # 簡單清理HTML標籤
                            import re
                            body = re.sub(r'<[^>]+>', ' ', html_body)
                            body = re.sub(r'\s+', ' ', body).strip()
                else:
                    payload = msg.get_payload(decode=True)
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="ignore") if payload else ""

                emails.append({
                    "subject": subject[:100],
                    "from": from_addr[:80],
                    "date": date_str,
                    "body_preview": body[:500] if body else ""
                })

            except Exception:
                continue

        mail.logout()

    except Exception:
        pass

    return emails


def generate_daily_report(date: str = None, enable_ai: bool = True) -> str:
    """生成日報

    Args:
        date: 日期字串
        enable_ai: 是否啟用 AI 智慧分析（預設啟用）
    """
    if date is None:
        date = datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")

    # 採集 Git 資料
    git_stats = collect_git_stats(date)

    # 採集郵箱資料
    email_stats = collect_email_stats(date)

    # 讀取記憶檔案
    memory_file = AGENT_ROOT / "memory" / f"{date}.md"
    memory_content = ""
    work_items = []

    if memory_file.exists():
        memory_content = memory_file.read_text(encoding="utf-8")
        for line in memory_content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                item = stripped.lstrip("-* ").strip()
                if item and not item.startswith("<!--"):
                    work_items.append(item)

    # 查詢 todo 檔案
    todo_file = None
    session_dir = AGENT_ROOT / "sessions"
    if session_dir.exists():
        todo_files = list(session_dir.rglob("todo.md"))
        if todo_files:
            todo_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            todo_file = todo_files[0]

    # 解析 todo
    completed_tasks = []
    pending_tasks = []

    if todo_file and todo_file.exists():
        todo_content = todo_file.read_text(encoding="utf-8")
        for line in todo_content.split("\n"):
            stripped = line.strip()
            # Checkbox 格式
            match = re.match(r"-\s*\[([xX ])\]\s*(.+)", stripped)
            if match:
                checked = match.group(1).lower() == "x"
                task = match.group(2).strip()
                if checked:
                    completed_tasks.append(task)
                else:
                    pending_tasks.append(task)

    # 生成報告
    lines = [
        f"# 📋 工作日報 - {date}",
        "",
        "## 📊 今日概覽",
        "",
        "| 指標 | 數值 |",
        "|------|------|",
        f"| 程式碼提交 | {git_stats.get('total_commits', 0)} 次 |",
        f"| 程式碼變更 | +{git_stats.get('total_insertions', 0)}/-{git_stats.get('total_deletions', 0)} |",
        f"| 已完成任務 | {len(completed_tasks)} 項 |",
        f"| 進行中 | {len(pending_tasks)} 項 |",
    ]

    # 新增郵箱統計（如果採整合功）
    if "error" not in email_stats:
        lines.extend([
            f"| 郵件收件 | {email_stats.get('received_today', 0)} 封 |",
            f"| 未讀郵件 | {email_stats.get('unread', 0)} 封 |",
        ])

    lines.append("")

    # 已完成任務
    if completed_tasks:
        lines.extend(["## ✅ 已完成任務", ""])
        for task in completed_tasks[:10]:
            lines.append(f"- {task}")
        lines.append("")

    # 程式碼提交
    if git_stats.get("commits"):
        lines.extend([
            "## 💻 程式碼提交",
            "",
            "| 時間 | 提交資訊 | 變更 |",
            "|------|----------|------|",
        ])
        for commit in git_stats["commits"][:10]:
            lines.append(
                f"| {commit.get('hash', '-')} | {commit.get('message', '-')[:40]} | "
                f"+{commit.get('insertions', 0)}/-{commit.get('deletions', 0)} |"
            )
        lines.append("")

    # 工作記錄
    if work_items:
        lines.extend(["## 📝 今日工作記錄", ""])
        for item in work_items[:10]:
            lines.append(f"- {item}")
        lines.append("")

    # AI 智慧分析（如果啟用）
    if enable_ai:
        try:
            # 使用相對匯入
            from analyzers.ai_analyzer import AIAnalyzer

            ai_analyzer = AIAnalyzer()

            # 準備 AI 分析資料
            ai_data = {
                "date": date,
                "git": {
                    "total_commits": git_stats.get("total_commits", 0),
                    "total_insertions": git_stats.get("total_insertions", 0),
                    "total_deletions": git_stats.get("total_deletions", 0),
                    "commits": git_stats.get("commits", []),
                },
                "todo": {
                    "completed_count": len(completed_tasks),
                    "total_count": len(completed_tasks) + len(pending_tasks),
                    "pending_items": pending_tasks[:5],
                    "in_progress_items": [],
                },
                "email": {
                    "received_count": email_stats.get("received_today", 0),
                    "sent_count": 0,
                },
                "memory": {
                    "content": memory_content[:500] if memory_content else "",
                }
            }

            # 採集工作模式分析資料
            pattern_data = []
            for i in range(7):
                check_date = (datetime.now(_REPORT_TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
                day_stats = collect_git_stats(check_date)
                for commit in day_stats.get("commits", []):
                    pattern_data.append({
                        "date": check_date,
                        "time": commit.get("hash", "")[:8],  # 使用 hash 作為時間佔位
                        "message": commit.get("message", ""),
                    })

            # 執行 AI 分析
            import asyncio
            ai_result = asyncio.run(ai_analyzer.analyze_full(ai_data, pattern_data))

            # 在開頭新增 AI 摘要
            if ai_result.summary:
                lines.insert(2, "")  # 在標題後插入空行
                lines.insert(2, f"> {ai_result.summary}")
                lines.insert(2, "")
                lines.insert(2, "## 🤖 AI 智慧摘要")
                lines.insert(2, "")

            # 替換明日計劃為 AI 建議
            if ai_result.tomorrow_suggestions:
                ai_plan_index = None
                for i, line in enumerate(lines):
                    if "## 🔜 明日計劃" in line:
                        ai_plan_index = i
                        break

                if ai_plan_index:
                    lines[ai_plan_index] = "## 💡 工作建議與明日計劃"
                    # 在明日計劃後新增 AI 建議
                    insert_index = ai_plan_index + 1
                    for j, suggestion in enumerate(ai_result.tomorrow_suggestions):
                        lines.insert(insert_index + j, f"- {suggestion}")
                    lines.insert(insert_index + len(ai_result.tomorrow_suggestions), "")
                else:
                    # 如果沒有明日計劃章節，在報告末尾新增 AI 建議章節
                    lines.append("")
                    lines.append("## 💡 工作建議與明日計劃")
                    lines.append("")
                    lines.append("### 🔜 AI 明日計劃建議")
                    lines.append("")
                    for suggestion in ai_result.tomorrow_suggestions:
                        lines.append(f"- {suggestion}")
                    lines.append("")

            # 新增工作模式分析
            if ai_result.work_pattern and ai_result.work_pattern.get("description"):
                lines.append("")
                lines.append("## 📊 工作模式分析（近7天）")
                lines.append("")
                lines.append(ai_result.work_pattern.get("description", ""))
                lines.append("")

                peak_hours = ai_result.work_pattern.get("peak_hours", [])
                if peak_hours:
                    lines.append(f"- **效率高峰時段**: {', '.join([f'{h}:00' for h in peak_hours])}")

                avg_commits = ai_result.work_pattern.get("avg_commits_per_day", 0)
                if avg_commits > 0:
                    lines.append(f"- **平均每日提交**: {avg_commits:.1f} 次")

                lines.append("")

        except Exception as e:
            lines.append("")
            lines.append(f"<!-- AI 分析失敗: {e} -->")
            lines.append("")
    else:
        # 明日計劃
        lines.extend(["## 🔜 明日計劃", ""])
        if pending_tasks:
            for task in pending_tasks[:5]:
                lines.append(f"- {task}")
        else:
            lines.append("- 待補充")
        lines.append("")

    return "\n".join(lines)


def generate_monthly_report(year: int = None, month: int = None) -> str:
    """生成月報"""
    now = datetime.now(_REPORT_TZ)
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    import calendar
    _, days_in_month = calendar.monthrange(year, month)

    # 採集整月資料
    total_commits = 0
    total_insertions = 0
    total_deletions = 0
    active_days = 0
    total_emails_received = 0
    total_unread = 0
    email_collection_days = 0
    email_errors = []

    for day in range(1, days_in_month + 1):
        date = f"{year:04d}-{month:02d}-{day:02d}"

        # 採集 Git 資料
        stats = collect_git_stats(date)
        commits = stats.get("total_commits", 0)
        total_commits += commits
        total_insertions += stats.get("total_insertions", 0)
        total_deletions += stats.get("total_deletions", 0)
        if commits > 0:
            active_days += 1

        # 採集郵箱資料
        email_stats = collect_email_stats(date)
        if "error" not in email_stats:
            total_emails_received += email_stats.get("received_today", 0)
            email_collection_days += 1
        else:
            # 只記錄一次錯誤，避免重複
            if len(email_errors) == 0:
                email_errors.append(email_stats.get("error", "Unknown error"))

    # 獲取當前未讀郵件數
    current_email_stats = collect_email_stats()
    current_unread = current_email_stats.get("unread", 0) if "error" not in current_email_stats else 0

    # 生成報告
    lines = [
        f"# 📋 工作月報 - {year}年{month}月",
        "",
        "## 📊 本月概覽",
        "",
        "| 指標 | 數值 |",
        "|------|------|",
        f"| 活躍天數 | {active_days}/{days_in_month} 天 |",
        f"| 程式碼提交 | {total_commits} 次 |",
        f"| 程式碼變更 | +{total_insertions}/-{total_deletions} |",
    ]

    # 新增郵箱統計
    if email_collection_days > 0:
        lines.extend([
            f"| 郵件收件 | {total_emails_received} 封 |",
            f"| 當前未讀 | {current_unread} 封 |",
        ])
    elif email_errors:
        lines.append(f"| 郵箱狀態 | 採集失敗: {email_errors[0][:30]}... |")

    lines.append("")

    # 工作總結
    lines.extend([
        "## 📝 工作總結",
        "",
        f"本月共完成 {total_commits} 次程式碼提交，",
        f"淨增程式碼 {total_insertions - total_deletions} 行。",
    ])

    if email_collection_days > 0:
        lines.extend([
            "",
            f"郵箱方面，本月共收到 {total_emails_received} 封郵件，",
            f"當前有 {current_unread} 封未讀郵件。",
        ])

    # 新增近期郵件摘要
    lines.extend([
        "",
        "## 📧 近期郵件摘要",
        "",
    ])

    # 讀取最近30天的郵件
    recent_emails = collect_email_content(limit=15, days=30)
    if recent_emails:
        for em in recent_emails:
            lines.append(f"### {em['subject'][:50]}")
            lines.append(f"**發件人**: {em['from']}")
            lines.append(f"**時間**: {em['date']}")
            if em['body_preview']:
                lines.append(f"**內容預覽**: {em['body_preview'][:200]}...")
            lines.append("")
    else:
        lines.append("暫無郵件資料")
        lines.append("")

    lines.extend([
        "",
        "## 🔜 下月計劃",
        "",
        "- 繼續完善專案功能",
        "",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="日報/週報/月報生成器")
    parser.add_argument(
        "type",
        choices=["daily", "weekly", "monthly"],
        help="報告型別: daily(日報), weekly(週報), monthly(月報)"
    )
    parser.add_argument("--date", "-d", help="日期 (YYYY-MM-DD)")
    parser.add_argument("--year", "-y", type=int, help="年份")
    parser.add_argument("--month", "-m", type=int, help="月份")
    parser.add_argument("--save", "-s", action="store_true", default=True, help="儲存到檔案(預設開啟)")
    parser.add_argument("--no-save", action="store_true", help="不儲存檔案，直接輸出")
    parser.add_argument("--output-file", "-o", help="輸出檔案路徑")
    parser.add_argument("--ai", action="store_true", help="啟用 AI 智慧分析")
    parser.add_argument("--no-ai", action="store_true", help="禁用 AI 智慧分析")

    args = parser.parse_args()

    try:
        if args.type == "daily":
            date = args.date or datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")
            # AI 分析預設啟用，除非顯式指定 --no-ai
            enable_ai = not args.no_ai
            content = generate_daily_report(date, enable_ai=enable_ai)
            date_str = date

            if enable_ai:
                print("INFO: AI 智慧分析已啟用", file=sys.stderr)

        elif args.type == "weekly":
            date = args.date or datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")
            # 週報暫時用日報代替
            content = generate_daily_report(date)
            date_str = date

        elif args.type == "monthly":
            now = datetime.now(_REPORT_TZ)
            year = args.year or now.year
            month = args.month or now.month
            content = generate_monthly_report(year, month)
            date_str = f"{year:04d}-{month:02d}"

        # 儲存檔案（預設行為）
        if not args.no_save:
            if args.output_file:
                filepath = Path(args.output_file)
            else:
                reports_dir = AGENT_ROOT / "reports"
                reports_dir.mkdir(parents=True, exist_ok=True)
                filepath = reports_dir / f"{args.type}-{date_str}.md"
            filepath.write_text(content, encoding="utf-8")
            # 只輸出檔案路徑，方便 Agent 讀取
            print(f"REPORT_FILE:{filepath}")
        else:
            # 直接輸出內容
            print(content)

    except Exception as e:
        print(f"ERROR:{e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
