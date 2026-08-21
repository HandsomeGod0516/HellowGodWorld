#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _resolve_tool_timeout_s(default_s: float = 180.0) -> float:
    raw = (
        os.getenv("PLAYWRIGHT_TOOL_TIMEOUT_S")
        or os.getenv("PLAYWRIGHT_MCP_TIMEOUT_S")
        or os.getenv("BROWSER_TIMEOUT_S")
        or str(default_s)
    )
    try:
        parsed = float(raw)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default_s


def _format_tool_names(tool_call: Any) -> str:
    if isinstance(tool_call, list):
        names = [getattr(item, "name", "") for item in tool_call]
        names = [name for name in names if name]
        return ", ".join(names) if names else "<unknown>"
    name = getattr(tool_call, "name", "")
    return name or "<unknown>"


_XIAOHONGSHU_PUBLISH_GUIDANCE = """平臺特定行為示例：小紅書網頁版純文字帖子釋出。
僅當任務明確是在小紅書釋出帖子時才應用本示例。

【全域性原則】
- 只在小紅書創作或釋出頁面內操作，不進入首頁推薦流、搜尋、訊息、個人主頁或其他無關入口。
- 每一步先確認當前頁面狀態，再執行動作。
- 同一個動作最多重試 1 次；仍失敗則立即停止，並彙報當前頁面與失敗原因。
- 不要連續點選同一個按鈕。
- 只能選擇一條路徑執行；除非當前路徑入口明確不存在，否則不要中途切換路徑。
- 若出現登入、掃碼、驗證碼、風控、人機驗證，立即停止並請求人工接管。
- 若頁面文案與預期明顯不符，停止而不是猜測。

【內容一致性要求】
- 必須釋出使用者任務中已經提供、或上游流程已經明確生成好的最終文字內容。
- 嚴禁擅自改寫為“測試文字”“示例文字”“佔位文字”“體驗文案”“預設文案”或任何臨時內容。
- 嚴禁為了省事只輸入部分內容、摘要內容、前幾句內容，除非任務明確要求縮寫或摘要。
- 如果任務裡同時給出了標題和正文，必須分別寫入對應欄位，不要混淆。
- 如果任務裡沒有提供可釋出的最終文字內容，就停止並明確說明“缺少可釋出正文”，不要自行編造內容。
- 在輸入完成後，應讀取或檢查輸入框中的內容，確認其與目標文字核心內容一致，而不是測試文案或佔位文案。

【文字格式要求】
- 輸入正文時儘量保留原始段落結構、換行、列表、分段和語氣，不要壓成一整段。
- 若原文包含空行、段落分隔、專案符號或編號，輸入時應儘可能保留，以保證釋出後的可讀性和美觀度。
- 不要在輸入過程中頻繁刪改、反覆覆蓋或多次重寫同一段內容。
- 若頁面輸入框會吞掉換行，應在輸入後檢查實際顯示結果；如換行丟失，只允許進行一次針對性的格式修正。
- 標題應保持簡潔，不要把整段正文誤填到標題裡。
- 正文應完整，不要把標題重複貼上多次，也不要在正文里加入無關說明，如“以下是正文”“測試釋出”“幫你生成如下內容”等。

【路徑選擇規則】
- 如果能看到“上傳圖文”或“文字配圖”，優先走路徑 A。
- 如果路徑 A 不可見，但能看到“寫長文”或“新的創作”，走路徑 B。
- 如果兩條路徑入口都看不到，立即停止並彙報“未發現可用釋出入口”。

【路徑 A：上傳圖文 -> 文字配圖】
- 只點選“上傳圖文”一次，並等待頁面切換。
- 然後只選擇“文字配圖”，不要誤點普通圖片上傳、影片上傳、模板或靈感內容。
- 在目標輸入區一次性輸入用於生成圖片的完整文字，並確認文字確實已寫入。
- 只點選與“生成圖片”語義一致的主按鈕一次，等待生成結果；若生成失敗，最多重試 1 次。
- 一次性填寫標題，並確認標題非空。
- 一次性填寫正文或內容，並確認正文非空。
- 釋出前必須確認：至少保留 1 張生成圖片、標題非空、正文非空、當前仍在釋出編輯頁。
- 只點選一次“釋出”，然後等待頁面變化，不要再次點選。
- 若頁面離開當前編輯頁、進入作品頁/內容管理頁/列表頁，或原編輯態消失，即判定任務完成。
- 若出現明確報錯、稽核提示、網路異常、風控，提取報錯並停止。


【路徑 B：寫長文 -> 新的創作 -> 一鍵排版】
- 只點選“寫長文”一次並等待變化；如無變化，最多重試 1 次。
- 然後只點選“新的創作”，不要進入歷史草稿、模板或示例文章。
- 在主編輯區一次性貼上完整正文，並確認正文確實已寫入，再進行後續操作。
- 僅在正文存在時點選“一鍵排版”。
- 只選擇預設風格或任務指定風格，不來回嘗試多個風格。
- 只點選一次“下一步”，如未跳轉最多重試 1 次，並等待進入釋出資訊頁。
- 填寫標題並確認非空；只有當最終頁明確存在且為空的內容或摘要欄位時，才填寫該欄位。
- 釋出前必須確認：長文正文存在、標題非空、排版已完成、當前頁面存在“釋出”按鈕。
- 只點選一次“釋出”，然後等待頁面變化，不要再次點選。
- 若頁面離開當前編輯頁、進入作品頁/內容管理頁/列表頁，或原編輯態消失，即判定任務完成。
- 若出現明確報錯、稽核提示、網路異常、風控，提取報錯並停止。

【高價值關鍵詞】
上傳圖文、文字配圖、輸入文字生成圖片、寫長文、新的創作、一鍵排版、下一步、標題、內容、正文、釋出。
若這些關鍵詞不存在，不要猜測相似按鈕。

【釋出結果判定規則】
- 小紅書網頁版在點選“釋出”後，可能不會出現明顯的“釋出成功”彈窗或強提示。
- 因此，不能把“沒有成功彈窗”直接判斷為釋出失敗。
- 點選“釋出”後，只要出現以下任一訊號，即可判定為釋出成功：
  1. 頁面跳轉離開當前編輯頁
  2. 當前編輯態消失，無法繼續看到原來的標題/正文編輯框
  3. “釋出”按鈕消失、變灰後不再恢復，且頁面進入新的內容頁、作品頁、管理頁或列表頁
  4. 頁面出現與“作品”“筆記”“內容管理”“創作中心”“釋出管理”“我的內容”相關的結果頁
  5. 新發布內容出現在作品列表、內容列表或筆記列表中
- 如果點選“釋出”後頁面進入載入、跳轉、重新整理或編輯頁退出，也應優先視為成功後的正常流轉，而不是立即判定失敗。
- 只有在頁面明確出現報錯、網路異常、稽核攔截、風控攔截、許可權不足，或仍然停留在原編輯頁且“釋出”按鈕恢復可點選時，才判定為未成功。
- 若點選“釋出”後未看到明顯成功彈窗，不要重複點選“釋出”；應先觀察頁面是否已離開編輯態或進入作品/管理相關頁面。

【失敗時的固定輸出格式】
- 當前路徑：
- 當前步驟：
- 當前頁面可見關鍵詞：
- 已執行動作：
- 失敗原因判斷：
- 需要人工處理事項："""



