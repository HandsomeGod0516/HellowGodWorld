"""AgentSociety2 custom agent backed by a JiuwenClaw AgentServer.

JiuwenClaw runs as a separate process. This adapter only speaks the
AgentServer WebSocket protocol shape that JiuwenClaw already accepts, so the
AgentSociety runtime does not need to import JiuwenClaw or openjiuwen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from agentsociety2.agent.base import AgentBase
from agentsociety2.agent.skills import SkillRegistry
from agentsociety2.agent.skills.runtime import AgentSkillRuntime


DEFAULT_JIUWENCLAW_WS_URL = "ws://127.0.0.1:18092"
DEFAULT_CHANNEL_ID = "agentsociety"
DEFAULT_MODE = "agent.plan"
DEFAULT_JIUWENCLAW_REQUEST_CONCURRENCY = 24
JIUWENCLAW_REQUEST_CONCURRENCY_ENV = "AGENTSOCIETY_JIUWENCLAW_REQUEST_CONCURRENCY"
logger = logging.getLogger(__name__)
DEFAULT_COMMON_SKILLS = [
    "routine.daily",
    "social.reply",
    "memory.record",
    "map.navigate",
    "safety.respond",
]
SKILL_CHINESE_LABELS = {
    "care.basic": "基礎關懷",
    "chronic.followup": "健康隨訪",
    "class.learn": "課堂學習",
    "class.organize": "課堂組織",
    "community.coordinate": "社群協調",
    "community.observe": "社群觀察",
    "computer.basic": "基礎電腦處理",
    "computer.repair": "電腦維修",
    "conflict.mediate": "矛盾調解",
    "cooking.lightmeal": "簡餐準備",
    "crowd.guide": "人群引導",
    "emotion.calm": "情緒安撫",
    "first_aid.basic": "基礎急救",
    "garden.basic": "庭院照料",
    "gossip.filter": "訊息甄別",
    "health.educate": "健康說明",
    "history.localtelling": "本地故事講述",
    "info.research": "資訊查證",
    "ingredient.advise": "食材建議",
    "inventory.count": "庫存清點",
    "ledger.basic": "賬目記錄",
    "library.curate": "圖書整理",
    "listen.relay": "傾聽轉達",
    "map.navigate": "地圖導航",
    "memory.record": "記憶記錄",
    "messaging.group": "群組通知",
    "neighbor.greet": "鄰里問候",
    "neighbor.support": "鄰里支援",
    "notice.write": "公告撰寫",
    "patrol.plan": "巡查規劃",
    "peer.communicate": "同伴溝通",
    "phone.photolog": "手機記錄",
    "price.negotiate": "價格協商",
    "privacy.protect": "隱私保護",
    "radio.comms": "無線電溝通",
    "record.shortnote": "短筆記記錄",
    "remote.communicate": "遠端溝通",
    "repair.basic": "基礎維修",
    "roster.verify": "名單核對",
    "route.localmap": "本地路線判斷",
    "route.recall": "路線回憶",
    "routine.daily": "日常安排",
    "safety.respond": "安全響應",
    "script.automate": "指令碼自動化",
    "shop.run": "店鋪經營",
    "sketch.draw": "速寫記錄",
    "social.matchmake": "牽線介紹",
    "social.reply": "社交回復",
    "stall.run": "攤位經營",
    "story.localpast": "本地舊事講述",
    "tools.repair": "工具維修",
    "vegetable.source": "蔬菜採購",
    "writing.feedback": "寫作反饋",
    "writing.hand": "手寫記錄",
    "youth.communicate": "青少年溝通",
}
STATUS_LABELS = {
    "active": "活躍",
    "available": "可交流",
    "calm": "平靜",
    "caring": "照護中",
    "content": "滿足",
    "coordinating": "協調中",
    "eating": "用餐中",
    "focused": "專注",
    "moving": "移動中",
    "peaceful": "安穩",
    "ready": "就緒",
    "resting": "休息中",
    "socializing": "社交中",
    "starting_day": "開始一天",
    "studying": "學習中",
    "teaching": "授課中",
    "tired": "疲憊",
    "warm": "溫和",
    "winding_down": "放鬆中",
    "working": "工作中",
}
CHINESE_OUTPUT_POLICY = (
    "語言硬性規則：除 JSON 鍵名、action_type 列舉、location_id、interaction_id、"
    "skill_id、session_id、URL 等機器識別符號外，所有會被人看到的自然語言都必須使用"
    "簡體中文。public_summary、environment_instruction、action_proposal.content、"
    "action、status、emotion、reason、事件、通知、記憶內容和對話內容都不能出現英文句子或英文片語；"
    "智慧體姓名可以保留英文。"
)
SKILL_RESULT_SCHEMA_VERSION = "agent_skill_result.v1"
URGENT_INTERVENTION_KEYWORDS = (
    "火山",
    "爆發",
    "地震",
    "火災",
    "洪水",
    "海嘯",
    "爆炸",
    "撤離",
    "疏散",
    "緊急",
    "危險",
    "災",
    "evacuate",
    "emergency",
    "volcano",
    "earthquake",
    "fire",
    "flood",
)
PUBLIC_GROUP_SKILL_IDS = {
    "safety.respond",
    "notice.write",
    "radio.comms",
    "messaging.group",
}


def _workspace_root_from_file() -> str:
    """Infer the AgentSociety workspace root from custom/agents/this_file.py."""

    try:
        return str(Path(__file__).resolve().parents[2])
    except IndexError:
        return str(Path.cwd())


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation for profile/dump fields."""

    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return str(value)


def _contains_latin_text(value: Any) -> bool:
    return any("a" <= ch.lower() <= "z" for ch in str(value or ""))


def _contains_latin_text_outside_terms(value: Any, allowed_terms: list[str]) -> bool:
    text = str(value or "")
    terms: set[str] = set()
    for item in allowed_terms:
        term = str(item).strip()
        if not term:
            continue
        terms.add(term)
        terms.update(
            piece
            for piece in term.replace("-", " ").replace("_", " ").split()
            if _contains_latin_text(piece)
        )
    for term in sorted(terms, key=len, reverse=True):
        text = text.replace(term, "")
    return _contains_latin_text(text)


