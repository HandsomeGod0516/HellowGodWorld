"""
openJiuwen-DeepSearch 主指令碼

使用 uv 執行：
    uv run scripts/main.py --query "研究題目"

依賴：
    - openjiuwen-deepsearch==0.1.1
    - python-dotenv
"""
import argparse
import asyncio
import datetime
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
import shutil

from dotenv import load_dotenv
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.utils.debug_utils.result_exporter import ResultExporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

from convert_docx import convert_md_to_docx
from convert_html import convert_md_to_html

# 獲取技能根目錄，優先使用 SKILL_ROOT 環境變數，否則自動檢測
SKILL_ROOT = Path(os.getenv("SKILL_ROOT", Path(__file__).parent.parent))

# 載入 .env 檔案
env_path = SKILL_ROOT / ".env"
load_dotenv(env_path)

os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_VERIFY"] = "false"

# 初始化日誌管理器
log_dir = SKILL_ROOT / "output" / "logs"
LogManager.init(
    log_dir=str(log_dir),
    max_bytes=100 * 1024 * 1024,
    backup_count=20,
    level="DEBUG",
    is_sensitive=False
)

# 初始化結果匯出器
results_dir = SKILL_ROOT / "output" / "results"
ResultExporter.init(
    results_dir=str(results_dir)
)

logger = logging.getLogger(__name__)


async def run_jiuwen_workflow(query: str, agent_config: dict):
    """
    執行 openJiuwen-DeepSearch 工作流

    Args:
        query: 使用者查詢字串
        agent_config: Agent 配置字典

    Returns:
        最終研究報告內容
    """
    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(agent_config)

    full_report = ""

    async for chunk in agent.run(
            message=query,
            conversation_id=str(uuid.uuid4()),
            report_template="",
            interrupt_feedback="",
            agent_config=agent_config
    ):
        logger.debug("[Stream message from node: %s]", chunk)
        chunk_content = json.loads(chunk)
        report_result = parse_endnode_content(chunk_content)
        if report_result:
            logger.debug("[Final Report is: %s]", report_result)
            if not full_report:
                full_report = report_result.get("response_content", "")

    output_md = f"{query}.md"
    output_html = f"{query}.html"
    output_docx = f"{query}.docx"

    workspace = Path("..") / ".." / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    workspace_md_path = workspace / output_md
    workspace_html_path = workspace / output_html
    workspace_docx_path = workspace / output_docx

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_md_path = output_dir / output_md
    output_html_path = output_dir / output_html
    output_docx_path = output_dir / output_docx

    try:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(full_report)
        convert_md_to_html(output_md, output_html)
        convert_md_to_docx(output_md, output_docx)
        
        shutil.copy(output_md, workspace_md_path)
        shutil.copy(output_html, workspace_html_path)
        shutil.copy(output_docx, workspace_docx_path)

        shutil.copy(output_md, output_md_path)
        shutil.copy(output_html, output_html_path)
        shutil.copy(output_docx, output_docx_path)
    except OSError as e:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(full_report)

    return full_report


def load_agent_config() -> dict:
    """
    從環境變數載入 Agent 配置

    Returns:
        Agent 配置字典

    Raises:
        ValueError: 缺少必需的環境變數
    """
    # 從環境變數讀取配置
    required_env_vars = {
        "LLM_MODEL_NAME": "LLM 模型名稱",
        "LLM_MODEL_TYPE": "LLM 模型型別",
        "LLM_BASE_URL": "LLM API 地址",
        "LLM_API_KEY": "LLM API Key",
        "WEB_SEARCH_ENGINE_NAME": "搜尋引擎名稱",
        "WEB_SEARCH_API_KEY": "搜尋引擎 API Key",
        "WEB_SEARCH_URL": "搜尋引擎 API 地址",
    }

    # 檢查必需的環境變數
    missing_vars = [
        var_name for var_name, desc in required_env_vars.items()
        if not os.getenv(var_name)
    ]

    if missing_vars:
        raise ValueError(
            f"缺少必需的環境變數: {', '.join(missing_vars)}\n"
            f"請在 .env 檔案中配置這些變數。"
        )

    # 載入配置
    config = Config().agent_config.model_dump()

    # LLM 配置
    config["llm_config"]["general"] = {
        "model_name": os.getenv("LLM_MODEL_NAME"),
        "model_type": os.getenv("LLM_MODEL_TYPE"),
        "base_url": os.getenv("LLM_BASE_URL"),
        "api_key": bytearray(os.getenv("LLM_API_KEY", ""), encoding="utf-8"),
    }

    # 搜尋引擎配置
    config["web_search_engine_config"] = {
        "search_engine_name": os.getenv("WEB_SEARCH_ENGINE_NAME"),
        "search_api_key": bytearray(os.getenv("WEB_SEARCH_API_KEY", ""), encoding="utf-8"),
        "search_url": os.getenv("WEB_SEARCH_URL"),
        "max_web_search_results": int(os.getenv("MAX_WEB_SEARCH_RESULTS", "5")),
    }

    # 工作流配置
    config["workflow_human_in_the_loop"] = False
    config["search_mode"] = "research"
    config["outliner_max_section_num"] = 5

    # 執行方式
    execution_method = os.getenv("EXECUTION_METHOD", "parallel")
    if execution_method == ExecutionMethod.DEPENDENCY_DRIVING.value:
        config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
    else:
        config["execution_method"] = ExecutionMethod.PARALLEL.value

    return config