def _is_xiaohongshu_publish_task(task: str) -> bool:
    text = (task or "").strip().lower()
    if not text:
        return False
    platform_markers = ("小紅書", "xiaohongshu", "xhs")
    publish_markers = ("釋出", "發帖", "帖子", "筆記", "創作")

    return any(marker in text for marker in platform_markers) and any(
        marker in text for marker in publish_markers
    )


def augment_browser_task_prompt(task: str) -> str:
    base = (task or "").strip()
    if not _is_xiaohongshu_publish_task(base):
        return base
    return (
        f"{base}\n\n{_XIAOHONGSHU_PUBLISH_GUIDANCE}\n\n"
        "Execution requirement: if this Xiaohongshu publish task reaches a "
        "valid final 釋出 button and there is no explicit blocking "
        "verification prompt, you must click 釋出 directly instead of asking "
        "the user to do it manually.\n"
        "Content requirement: you must publish the exact user-provided or "
        "already-generated final text content, preserve intended paragraph "
        "breaks as much as the editor allows, and never replace it with test "
        "text, placeholder text, or abbreviated sample text."
    )


def _build_main_agent_system_prompt(default_timeout_s: float) -> str:
    timeout_text = f"{int(default_timeout_s)}" if default_timeout_s.is_integer() else f"{default_timeout_s:.1f}"
    return (
        "You are the main orchestration agent.\n"
        "For browser tasks, prefer browser_run_task.\n"
        "Default to one comprehensive browser_run_task call per user request.\n"
        "Do not split work into many small browser_run_task calls unless a prior browser result shows "
        "a concrete blocking error that requires a narrower retry.\n"
        "Reuse the same session_id across retries to preserve browser continuity.\n"
        f"Use a long browser timeout. Do not pass timeout_s below {timeout_text}s. "
        "Prefer omitting timeout_s so the default long timeout is used.\n"
        "When a request is not straightforward and needs custom logic, call browser_custom_action first.\n"
        "If action names or params are unclear, call browser_list_custom_actions first and "
        "then call browser_custom_action with the matching action and params.\n"
        "Do not simulate browser actions yourself.\n"
        "For explicit user-authorized publishing, posting, sending, or "
        "submitting tasks, you are expected to complete the final "
        "Publish/Post/Send/Submit click yourself once the required fields are "
        "valid.\n"
        "Do not stop for generic account-safety concerns. Only stop when the "
        "website explicitly shows a blocking login, captcha, risk-control, "
        "security verification, permission gate, or other manual-review "
        "requirement.\n"
        "If the final publish or submit button is visible, enabled, and all "
        "required prechecks pass, click it exactly once and wait for the "
        "result.\n"
        "Pass through the full user goal clearly as browser task text.\n"
        "Keep user-facing answer concise and factual.\n"
        "If a browser tool returns an error, report it explicitly."
    )