class JiuwenClawAgent(AgentBase):
    """AgentSociety2 AgentBase adapter for a running JiuwenClaw AgentServer."""

    _request_semaphore: ClassVar[asyncio.Semaphore | None] = None
    _request_semaphore_limit: ClassVar[int | None] = None
    _request_semaphore_loop: ClassVar[asyncio.AbstractEventLoop | None] = None

    @classmethod
    def _request_concurrency_limit(cls) -> int:
        raw_value = os.getenv(JIUWENCLAW_REQUEST_CONCURRENCY_ENV, "").strip()
        if not raw_value:
            return DEFAULT_JIUWENCLAW_REQUEST_CONCURRENCY
        try:
            value = int(raw_value)
        except ValueError:
            return DEFAULT_JIUWENCLAW_REQUEST_CONCURRENCY
        return max(1, value)

    @classmethod
    def _request_semaphore_for_current_loop(cls) -> tuple[asyncio.Semaphore, int]:
        limit = cls._request_concurrency_limit()
        loop = asyncio.get_running_loop()
        if (
            cls._request_semaphore is None
            or cls._request_semaphore_limit != limit
            or cls._request_semaphore_loop is not loop
        ):
            cls._request_semaphore = asyncio.Semaphore(limit)
            cls._request_semaphore_limit = limit
            cls._request_semaphore_loop = loop
        return cls._request_semaphore, limit

    def __init__(
        self,
        id: int,
        profile: Any,
        name: str | None = None,
        jiuwenclaw_ws_url: str = DEFAULT_JIUWENCLAW_WS_URL,
        session_id: str | None = None,
        mode: str = DEFAULT_MODE,
        trusted_dirs: list[str] | None = None,
        request_timeout: float = 600.0,
        enable_memory: bool = True,
        channel_id: str = DEFAULT_CHANNEL_ID,
        enable_daily_life: bool = True,
        daily_life_skill_path: str | None = None,
        enable_skill_runtime: bool = True,
        common_skill_ids: list[str] | None = None,
        skill_ids: list[str] | None = None,
        mounted_skill_ids: list[str] | None = None,
        skill_runtime_skill_names: list[str] | None = None,
        experiment_context: Any | None = None,
    ) -> None:
        super().__init__(id=id, profile=profile, name=name)
        self._jiuwenclaw_ws_url = jiuwenclaw_ws_url
        self._session_id = session_id or f"agentsociety_agent_{id}"
        self._mode = mode
        self._trusted_dirs = trusted_dirs or [_workspace_root_from_file()]
        self._request_timeout = float(request_timeout)
        self._enable_memory = bool(enable_memory)
        self._channel_id = channel_id or DEFAULT_CHANNEL_ID
        # Legacy constructor fields are accepted for old configs. Runtime
        # behavior is always driven by executable AgentSociety skills.
        _ = (
            enable_daily_life,
            daily_life_skill_path,
            enable_skill_runtime,
            skill_runtime_skill_names,
        )
        self._enable_skill_runtime = True
        self._experiment_context = experiment_context
        self._common_skill_ids = self._normalize_skill_ids(
            common_skill_ids or DEFAULT_COMMON_SKILLS
        )
        personal_skill_ids = self._normalize_skill_ids(skill_ids or [])
        if not personal_skill_ids and mounted_skill_ids:
            common_set = set(self._common_skill_ids)
            personal_skill_ids = [
                item
                for item in self._normalize_skill_ids(mounted_skill_ids)
                if item not in common_set
            ]
        self._skill_ids = personal_skill_ids
        self._skill_registry = SkillRegistry()
        self._skill_registry.scan_custom(_workspace_root_from_file())
        self._skill_runtime = AgentSkillRuntime(
            agent_id=id,
            registry=self._skill_registry,
        )
        self._ws: Any = None
        self._ws_lock = asyncio.Lock()
        self._last_response: str = ""
        self._last_environment_result: str = ""
        self._agent_work_dir: Path | None = None
        self._pending_interventions: list[str] = []
        self._recent_live_questions: list[dict[str, str]] = []
        self._last_selected_skills: set[str] = set()
        self._last_activated_skills: set[str] = set()
        self._last_skill_results: list[dict[str, Any]] = []
        self._last_action_proposal: dict[str, Any] = {}
        self._last_social_action_proposal: dict[str, Any] = {}
        self._last_skill_decision: dict[str, Any] = {}
        self._last_skill_result: dict[str, Any] = {}
        self._last_environment_effects: list[dict[str, Any]] = []

    @classmethod
    def mcp_description(cls) -> str:
        return """JiuwenClawAgent: AgentSociety2 custom agent backed by JiuwenClaw

Runs JiuwenClaw as an external AgentServer and communicates over WebSocket.

Profile fields are free-form. Useful fields:
- name: display name
- role: simulation role
- persona: behavior/personality notes

Initialization example:
```json
{
  "id": 0,
  "profile": {
    "name": "Jiuwen assistant",
    "role": "JiuwenClaw-driven simulation agent"
  },
  "jiuwenclaw_ws_url": "ws://127.0.0.1:18092",
  "session_id": "agentsociety_agent_0",
  "mode": "agent.plan",
  "trusted_dirs": ["<path-to-GOD>/agentsociety"],
  "enable_memory": true,
  "enable_skill_runtime": true,
  "common_skill_ids": ["routine.daily", "social.reply", "memory.record", "map.navigate", "safety.respond"],
  "skill_ids": ["community.coordinate", "tools.repair"]
}
```
"""

    @staticmethod
    def _normalize_skill_ids(values: list[str] | None) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            skill_id = str(value or "").strip()
            if skill_id and skill_id not in seen:
                seen.add(skill_id)
                result.append(skill_id)
        return result

    def _mounted_skill_ids(self) -> list[str]:
        mounted = self._normalize_skill_ids(self._common_skill_ids + self._skill_ids)
        return mounted or list(DEFAULT_COMMON_SKILLS)

    async def init(self, env: Any) -> None:
        await super().init(env)
        if getattr(env, "run_dir", None) is None:
            self._agent_work_dir = None
            return
        self._skill_registry.scan_custom(_workspace_root_from_file())
        self._agent_work_dir = self._skill_runtime.ensure_agent_work_dir(env)
        self._skill_runtime.ensure_standard_workspace_dirs()
        self._write_json(
            "agent_config.json",
            {
                "agent_type": self.__class__.__name__,
                "id": self.id,
                "name": self.name,
                "profile": _json_safe(self.get_profile()),
                "jiuwenclaw_ws_url": self._jiuwenclaw_ws_url,
                "session_id": self._session_id,
                "mode": self._mode,
                "trusted_dirs": list(self._trusted_dirs),
                "request_timeout": self._request_timeout,
                "enable_memory": self._enable_memory,
                "enable_skill_runtime": self._enable_skill_runtime,
                "common_skill_ids": list(self._common_skill_ids),
                "skill_ids": list(self._skill_ids),
                "mounted_skill_ids": self._mounted_skill_ids(),
                "experiment_context": _json_safe(self._experiment_context),
            },
        )

    def _experiment_context_text(self) -> str:
        if not self._experiment_context:
            return ""
        return (
            "\n實驗上下文：\n"
            f"{json.dumps(_json_safe(self._experiment_context), ensure_ascii=False)}\n"
            f"{CHINESE_OUTPUT_POLICY}\n"
        )

    async def ask(self, message: str, readonly: bool = True) -> str:
        prompt = self._build_ask_prompt(message=message, readonly=readonly)
        answer = await self._send_jiuwenclaw_request(prompt)
        self._last_response = answer
        self._recent_live_questions.append(
            {
                "question": message,
                "answer": answer,
                "readonly": str(readonly),
            }
        )
        self._recent_live_questions = self._recent_live_questions[-10:]
        return answer

    async def answer_external_question(
        self,
        prompt: str,
        *,
        t: datetime,
        response_type: str = "text",
        choices: list[str] | None = None,
    ) -> str:
        """Answer live targeted Ask through the JiuwenClaw agent session."""

        requirement = self._external_question_output_requirement(
            response_type,
            choices,
        )
        message = (
            "這是給 AgentSociety 模擬智慧體的外部提問。\n"
            f"當前模擬時間：{t.isoformat()}\n"
            f"輸出要求：{requirement}\n"
            f"{CHINESE_OUTPUT_POLICY}\n"
            "請以該智慧體第一人稱回答。以當前模擬狀態、近期實時提問、角色檔案和九問會話上下文為準。"
            "只讀提問不得改變 AgentSociety 環境。\n\n"
            f"問題：\n{prompt}"
        )
        return await self.ask(message, readonly=True)

    async def step(self, tick: int, t: datetime) -> str:
        observation = await self._observe_environment()
        pending_interventions = list(self._pending_interventions)
        self._pending_interventions = []
        broadcast_result = await self._broadcast_urgent_interventions(
            pending_interventions
        )

        result = await self._run_skill_runtime(
            tick=tick,
            t=t,
            observation=observation,
            pending_interventions=pending_interventions,
            broadcast_result=broadcast_result,
        )
        status = "completed" if result.get("ok", True) else "error"
        self._last_environment_result = json.dumps(
            result.get("environment_effects") or [],
            ensure_ascii=False,
        )
        self._persist_runtime_state(tick=tick, t=t, status=status)
        public_summary = str(result.get("public_summary") or "技能步驟已完成。")
        environment_summary = result.get("environment_effects") or []
        if environment_summary:
            return (
                f"{public_summary}\n\n"
                f"環境結果：{json.dumps(environment_summary, ensure_ascii=False)}"
            )
        return public_summary

    def queue_intervention(self, instruction: str) -> str:
        self._pending_interventions.append(str(instruction))
        return (
            "已實時投遞到該 agent 的 pending interventions；"
            "下一次 step prompt 會包含這條幹預並要求優先執行。"
            "若識別為緊急公共事件，會先自動廣播到小鎮群組。"
        )

    async def _observe_environment(self) -> Any:
        social_env = self._find_social_environment()
        if social_env is not None and callable(getattr(social_env, "observe_agent", None)):
            try:
                return await social_env.observe_agent(self.id)
            except Exception:
                pass
        try:
            _, observation = await self.ask_env(
                {"variables": {}},
                "請觀察該智慧體當前的環境狀態。",
                readonly=True,
            )
            return observation
        except Exception as exc:
            return f"無法觀察環境：{exc}"

    async def _run_skill_runtime(
        self,
        *,
        tick: int,
        t: datetime,
        observation: Any,
        pending_interventions: list[str] | None = None,
        broadcast_result: str = "",
    ) -> dict[str, Any]:
        if self._agent_work_dir is None:
            self._last_selected_skills = set()
            self._last_activated_skills = set()
            self._last_skill_results = []
            self._last_action_proposal = {}
            self._last_social_action_proposal = {}
            self._last_skill_decision = {}
            self._last_skill_result = {}
            self._last_environment_effects = []
            return {}

        pending_interventions = pending_interventions or []
        runtime_args = {
            "agent_id": self.id,
            "agent_name": self.name,
            "profile": _json_safe(self.get_profile()),
            "tick": tick,
            "time": t.isoformat(),
            "observation": _json_safe(observation),
            "agent_work_dir": str(self._agent_work_dir),
            "pending_interventions": list(pending_interventions),
            "broadcast_result": broadcast_result,
        }
        self._skill_runtime.workspace_write(
            "state/observation.json",
            json.dumps(_json_safe(observation), ensure_ascii=False, indent=2),
        )
        self._skill_runtime.workspace_write(
            "state/profile.json",
            json.dumps(_json_safe(self.get_profile()), ensure_ascii=False, indent=2),
        )
        self._skill_runtime.workspace_write(
            "state/current_time.json",
            json.dumps(
                {"tick": tick, "time": t.isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
        )
        self._skill_runtime.workspace_write(
            "state/mounted_skills.json",
            json.dumps(self._mounted_skill_ids(), ensure_ascii=False, indent=2),
        )

        requested_skill_ids = self._mounted_skill_ids()
        metadata = self._skill_runtime.skill_list(requested_skill_ids)
        discovered = {str(item["name"]) for item in metadata if item.get("name")}
        mounted = [skill_id for skill_id in requested_skill_ids if skill_id in discovered]
        self._last_selected_skills = set(mounted)
        self._last_activated_skills = set()
        self._last_skill_results = []
        self._last_action_proposal = {}
        self._last_social_action_proposal = {}
        self._last_skill_decision = {}
        self._last_skill_result = {}
        self._last_environment_effects = []

        decision = await self._select_next_skill(
            tick=tick,
            t=t,
            observation=observation,
            catalog=[item for item in metadata if str(item.get("name") or "") in mounted],
            mounted_skill_ids=mounted,
            pending_interventions=pending_interventions,
            broadcast_result=broadcast_result,
        )
        selected_skill_id = str(decision.get("selected_skill_id") or "").strip()
        if selected_skill_id not in mounted:
            decision = self._fallback_skill_decision(
                mounted_skill_ids=mounted,
                observation=observation,
                pending_interventions=pending_interventions,
                reason=f"選擇的技能無效或不可用：{selected_skill_id}",
            )
            selected_skill_id = str(decision.get("selected_skill_id") or "").strip()

        self._last_skill_decision = dict(decision)
        if not selected_skill_id:
            return {
                "ok": False,
                "public_summary": "該智慧體沒有掛載可執行技能。",
                "mounted_skill_ids": mounted,
                "last_skill_decision": dict(decision),
            }

        self._last_activated_skills = {selected_skill_id}
        self._skill_runtime.skill_activate(selected_skill_id)
        runtime_args["selected_skill_id"] = selected_skill_id
        runtime_args["skill_args"] = (
            decision.get("args") if isinstance(decision.get("args"), dict) else {}
        )
        runtime_args["skill_decision"] = dict(decision)
        try:
            raw_result = await self._skill_runtime.execute(
                selected_skill_id,
                runtime_args,
            )
        except Exception as exc:
            raw_result = {
                "ok": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "error_type": type(exc).__name__,
                "artifacts": [],
            }

        skill_result = self._parse_skill_result(raw_result, selected_skill_id)
        skill_result = self._localize_skill_result(skill_result, selected_skill_id)
        validation = self._validate_skill_result(
            selected_skill_id=selected_skill_id,
            skill_result=skill_result,
            observation=observation,
        )
        skill_result["validation"] = validation
        environment_effects = await self._apply_skill_result(skill_result, validation)
        self._last_skill_result = dict(skill_result)
        self._last_environment_effects = list(environment_effects)
        self._last_action_proposal = self._legacy_action_from_skill_result(skill_result)
        entry = {
            "tick": tick,
            "time": t.isoformat(),
            "tool": "execute_skill",
            "skill_name": selected_skill_id,
            "decision": dict(decision),
            "result": raw_result,
            "skill_result": skill_result,
            "environment_effects": environment_effects,
        }
        self._last_skill_results = [entry]
        self._skill_runtime.append_tool_log(entry)

        public_summary = str(
            decision.get("public_summary")
            or skill_result.get("summary")
            or f"{self.name} 執行了{self._skill_label(selected_skill_id)}。"
        )
        return {
            "ok": bool(raw_result.get("ok")) and not validation.get("errors"),
            "public_summary": public_summary,
            "mounted_skill_ids": mounted,
            "selected_skills": mounted,
            "activated_skills": sorted(self._last_activated_skills),
            "last_skill_decision": dict(decision),
            "last_skill_result": skill_result,
            "environment_effects": environment_effects,
            "skill_results": [entry],
        }

    async def _select_next_skill(
        self,
        *,
        tick: int,
        t: datetime,
        observation: Any,
        catalog: list[dict[str, Any]],
        mounted_skill_ids: list[str],
        pending_interventions: list[str],
        broadcast_result: str,
    ) -> dict[str, Any]:
        if not mounted_skill_ids:
            return self._fallback_skill_decision(
                mounted_skill_ids=[],
                observation=observation,
                pending_interventions=pending_interventions,
                reason="沒有可用的已掛載技能。",
            )
        prompt = self._build_skill_selection_prompt(
            tick=tick,
            t=t,
            observation=observation,
            catalog=catalog,
            pending_interventions=pending_interventions,
            broadcast_result=broadcast_result,
        )
        try:
            raw = await self._send_jiuwenclaw_request(prompt)
            self._last_response = raw
            self._append_thread_message("user", prompt, tick=tick, t=t)
            self._append_thread_message("assistant", raw, tick=tick, t=t)
            decision = self._parse_step_decision(raw)
            if isinstance(decision, dict) and decision.get("_parsed"):
                decision.pop("_parsed", None)
                return decision
            return self._fallback_skill_decision(
                mounted_skill_ids=mounted_skill_ids,
                observation=observation,
                pending_interventions=pending_interventions,
                reason="九問返回的技能決策不是 JSON。",
            )
        except Exception as exc:
            self._last_response = f"九問技能選擇失敗：{exc}"
            return self._fallback_skill_decision(
                mounted_skill_ids=mounted_skill_ids,
                observation=observation,
                pending_interventions=pending_interventions,
                reason=str(exc),
            )

    def _build_skill_selection_prompt(
        self,
        *,
        tick: int,
        t: datetime,
        observation: Any,
        catalog: list[dict[str, Any]],
        pending_interventions: list[str],
        broadcast_result: str,
    ) -> str:
        compact_catalog = [
            {
                "name": item.get("name"),
                "description": item.get("description"),
                "effects": item.get("effects", []),
                "args_schema": item.get("args_schema", {}),
                "trigger_examples": item.get("trigger_examples", []),
                "shared": item.get("shared", False),
            }
            for item in catalog
        ]
        return (
            "你正在為一個 AgentSociety 模擬智慧體選擇本步驟唯一要執行的技能。\n"
            f"智慧體編號：{self.id}\n"
            f"智慧體姓名：{self.name}\n"
            f"角色檔案：{json.dumps(_json_safe(self.get_profile()), ensure_ascii=False)}\n"
            f"{self._experiment_context_text()}"
            f"模擬時間：{t.isoformat()}\n"
            f"單步秒數：{tick}\n"
            f"環境觀察：\n{json.dumps(_json_safe(observation), ensure_ascii=False, indent=2)}\n"
            f"待處理實時干預：{json.dumps(pending_interventions, ensure_ascii=False)}\n"
            f"自動緊急處理結果：{broadcast_result}\n"
            f"已掛載可執行技能目錄：\n{json.dumps(compact_catalog, ensure_ascii=False, indent=2)}\n\n"
            f"{CHINESE_OUTPUT_POLICY}\n"
            "請從已掛載目錄中選擇一個技能。只返回一個 JSON 物件：\n"
            "{\n"
            '  "selected_skill_id": "一個已掛載技能編號",\n'
            '  "args": {"optional": "符合 args_schema 的技能引數"},\n'
            '  "reason": "為什麼這個技能適合當前狀態",\n'
            '  "public_summary": "用中文簡短描述智慧體想做什麼"\n'
            "}\n"
            "不要返回 environment_instruction 或 action_proposal，選中的技能指令碼會產生效果。"
        )

    def _fallback_skill_decision(
        self,
        *,
        mounted_skill_ids: list[str],
        observation: Any,
        pending_interventions: list[str],
        reason: str,
    ) -> dict[str, Any]:
        observation_dict = observation if isinstance(observation, dict) else {}
        text = " ".join(
            [
                str(observation_dict.get("latest_event") or ""),
                str(observation_dict.get("last_message") or ""),
                " ".join(pending_interventions),
                json.dumps(observation_dict.get("recent_messages") or [], ensure_ascii=False),
            ]
        ).lower()
        def available(skill_id: str) -> bool:
            return skill_id in mounted_skill_ids

        if any(keyword.lower() in text for keyword in URGENT_INTERVENTION_KEYWORDS) and available("safety.respond"):
            selected = "safety.respond"
        elif observation_dict.get("recent_messages") and available("social.reply"):
            selected = "social.reply"
        elif available("routine.daily"):
            selected = "routine.daily"
        elif mounted_skill_ids:
            selected = mounted_skill_ids[0]
        else:
            selected = ""
        return {
            "selected_skill_id": selected,
            "args": {},
            "reason": f"後備技能選擇：{reason}",
            "public_summary": f"{self.name} 本步執行{selected or '空技能'}。",
            "fallback": True,
        }

    def _parse_skill_result(
        self,
        raw_result: dict[str, Any],
        selected_skill_id: str,
    ) -> dict[str, Any]:
        stdout = str(raw_result.get("stdout") or "").strip()
        parsed = self._extract_json_object(stdout) if stdout else None
        if isinstance(parsed, dict):
            result = parsed
        else:
            result = {
                "schema_version": SKILL_RESULT_SCHEMA_VERSION,
                "skill_id": selected_skill_id,
                "summary": f"{selected_skill_id} 沒有返回有效的 JSON。",
                "reason": str(raw_result.get("stderr") or raw_result.get("error_type") or "技能輸出無效"),
                "confidence": 0.0,
                "world_effect": None,
                "speech_effect": None,
                "memory_effects": [],
            }
        result.setdefault("schema_version", SKILL_RESULT_SCHEMA_VERSION)
        result.setdefault("skill_id", selected_skill_id)
        result.setdefault("memory_effects", [])
        return result

    @staticmethod
    def _skill_label(skill_id: str) -> str:
        return SKILL_CHINESE_LABELS.get(str(skill_id), "技能")

    def _allowed_visible_latin_terms(self) -> list[str]:
        terms = [self.name]
        profile = _json_safe(self.get_profile())
        if isinstance(profile, dict):
            if profile.get("name"):
                terms.append(str(profile["name"]))
            social_network = profile.get("social_network")
            if isinstance(social_network, dict):
                terms.extend(str(name) for name in social_network.keys())
        return terms

    def _contains_visible_english(self, value: Any) -> bool:
        return _contains_latin_text_outside_terms(
            value,
            self._allowed_visible_latin_terms(),
        )

    def _localize_skill_result(
        self,
        skill_result: dict[str, Any],
        selected_skill_id: str,
    ) -> dict[str, Any]:
        """Keep executable skill outputs from leaking English into visible replay text."""

        label = self._skill_label(selected_skill_id)
        result = dict(skill_result)
        if self._contains_visible_english(result.get("summary")):
            result["summary"] = f"執行{label}。"
        if self._contains_visible_english(result.get("reason")):
            result["reason"] = f"根據當前觀察選擇{label}。"

        world = result.get("world_effect")
        if isinstance(world, dict):
            world = dict(world)
            if self._contains_visible_english(world.get("action")):
                world["action"] = f"執行{label}"
            if self._contains_visible_english(world.get("status")):
                world["status"] = STATUS_LABELS.get(str(world.get("status")), "活躍")
            if self._contains_visible_english(world.get("emotion")):
                world["emotion"] = STATUS_LABELS.get(str(world.get("emotion")), "平靜")
            if self._contains_visible_english(world.get("reason")):
                world["reason"] = f"執行{label}"
            params = world.get("params")
            if isinstance(params, dict) and self._contains_visible_english(params.get("message")):
                params = dict(params)
                params["message"] = f"執行{label}"
                world["params"] = params
            result["world_effect"] = world

        speech = result.get("speech_effect")
        if isinstance(speech, dict):
            speech = dict(speech)
            if self._contains_visible_english(speech.get("content")):
                speech["content"] = "我會用中文同步當前處理。"
            result["speech_effect"] = speech

        memories = result.get("memory_effects")
        if isinstance(memories, list):
            localized_memories = []
            for memory in memories:
                if not isinstance(memory, dict):
                    localized_memories.append(memory)
                    continue
                localized = dict(memory)
                if self._contains_visible_english(localized.get("content")):
                    localized["content"] = f"記錄了技能事件：{label}。"
                localized_memories.append(localized)
            result["memory_effects"] = localized_memories
        return result

    def _validate_skill_result(
        self,
        *,
        selected_skill_id: str,
        skill_result: dict[str, Any],
        observation: Any,
    ) -> dict[str, Any]:
        errors: list[str] = []
        valid_effects = {"world_effect": False, "speech_effect": False, "memory_effects": True}
        info = self._skill_registry.get_skill_info(selected_skill_id, load_content=False)
        allowed = set(info.effects if info is not None else [])

        if skill_result.get("schema_version") != SKILL_RESULT_SCHEMA_VERSION:
            errors.append("invalid schema_version")
        if str(skill_result.get("skill_id") or "") != selected_skill_id:
            errors.append("skill_id does not match selected_skill_id")

        observation_dict = observation if isinstance(observation, dict) else {}
        known_locations = {
            str(item.get("id") or "")
            for item in observation_dict.get("known_locations", []) or []
            if isinstance(item, dict)
        }
        known_interactions = {
            str(item.get("id") or ""): item
            for item in observation_dict.get("known_interactions", []) or []
            if isinstance(item, dict)
        }
        current_location = str(observation_dict.get("location_id") or "")

        world = skill_result.get("world_effect")
        if isinstance(world, dict):
            effect_type = str(world.get("type") or "")
            if effect_type not in allowed:
                errors.append(f"world_effect type '{effect_type}' is not allowed for {selected_skill_id}")
            elif effect_type == "move":
                location_id = str(world.get("location_id") or world.get("location") or "")
                if not location_id or (known_locations and location_id not in known_locations):
                    errors.append(f"unknown move location_id: {location_id}")
                else:
                    valid_effects["world_effect"] = True
            elif effect_type == "interact":
                interaction_id = str(world.get("interaction_id") or "")
                interaction = known_interactions.get(interaction_id)
                allowed_locations = (
                    interaction.get("allowed_location_ids")
                    if isinstance(interaction, dict)
                    else None
                )
                if not interaction:
                    errors.append(f"unknown interaction_id: {interaction_id}")
                elif allowed_locations and current_location not in allowed_locations:
                    errors.append(f"interaction '{interaction_id}' is not available at {current_location}")
                else:
                    valid_effects["world_effect"] = True
            elif effect_type == "set_state":
                valid_effects["world_effect"] = True
            else:
                errors.append(f"unsupported world_effect type: {effect_type}")

        speech = skill_result.get("speech_effect")
        if isinstance(speech, dict):
            effect_type = str(speech.get("type") or "")
            if effect_type not in allowed:
                errors.append(f"speech_effect type '{effect_type}' is not allowed for {selected_skill_id}")
            elif effect_type == "direct_message" and self._safe_int(speech.get("receiver_id")) <= 0:
                errors.append("direct_message requires receiver_id")
            elif effect_type == "group_message" and self._safe_int(speech.get("group_id")) <= 0:
                errors.append("group_message requires group_id")
            elif effect_type in {"direct_message", "group_message"}:
                valid_effects["speech_effect"] = True
            else:
                errors.append(f"unsupported speech_effect type: {effect_type}")

        memories = skill_result.get("memory_effects")
        if memories is not None and not isinstance(memories, list):
            errors.append("memory_effects must be a list")
            valid_effects["memory_effects"] = False
        if memories and "remember" not in allowed:
            errors.append(f"memory_effects are not allowed for {selected_skill_id}")
            valid_effects["memory_effects"] = False

        return {"errors": errors, "valid_effects": valid_effects, "allowed_effects": sorted(allowed)}

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _truthy_public_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "public",
            "broadcast",
            "announcement",
            "all",
        }

    def _proposal_allows_public_group(self, proposal: dict[str, Any]) -> bool:
        for key in ("broadcast", "public", "is_public"):
            if self._truthy_public_value(proposal.get(key)):
                return True
        for key in ("scope", "message_scope"):
            if self._truthy_public_value(proposal.get(key)):
                return True
        return False

    def _is_public_speech_effect(
        self,
        effect: dict[str, Any],
        skill_result: dict[str, Any],
    ) -> bool:
        if str(skill_result.get("skill_id") or "") in PUBLIC_GROUP_SKILL_IDS:
            return True
        for key in ("broadcast", "public", "is_public"):
            if self._truthy_public_value(effect.get(key)):
                return True
        for key in ("scope", "message_scope"):
            if self._truthy_public_value(effect.get(key)):
                return True
        return False

    async def _nearby_receiver_id(
        self,
        agent_id: int,
        social_env: Any | None = None,
    ) -> int | None:
        social_env = social_env or self._find_social_environment()
        observe_agent = getattr(social_env, "observe_agent", None)
        if not callable(observe_agent):
            return None
        observed = observe_agent(agent_id)
        if asyncio.iscoroutine(observed):
            observed = await observed
        if not isinstance(observed, dict):
            return None
        for item in observed.get("nearby_agents", []) or []:
            if isinstance(item, dict):
                receiver_id = self._safe_int(item.get("agent_id"))
            else:
                receiver_id = self._safe_int(item)
            if receiver_id > 0 and receiver_id != int(agent_id):
                return receiver_id
        return None

    async def _apply_skill_result(
        self,
        skill_result: dict[str, Any],
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        valid_effects = validation.get("valid_effects") if isinstance(validation, dict) else {}
        world = skill_result.get("world_effect")
        if isinstance(world, dict) and valid_effects.get("world_effect"):
            result = await self._apply_world_effect(world)
            applied.append({"effect": "world_effect", "request": world, "result": result})
        speech = skill_result.get("speech_effect")
        if isinstance(speech, dict) and valid_effects.get("speech_effect"):
            result = await self._apply_speech_effect(speech, skill_result)
            applied.append({"effect": "speech_effect", "request": speech, "result": result})
        memories = skill_result.get("memory_effects")
        if isinstance(memories, list) and valid_effects.get("memory_effects"):
            result = self._append_memory_effects(memories, skill_result)
            applied.append({"effect": "memory_effects", "request": memories, "result": result})
        if validation.get("errors"):
            applied.append({"effect": "validation", "errors": validation.get("errors")})
        return applied

    async def _apply_world_effect(self, effect: dict[str, Any]) -> Any:
        effect_type = str(effect.get("type") or "")
        proposal: dict[str, Any]
        if effect_type == "move":
            proposal = {
                "action_type": "move",
                "agent_id": self.id,
                "location_id": effect.get("location_id") or effect.get("location"),
                "reason": effect.get("reason"),
            }
        elif effect_type == "interact":
            proposal = {
                "action_type": "interact",
                "agent_id": self.id,
                "interaction_id": effect.get("interaction_id"),
                "params": effect.get("params") if isinstance(effect.get("params"), dict) else {},
                "reason": effect.get("reason"),
            }
        elif effect_type == "set_state":
            proposal = {
                "action_type": "set_action",
                "agent_id": self.id,
                "action": effect.get("action") or effect.get("reason") or "繼續日常安排",
                "status": effect.get("status") or "活躍",
                "emotion": effect.get("emotion") or "平靜",
                "reason": effect.get("reason"),
            }
        else:
            return {"ok": False, "error": f"不支援的世界效果：{effect_type}"}
        raw = await self._apply_action_proposal(proposal)
        try:
            return json.loads(raw)
        except Exception:
            return raw

    async def _apply_speech_effect(
        self,
        effect: dict[str, Any],
        skill_result: dict[str, Any] | None = None,
    ) -> Any:
        effect_type = str(effect.get("type") or "")
        if effect_type == "direct_message":
            proposal = {
                "action_type": "direct_message",
                "agent_id": self.id,
                "receiver_id": effect.get("receiver_id"),
                "content": effect.get("content"),
            }
        elif effect_type == "group_message":
            if self._is_public_speech_effect(effect, skill_result or {}):
                proposal = {
                    "action_type": "group_message",
                    "agent_id": self.id,
                    "group_id": effect.get("group_id") or 1,
                    "content": effect.get("content"),
                    "public": True,
                }
            else:
                receiver_id = await self._nearby_receiver_id(self.id)
                if receiver_id is None:
                    return {
                        "ok": False,
                        "error": "no_nearby_agent",
                        "skipped_group_broadcast": True,
                    }
                proposal = {
                    "action_type": "direct_message",
                    "agent_id": self.id,
                    "receiver_id": receiver_id,
                    "content": effect.get("content"),
                    "converted_from_group_message": True,
                }
        else:
            return {"ok": False, "error": f"不支援的發言效果：{effect_type}"}
        raw = await self._apply_action_proposal(proposal)
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def _append_memory_effects(
        self,
        memories: list[dict[str, Any]],
        skill_result: dict[str, Any],
    ) -> dict[str, Any]:
        target = self._runtime_path("memory/skill_memory.jsonl")
        if target is None:
            return {"ok": False, "error": "agent workspace is not initialized"}
        written = 0
        with target.open("a", encoding="utf-8") as f:
            for memory in memories:
                if not isinstance(memory, dict):
                    continue
                entry = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "agent_id": self.id,
                    "skill_id": skill_result.get("skill_id"),
                    **memory,
                }
                f.write(json.dumps(_json_safe(entry), ensure_ascii=False) + "\n")
                written += 1
        return {"ok": True, "written": written, "path": str(target)}

    def _legacy_action_from_skill_result(self, skill_result: dict[str, Any]) -> dict[str, Any]:
        world = skill_result.get("world_effect")
        if not isinstance(world, dict):
            return {}
        effect_type = str(world.get("type") or "")
        if effect_type == "move":
            return {
                "source": skill_result.get("skill_id"),
                "action_type": "move",
                "agent_id": self.id,
                "location_id": world.get("location_id") or world.get("location"),
                "reason": world.get("reason"),
            }
        if effect_type == "interact":
            return {
                "source": skill_result.get("skill_id"),
                "action_type": "interact",
                "agent_id": self.id,
                "interaction_id": world.get("interaction_id"),
                "params": world.get("params") if isinstance(world.get("params"), dict) else {},
                "reason": world.get("reason"),
            }
        if effect_type == "set_state":
            return {
                "source": skill_result.get("skill_id"),
                "action_type": "set_action",
                "agent_id": self.id,
                "action": world.get("action"),
                "status": world.get("status"),
                "emotion": world.get("emotion"),
                "reason": world.get("reason"),
            }
        return {}

    def _action_proposal_from_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        raw = decision.get("action_proposal")
        if isinstance(raw, dict):
            return raw
        action_type = str(decision.get("action_type") or "").strip()
        if not action_type:
            return {}
        proposal = {
            "source": "jiuwen_decision",
            "action_type": action_type,
            "agent_id": self.id,
        }
        for key in (
            "location_id",
            "location",
            "interaction_id",
            "receiver_id",
            "group_id",
            "content",
            "params",
            "action",
            "status",
            "emotion",
            "reason",
            "broadcast",
            "public",
            "is_public",
            "scope",
            "message_scope",
        ):
            if key in decision:
                proposal[key] = decision[key]
        return proposal

    async def _apply_action_proposal(self, proposal: dict[str, Any]) -> str:
        if not proposal:
            return ""
        action_type = str(proposal.get("action_type") or "").strip()
        if not action_type or action_type == "none":
            return ""

        social_env = self._find_social_environment()
        agent_id = int(proposal.get("agent_id") or self.id)
        try:
            if social_env is not None:
                if action_type == "move" and callable(getattr(social_env, "move_agent", None)):
                    result = await social_env.move_agent(
                        agent_id=agent_id,
                        location=str(proposal.get("location_id") or proposal.get("location") or ""),
                    )
                    return json.dumps(result, ensure_ascii=False)
                if action_type == "interact" and callable(getattr(social_env, "interact", None)):
                    result = await social_env.interact(
                        agent_id=agent_id,
                        interaction_id=str(proposal.get("interaction_id") or ""),
                        params=proposal.get("params") if isinstance(proposal.get("params"), dict) else {},
                    )
                    return json.dumps(result, ensure_ascii=False)
                if action_type == "direct_message" and callable(getattr(social_env, "send_message", None)):
                    result = await social_env.send_message(
                        sender_id=agent_id,
                        receiver_id=int(proposal.get("receiver_id") or 0),
                        content=str(proposal.get("content") or ""),
                    )
                    return json.dumps(result, ensure_ascii=False)
                if action_type == "group_message" and callable(getattr(social_env, "send_group_message", None)):
                    if not self._proposal_allows_public_group(proposal):
                        receiver_id = await self._nearby_receiver_id(agent_id, social_env)
                        if receiver_id is None:
                            return json.dumps(
                                {
                                    "ok": False,
                                    "error": "no_nearby_agent",
                                    "skipped_group_broadcast": True,
                                },
                                ensure_ascii=False,
                            )
                        if callable(getattr(social_env, "send_message", None)):
                            result = await social_env.send_message(
                                sender_id=agent_id,
                                receiver_id=receiver_id,
                                content=str(proposal.get("content") or ""),
                            )
                            return json.dumps(result, ensure_ascii=False)
                        return json.dumps(
                            {
                                "ok": False,
                                "error": "direct_message_unavailable",
                                "skipped_group_broadcast": True,
                            },
                            ensure_ascii=False,
                        )
                    result = await social_env.send_group_message(
                        sender_id=agent_id,
                        group_id=int(proposal.get("group_id") or 1),
                        content=str(proposal.get("content") or ""),
                    )
                    return json.dumps(result, ensure_ascii=False)
                if action_type == "set_action" and callable(getattr(social_env, "set_agent_action", None)):
                    result = await social_env.set_agent_action(
                        agent_id=agent_id,
                        action=str(proposal.get("action") or proposal.get("reason") or "繼續日常安排"),
                        status=str(proposal.get("status") or "活躍"),
                        emotion=str(proposal.get("emotion") or "平靜"),
                    )
                    return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return f"動作執行失敗：{exc}"

        instruction = str(proposal.get("environment_instruction") or "").strip()
        if not instruction:
            instruction = f"請執行這個 AgentSociety 動作提案：{json.dumps(proposal, ensure_ascii=False)}"
        try:
            _, env_result = await self.ask_env(
                {"variables": {}},
                instruction,
                readonly=False,
            )
            return str(env_result)
        except Exception as exc:
            return f"動作執行失敗：{exc}"

    async def _broadcast_urgent_interventions(self, instructions: list[str]) -> str:
        urgent_instructions = [
            instruction.strip()
            for instruction in instructions
            if instruction.strip() and self._is_urgent_intervention(instruction)
        ]
        if not urgent_instructions:
            return ""

        content = (
            "緊急通知："
            + "；".join(urgent_instructions)
            + "。請所有人立即暫停當前安排，確認自身安全，並同步撤離、聯絡和物資需求。"
        )
        env = self._find_social_environment()
        if env is not None and callable(getattr(env, "send_group_message", None)):
            try:
                result = await env.send_group_message(
                    sender_id=self.id,
                    group_id=1,
                    content=content,
                )
                return f"已向 1 號群傳送緊急干預通知：{result}"
            except Exception as exc:
                return f"步驟提示前傳送緊急通知失敗：{exc}"

        try:
            _, env_result = await self.ask_env(
                {"variables": {}},
                (
                    f"請讓 {self.id} 號智慧體向 1 號群傳送這條緊急內容：{content}"
                ),
                readonly=False,
            )
            return f"已透過環境路由傳送緊急干預通知：{env_result}"
        except Exception as exc:
            return f"步驟提示前傳送緊急通知失敗：{exc}"

    def _is_urgent_intervention(self, instruction: str) -> bool:
        lowered = instruction.lower()
        return any(keyword in lowered for keyword in URGENT_INTERVENTION_KEYWORDS)

    def _find_social_environment(self) -> Any | None:
        env = getattr(self, "_env", None)
        env_modules = getattr(env, "env_modules", None)
        if isinstance(env_modules, list):
            for module in env_modules:
                if callable(getattr(module, "send_group_message", None)):
                    return module
        if isinstance(env_modules, dict):
            for module in env_modules.values():
                if callable(getattr(module, "send_group_message", None)):
                    return module
        if callable(getattr(env, "send_group_message", None)):
            return env
        return None

    def _runtime_path(self, relative_path: str) -> Path | None:
        if self._agent_work_dir is None:
            return None
        work_root = self._agent_work_dir.resolve()
        target = (work_root / relative_path).resolve()
        try:
            target.relative_to(work_root)
        except ValueError:
            raise ValueError(f"Path escapes agent workspace: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _write_json(self, relative_path: str, data: Any) -> None:
        target = self._runtime_path(relative_path)
        if target is None:
            return
        target.write_text(
            json.dumps(_json_safe(data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_thread_message(
        self,
        role: str,
        content: str,
        *,
        tick: int,
        t: datetime,
    ) -> None:
        target = self._runtime_path(".runtime/logs/thread_messages.jsonl")
        if target is None:
            return
        entry = {
            "tick": tick,
            "time": t.isoformat(),
            "role": role,
            "content": content,
        }
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _workspace_files(self) -> list[str]:
        if self._agent_work_dir is None:
            return []
        return sorted(
            str(path.relative_to(self._agent_work_dir))
            for path in self._agent_work_dir.rglob("*")
            if path.is_file()
        )

    def _persist_runtime_state(self, *, tick: int, t: datetime, status: str) -> None:
        state = {
            "agent_id": self.id,
            "tick": tick,
            "time": t.isoformat(),
            "status": status,
            "token_usage": {},
            "selected_skills": sorted(self._last_selected_skills),
            "activated_skills": sorted(self._last_activated_skills),
            "mounted_skill_ids": self._mounted_skill_ids(),
            "last_skill_decision": dict(self._last_skill_decision),
        }
        snapshot = {
            **state,
            "id": self.id,
            "name": self.name,
            "profile": _json_safe(self.get_profile()),
            "agent_type": self.__class__.__name__,
            "jiuwenclaw_ws_url": self._jiuwenclaw_ws_url,
            "session_id": self._session_id,
            "mode": self._mode,
            "last_response": self._last_response,
            "last_environment_result": self._last_environment_result,
            "pending_interventions": list(self._pending_interventions),
            "recent_live_questions": list(self._recent_live_questions),
            "last_skill_results": list(self._last_skill_results),
            "last_action_proposal": dict(self._last_action_proposal),
            "last_social_action_proposal": dict(self._last_social_action_proposal),
            "mounted_skill_ids": self._mounted_skill_ids(),
            "last_skill_decision": dict(self._last_skill_decision),
            "last_skill_result": dict(self._last_skill_result),
            "last_environment_effects": list(self._last_environment_effects),
            "skill_states": {
                "mounted_skill_ids": self._mounted_skill_ids(),
                "last_skill_decision": dict(self._last_skill_decision),
                "last_skill_result": dict(self._last_skill_result),
                "last_environment_effects": list(self._last_environment_effects),
            },
            "workspace_files": self._workspace_files(),
        }
        if self._enable_skill_runtime and self._agent_work_dir is not None:
            self._skill_runtime.persist_session_state(
                tick=tick,
                t=t,
                selected_skills=self._last_selected_skills,
                activated_skills=self._last_activated_skills,
                token_usage={},
                runtime_snapshot=snapshot,
            )
            self._skill_runtime.append_step_replay(
                tick=tick,
                t=t,
                selected_skills=self._last_selected_skills,
                tool_history=list(self._last_skill_results),
            )
            self._skill_runtime.refresh_workspace_documents()
            return
        self._write_json(".runtime/logs/session_state.json", state)
        self._write_json(".runtime/logs/agent_state_snapshot.json", snapshot)

    async def dump(self) -> dict:
        return {
            "id": self._id,
            "profile": _json_safe(self.get_profile()),
            "name": self._name,
            "jiuwenclaw_ws_url": self._jiuwenclaw_ws_url,
            "session_id": self._session_id,
            "mode": self._mode,
            "trusted_dirs": list(self._trusted_dirs),
            "request_timeout": self._request_timeout,
            "enable_memory": self._enable_memory,
            "channel_id": self._channel_id,
            "enable_skill_runtime": self._enable_skill_runtime,
            "common_skill_ids": list(self._common_skill_ids),
            "skill_ids": list(self._skill_ids),
            "mounted_skill_ids": self._mounted_skill_ids(),
            "experiment_context": _json_safe(self._experiment_context),
            "last_response": self._last_response,
            "last_environment_result": self._last_environment_result,
            "pending_interventions": list(self._pending_interventions),
            "recent_live_questions": list(self._recent_live_questions),
            "last_action_proposal": dict(self._last_action_proposal),
            "last_social_action_proposal": dict(self._last_social_action_proposal),
            "last_skill_decision": dict(self._last_skill_decision),
            "last_skill_result": dict(self._last_skill_result),
            "last_environment_effects": list(self._last_environment_effects),
        }

    async def load(self, dump_data: dict) -> None:
        self._id = int(dump_data.get("id", self._id))
        if "profile" in dump_data:
            self._profile = dump_data["profile"]
        self._name = str(dump_data.get("name", self._name))
        self._jiuwenclaw_ws_url = str(
            dump_data.get("jiuwenclaw_ws_url", self._jiuwenclaw_ws_url)
        )
        self._session_id = str(dump_data.get("session_id", self._session_id))
        self._mode = str(dump_data.get("mode", self._mode))
        trusted_dirs = dump_data.get("trusted_dirs")
        if isinstance(trusted_dirs, list):
            self._trusted_dirs = [str(item) for item in trusted_dirs if str(item)]
        self._request_timeout = float(
            dump_data.get("request_timeout", self._request_timeout)
        )
        self._enable_memory = bool(dump_data.get("enable_memory", self._enable_memory))
        self._channel_id = str(dump_data.get("channel_id", self._channel_id))
        self._enable_skill_runtime = True
        if "experiment_context" in dump_data:
            self._experiment_context = dump_data["experiment_context"]
        common_skill_ids = dump_data.get("common_skill_ids")
        if isinstance(common_skill_ids, list):
            self._common_skill_ids = self._normalize_skill_ids(common_skill_ids)
        skill_ids = dump_data.get("skill_ids")
        if isinstance(skill_ids, list):
            self._skill_ids = self._normalize_skill_ids(skill_ids)
        elif isinstance(dump_data.get("mounted_skill_ids"), list):
            common_set = set(self._common_skill_ids)
            self._skill_ids = [
                item
                for item in self._normalize_skill_ids(dump_data["mounted_skill_ids"])
                if item not in common_set
            ]
        self._last_response = str(dump_data.get("last_response", ""))
        self._last_environment_result = str(
            dump_data.get("last_environment_result", "")
        )
        if isinstance(dump_data.get("last_action_proposal"), dict):
            self._last_action_proposal = dict(dump_data["last_action_proposal"])
        if isinstance(dump_data.get("last_social_action_proposal"), dict):
            self._last_social_action_proposal = dict(
                dump_data["last_social_action_proposal"]
            )
        if isinstance(dump_data.get("last_skill_decision"), dict):
            self._last_skill_decision = dict(dump_data["last_skill_decision"])
        if isinstance(dump_data.get("last_skill_result"), dict):
            self._last_skill_result = dict(dump_data["last_skill_result"])
        if isinstance(dump_data.get("last_environment_effects"), list):
            self._last_environment_effects = [
                item for item in dump_data["last_environment_effects"] if isinstance(item, dict)
            ]
        pending_interventions = dump_data.get("pending_interventions")
        if isinstance(pending_interventions, list):
            self._pending_interventions = [
                str(item) for item in pending_interventions if str(item).strip()
            ]
        recent_live_questions = dump_data.get("recent_live_questions")
        if isinstance(recent_live_questions, list):
            self._recent_live_questions = [
                item for item in recent_live_questions if isinstance(item, dict)
            ][-10:]

    async def close(self) -> None:
        if self._ws is not None:
            close = getattr(self._ws, "close", None)
            if callable(close):
                await close()
            self._ws = None

    def _build_ask_prompt(self, message: str, readonly: bool) -> str:
        readonly_text = (
            "這是隻讀提問，不要產生任何副作用。"
            if readonly
            else "你可以推理行動，但只有 AgentSociety 可以改變模擬環境。"
        )
        return (
            "你正在扮演一個 AgentSociety 模擬智慧體。\n"
            f"智慧體編號：{self.id}\n"
            f"智慧體姓名：{self.name}\n"
            f"角色檔案：{json.dumps(_json_safe(self.get_profile()), ensure_ascii=False)}\n"
            f"{self._experiment_context_text()}"
            f"{CHINESE_OUTPUT_POLICY}\n"
            f"約束：{readonly_text}\n\n"
            f"使用者或環境問題：\n{message}"
        )

    def _build_step_prompt(
        self,
        tick: int,
        t: datetime,
        observation: str,
        pending_interventions: list[str] | None = None,
        broadcast_result: str = "",
        skill_runtime_result: dict[str, Any] | None = None,
    ) -> str:
        intervention_text = ""
        if pending_interventions:
            lines = "\n".join(
                f"{index}. {instruction}"
                for index, instruction in enumerate(pending_interventions, start=1)
            )
            intervention_text = (
                "\n\n本步驟前收到的實時使用者干預：\n"
                f"{lines}\n"
                "除非客觀上無法執行，否則本步驟必須吸收這些干預。"
                "如果幹預要求移動、更新狀態、傳送訊息或改變行為，請把具體效果寫入 environment_instruction。"
                "如果是公共安全緊急情況，優先處理撤離、協作和溝通。\n"
            )
        broadcast_text = (
            f"\n\n自動緊急干預處理結果：\n{broadcast_result}\n"
            if broadcast_result
            else ""
        )
        skill_runtime_text = ""
        if skill_runtime_result:
            skill_runtime_text = (
                "\n\nAgentSociety 可執行技能執行結果：\n"
                f"{json.dumps(_json_safe(skill_runtime_result), ensure_ascii=False, indent=2)}\n"
                "請把它視為已落地的可執行上下文。如果明顯錯誤可以覆蓋；如果認可，請保持 "
                "environment_instruction 為空，AgentSociety 會直接執行提案。\n"
            )
        return (
            "你正在控制一個 AgentSociety 模擬智慧體，只執行一個步驟。\n"
            f"智慧體編號：{self.id}\n"
            f"智慧體姓名：{self.name}\n"
            f"角色檔案：{json.dumps(_json_safe(self.get_profile()), ensure_ascii=False)}\n"
            f"{self._experiment_context_text()}"
            f"{CHINESE_OUTPUT_POLICY}\n"
            f"模擬時間：{t.isoformat()}\n"
            f"單步秒數：{tick}\n"
            f"環境觀察：\n{observation}"
            f"{intervention_text}\n\n"
            f"{broadcast_text}"
            f"{skill_runtime_text}"
            "如果觀察中包含關於緊急情況的 recent_messages 或 latest_event，請把它當作實時資訊並立即調整行動。\n\n"
            "只返回一個 JSON 物件，結構如下：\n"
            "{\n"
            '  "public_summary": "用中文簡短描述本步驟",\n'
            '  "environment_instruction": "給 AgentSociety 環境的中文自然語言動作，或空字串",\n'
            '  "action_proposal": {"action_type": "move|interact|direct_message|group_message|set_action", "location_id": "optional", "interaction_id": "optional", "receiver_id": "direct_message 必填", "content": "必須是中文", "public": "只有公共公告/安全廣播/系統通知才可為 true"}\n'
            "}\n"
            "如果要使用上面的可執行技能提案，可以省略 action_proposal。\n"
            "普通對話必須使用 direct_message，並且只能發給環境觀察中的 nearby_agents。"
            "group_message 只用於明確的公共公告、安全廣播或系統通知；使用時必須設定 public=true。\n"
            "不要用 Markdown 包裹 JSON。"
        )

    async def _send_jiuwenclaw_request(self, prompt: str) -> str:
        request_id = f"agentsociety_{self.id}_{uuid.uuid4().hex[:12]}"
        payload = {
            "request_id": request_id,
            "channel_id": self._channel_id,
            "session_id": self._session_id,
            "req_method": "chat.send",
            "params": {
                "query": prompt,
                "mode": self._mode,
                "trusted_dirs": list(self._trusted_dirs),
            },
            "is_stream": True,
            "timestamp": time.time(),
            "metadata": {
                "source": "agentsociety",
                "agent_id": self.id,
                "agent_name": self.name,
                "enable_memory": self._enable_memory,
            },
        }

        request_started_at = time.perf_counter()
        queue_ms = 0.0
        status = "error"
        semaphore, concurrency_limit = self._request_semaphore_for_current_loop()
        try:
            async with semaphore:
                queue_ms = (time.perf_counter() - request_started_at) * 1000
                async with self._ws_lock:
                    await self._ensure_connected()
                    await self._ws.send(json.dumps(payload, ensure_ascii=False))
                    try:
                        response = await asyncio.wait_for(
                            self._receive_matching_response(request_id),
                            timeout=self._request_timeout,
                        )
                    except Exception:
                        await self._reset_websocket()
                        raise
                content = self._extract_response_content(response)
                status = "success"
                return content
        finally:
            total_ms = (time.perf_counter() - request_started_at) * 1000
            logger.info(
                "[JiuwenClawAgent] request timing: agent_id=%s request_id=%s "
                "status=%s queue_ms=%.1f total_ms=%.1f prompt_chars=%s "
                "concurrency_limit=%s",
                self.id,
                request_id,
                status,
                queue_ms,
                total_ms,
                len(prompt),
                concurrency_limit,
            )

    async def _ensure_connected(self) -> None:
        if self._ws is not None:
            return
        self._ws = await self._open_websocket(self._jiuwenclaw_ws_url)
        await self._consume_connection_ack_if_present()

    async def _open_websocket(self, uri: str) -> Any:
        origin = self._build_ws_origin(uri)
        try:
            from websockets.asyncio.client import connect
        except Exception:
            from websockets import connect

        # Match AgentServer defaults (ping_interval=30, ping_timeout=300).
        # Library defaults (~20s) cause keepalive ping timeout while agent.plan
        # is blocked on long multi-turn LLM+tool streams under 10-way load.
        return await connect(
            uri,
            origin=origin,
            ping_interval=30,
            ping_timeout=300,
        )

    def _build_ws_origin(self, uri: str) -> str | None:
        """Match JiuwenClaw AgentServer's localhost Origin check."""

        try:
            parsed = urlsplit(uri)
        except ValueError:
            return None
        if not parsed.netloc:
            return None
        scheme = "https" if parsed.scheme == "wss" else "http"
        return f"{scheme}://{parsed.netloc}"

    async def _consume_connection_ack_if_present(self) -> None:
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            return
        data = self._decode_wire_message(raw)
        if data.get("type") == "event" and data.get("event") == "connection.ack":
            return
        self._pending_response = data

    async def _receive_matching_response(self, request_id: str) -> dict:
        content_parts: list[str] = []
        final_content: str = ""
        pending = getattr(self, "_pending_response", None)
        if isinstance(pending, dict):
            self._pending_response = None
            if str(pending.get("request_id") or "") == request_id:
                piece = self._extract_stream_piece(pending)
                if piece["final"]:
                    final_content = piece["text"]
                elif piece["text"]:
                    content_parts.append(piece["text"])
                if pending.get("is_final"):
                    return self._with_stream_content(
                        pending,
                        final_content or "".join(content_parts),
                    )

        while True:
            raw = await self._ws.recv()
            data = self._decode_wire_message(raw)
            if data.get("type") == "event":
                continue
            if str(data.get("request_id") or "") == request_id:
                piece = self._extract_stream_piece(data)
                if piece["final"]:
                    final_content = piece["text"]
                elif piece["text"]:
                    content_parts.append(piece["text"])
                if data.get("is_final"):
                    return self._with_stream_content(
                        data,
                        final_content or "".join(content_parts),
                    )

    def _decode_wire_message(self, raw: str | bytes) -> dict:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JiuwenClaw WebSocket response must be a JSON object")
        return data

    def _extract_response_content(self, response: dict) -> str:
        if "ok" in response:
            if not response.get("ok", True):
                payload = response.get("payload") or {}
                raise RuntimeError(str(payload.get("error") or payload))
            payload = response.get("payload") or {}
            return str(payload.get("content") or payload.get("result") or payload)

        body = response.get("body") or {}
        status = str(response.get("status") or "")
        response_kind = str(response.get("response_kind") or "")
        if status == "failed" or response_kind.endswith(".error"):
            raise RuntimeError(str(body.get("message") or body.get("error") or body))

        result = body.get("result")
        if isinstance(result, dict):
            if "content" in result:
                content = result["content"]
                if isinstance(content, dict):
                    if "output" in content:
                        return str(content["output"])
                    if "content" in content:
                        return str(content["content"])
                return str(content)
            return json.dumps(result, ensure_ascii=False)
        if result is not None:
            return str(result)
        if "content" in body:
            return str(body["content"])
        return json.dumps(body, ensure_ascii=False)

    async def _reset_websocket(self) -> None:
        if self._ws is None:
            return
        close = getattr(self._ws, "close", None)
        try:
            if callable(close):
                await close()
        finally:
            self._ws = None
            self._pending_response = None

    def _with_stream_content(self, response: dict, content: str) -> dict:
        if not content:
            return response
        next_response = dict(response)
        body = dict(next_response.get("body") or {})
        body["result"] = {"content": content}
        next_response["body"] = body
        return next_response

    def _extract_stream_piece(self, response: dict) -> dict[str, Any]:
        body = response.get("body") or {}
        delta = body.get("delta")
        if isinstance(delta, dict):
            event_type = str(delta.get("event_type") or body.get("event_type") or "")
            content = delta.get("content")
            if content is None:
                content = delta.get("delta")
            if event_type == "chat.final" and content is not None:
                return {"text": str(content), "final": True}
            if event_type in {"chat.delta", "text.delta"} and content is not None:
                return {"text": str(content), "final": False}
        if body.get("delta_kind") == "text" and isinstance(delta, str):
            return {"text": delta, "final": False}
        payload = response.get("payload")
        if isinstance(payload, dict):
            event_type = str(payload.get("event_type") or "")
            content = payload.get("content")
            if event_type == "chat.final" and content is not None:
                return {"text": str(content), "final": True}
            if event_type == "chat.delta" and content is not None:
                return {"text": str(content), "final": False}
        return {"text": "", "final": False}

    def _parse_step_decision(self, text: str) -> dict[str, Any]:
        data = self._extract_json_object(text)
        if not isinstance(data, dict):
            return {"_parsed": False, "public_summary": text}
        data["_parsed"] = True
        return data

    def _extract_json_object(self, text: str) -> Any:
        stripped = text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fence:
            stripped = fence.group(1).strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        start = stripped.find("{")
        while start != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(stripped[start:])
                return obj
            except json.JSONDecodeError:
                start = stripped.find("{", start + 1)
        return None