def execute_deep_search(query: str) -> str | None:
    """
    執行深度研究（供 Agent 呼叫）

    Args:
        query: 研究題目

    Returns:
        研究報告內容，失敗返回 None
    """
    try:
        # 載入 Agent 配置
        agent_config = load_agent_config()

        # 執行工作流
        logger.info("開始執行深度研究: %s", query)
        result = asyncio.run(run_jiuwen_workflow(query, agent_config))

        if result:
            logger.info("研究報告生成完成")
            return result
        else:
            logger.warning("未生成研究報告")
            return None

    except ValueError as e:
        logger.error("配置錯誤: %s", e)
        return None
    except Exception as e:
        logger.exception("執行失敗: %s", e)
        return None


def run_background():
    """在後臺執行當前指令碼"""
    # 檢查是否已經在後臺程序中（防止無限迴圈）
    if os.getenv("JIUWEN_BACKGROUND_MODE") == "1":
        return

    script_path = Path(__file__).resolve()
    python_executable = sys.executable

    if sys.platform == "win32":
        pyw = Path(sys.executable).with_name("pythonw.exe")
        if pyw.exists():
            python_executable = str(pyw)

    cwd = Path.cwd()  # 當前工作目錄

    # 獲取當前命令列引數（移除 --background 標誌）
    cmd_args = [arg for arg in sys.argv[1:] if arg != "--background"]

    # 跨平臺後臺執行
    env = os.environ.copy()
    env["JIUWEN_BACKGROUND_MODE"] = "1"  # 標記已進入後臺模式

    # 確保 SKILL_ROOT 環境變數傳遞給子程序
    if "SKILL_ROOT" not in env:
        env["SKILL_ROOT"] = str(SKILL_ROOT)

    if sys.platform == "win32":
        detached_process = 0x00000008
        create_no_window = 0x08000000

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        proc = subprocess.Popen(
            [python_executable, str(script_path)] + cmd_args,
            creationflags=detached_process | subprocess.CREATE_NO_WINDOW,
            cwd=str(cwd),
            env=env,
            startupinfo=startupinfo
        )
    else:
        # Linux/macOS: 使用 start_new_session 建立新會話
        proc = subprocess.Popen(
            [python_executable, str(script_path)] + cmd_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(cwd),
            env=env
        )

    with open(f"PID.info", "w", encoding="utf-8") as f:
        f.write(f"啟動成功，子程序 PID={proc.pid}")
    query_text = ' '.join(cmd_args[cmd_args.index('--query') + 1:]) if '--query' in cmd_args else 'default'
    logger.info("任務已在後臺啟動，查詢: %s", query_text)
    sys.exit(0)


def main():
    """主函式（命令列入口）"""
    parser = argparse.ArgumentParser(
        description="openJiuwen-DeepSearch - 知識增強型深度檢索與研究引擎"
    )
    parser.add_argument(
        "--mode",
        default="query",
        choices=["query"],
        help="執行模式（當前僅支援 query）"
    )
    parser.add_argument(
        "--query",
        nargs="*",
        default=["AI手機研究報告"],
        help="研究題目（支援空格）"
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="在後臺執行（預設行為）"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="在前臺執行"
    )

    args = parser.parse_args()

    # 預設後臺執行模式，除非指定 --foreground
    if not args.foreground:
        run_background()

    query = " ".join(args.query)

    result = execute_deep_search(query)

    if result:
        logger.info("研究報告生成完成")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()