def ensure_execute_signature_compat(agent: ReActAgent) -> None:
    """Adapt execute signature and add a timeout watchdog around tool execution."""
    execute_fn = getattr(agent.ability_manager, "execute", None)
    if execute_fn is None:
        return
    if getattr(execute_fn, "_playwright_timeout_wrapped", False):
        return

    try:
        params = inspect.signature(execute_fn).parameters
    except (TypeError, ValueError):
        return

    original_execute = execute_fn
    supports_tag = "tag" in params
    tool_timeout_s = _resolve_tool_timeout_s()

    async def execute_with_tag(tool_call, session, tag=None):
        tool_names = _format_tool_names(tool_call)
        try:
            with anyio.fail_after(tool_timeout_s):
                if supports_tag:
                    return await original_execute(tool_call, session, tag=tag)
                return await original_execute(tool_call, session)
        except TimeoutError as exc:
            logger.error(
                f"Tool execution timed out after {tool_timeout_s:.1f}s; tools={tool_names}"
            )
            raise RuntimeError(
                f"tool_execution_timeout: tools={tool_names}, timeout_s={tool_timeout_s:.1f}"
            ) from exc

    agent.ability_manager.execute = execute_with_tag
    setattr(agent.ability_manager.execute, "_playwright_timeout_wrapped", True)


def build_browser_worker_agent(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    mcp_cfg: McpServerConfig,
    max_steps: int,
    screenshot_subdir: str = "screenshots",
) -> ReActAgent:
    screenshot_subdir = (
        (screenshot_subdir or "screenshots").strip().replace("\\", "/").strip("/") or "screenshots"
    )
    card = AgentCard(
        id="agent.playwright.browser_worker",
        name="playwright_browser_worker",
        description="Browser worker that executes web tasks using Playwright MCP tools.",
        input_params={},
    )
    agent = ReActAgent(card=card).configure(
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(max_steps)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a browser worker agent.\n"
                        "Execute browser tasks step-by-step with Playwright MCP tools only.\n"
                        "Before interacting, ensure page or selector readiness.\n"
                        "Keep actions targeted and avoid unnecessary page snapshots.\n"
                        "If actions repeatedly fail, stop and report the exact failing action.\n"
                        "When the user has explicitly asked to publish, post, "
                        "send, or submit content, you should directly perform "
                        "the final click yourself once required fields are "
                        "valid.\n"
                        "Do not hand the last step back to the user because of "
                        "generic account-safety concerns. Stop only for explicit "
                        "blocking login, captcha, risk-control, security "
                        "verification, permission, or human-takeover "
                        "requirements shown by the site.\n"
                        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
                        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
                        "Use browser_run_code with: "
                        f"async (page) => {{ await page.screenshot({{ path: '{screenshot_subdir}/screenshot.png' }}); "
                        f"return '{screenshot_subdir}/screenshot.png'; }}\n"
                        "Final output MUST be a single JSON object with keys:\n"
                        "ok (boolean), final (string), page (object with url and title), "
                        "screenshot (string|null), error (string|null).\n"
                        "Do not output markdown."
                    ),
                }
            ]
        )
    )
    agent.ability_manager.add(mcp_cfg)
    ensure_execute_signature_compat(agent)
    return agent


def build_main_agent(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    browser_tool_card,
    custom_action_tool_card=None,
    list_actions_tool_card=None,
) -> ReActAgent:
    default_timeout_s = _resolve_tool_timeout_s()
    card = AgentCard(
        id="agent.playwright.main_runtime",
        name="playwright_main_runtime",
        description="Main runtime agent that delegates browser work to browser_run_task.",
        input_params={},
    )
    agent = ReActAgent(card=card).configure(
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(25)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": _build_main_agent_system_prompt(default_timeout_s),
                }
            ]
        )
    )
    agent.ability_manager.add(browser_tool_card)
    if custom_action_tool_card is not None:
        agent.ability_manager.add(custom_action_tool_card)
    if list_actions_tool_card is not None:
        agent.ability_manager.add(list_actions_tool_card)
    ensure_execute_signature_compat(agent)
    return agent
