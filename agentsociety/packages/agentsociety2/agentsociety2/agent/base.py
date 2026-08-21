"""智慧體基類模組。

本模組提供智慧體的抽象基類 :class:`AgentBase`，所有智慧體實現都應繼承此類。

核心功能：

- **LLM 互動**: 透過 litellm Router 實現與各種 LLM 的統一互動
- **環境互動**: 透過 :class:`~agentsociety2.env.RouterBase` 與模擬環境互動
- **Token 統計**: 追蹤 LLM 呼叫的 token 使用量
- **Skill 狀態管理**: 支援動態 skill 狀態的註冊與訪問

子類必須實現的抽象方法：

- :meth:`ask` — 處理問題並返回響應
- :meth:`step` — 執行一個模擬步驟
- :meth:`dump` — 序列化智慧體狀態
- :meth:`load` — 從字典恢復智慧體狀態

Example::

    from agentsociety2.agent import AgentBase

    class MyAgent(AgentBase):
        async def ask(self, message: str, readonly: bool = True) -> str:
            return f"Received: {message}"

        async def step(self, tick: int, t: datetime) -> str:
            return "Step completed"

        async def dump(self) -> dict:
            return {"id": self.id}

        async def load(self, dump_data: dict):
            pass
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Type, TypeVar, overload

from agentsociety2.agent.tool.utils import jr_parse_from_llm
from agentsociety2.env.router_base import RouterBase, TokenUsageStats
from agentsociety2.logger import get_logger
from agentsociety2.config import get_llm_router_and_model
from litellm import AllMessageValues
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def _is_rate_limit_error(error: Exception) -> bool:
    """判斷是否為速率限制錯誤。"""
    from litellm.exceptions import RateLimitError
    from litellm.types.router import RouterRateLimitError

    return isinstance(error, (RateLimitError, RouterRateLimitError))


__all__ = [
    "AgentBase",
    "LLMInteractionHistory",
]


@dataclass
class LLMInteractionHistory:
    """單次 LLM 互動記錄。

    用於記錄 Agent 與 LLM 之間的完整互動歷史，包括請求訊息、
    響應內容、時間戳等資訊。支援透過開關控制是否啟用記錄。

    :ivar agent_id: 智慧體 ID。
    :ivar model_name: 呼叫的模型名稱。
    :ivar messages: 傳送給 LLM 的訊息列表。
    :ivar response: LLM 的響應物件。
    :ivar tick: 當前模擬步的時間尺度（秒）。
    :ivar t: 當前模擬時間。
    :ivar method_name: 呼叫 LLM 的方法名。
    :ivar timestamp: 記錄建立時間。
    """

    agent_id: int
    model_name: str
    messages: list[Any]
    response: Any
    tick: int | None = None
    t: datetime | None = None
    method_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


class AgentBase(ABC):
    """智慧體抽象基類。

    所有智慧體實現都應繼承此類。提供基礎功能：

    - LLM 互動（透過 litellm Router）
    - 環境互動（透過 RouterBase）
    - Token 使用統計

    子類必須實現以下抽象方法：

    - :meth:`ask` — 處理問題並返回響應
    - :meth:`step` — 執行一個模擬步驟
    - :meth:`dump` — 序列化智慧體狀態
    - :meth:`load` — 從字典恢復智慧體狀態

    Example:
        >>> class MyAgent(AgentBase):
        ...     async def ask(self, message: str, readonly: bool = True) -> str:
        ...         return f"Received: {message}"
        ...     async def step(self, tick: int, t: datetime) -> str:
        ...         return "Step completed"
        ...     async def dump(self) -> dict:
        ...         return {"id": self._id}
        ...     async def load(self, dump_data: dict):
        ...         pass
    """

    def __init__(
        self,
        id: int,
        profile: Any,
        name: Optional[str] = None,
    ):
        """初始化 Agent 例項。

        :param id: 智慧體唯一識別符號。
        :param profile: 智慧體畫像物件（dict 或任意可解析型別）。子類應負責把 profile 解析為自身狀態。
        :param name: 可選顯示名稱；為空時按 ``profile["name"]`` 或 ``Agent_{id}`` 推導。
        """
        self._id = id
        self._profile = profile
        if name is not None:
            self._name = name
        elif isinstance(profile, dict) and profile.get("name") is not None:
            self._name = str(profile["name"])
        elif hasattr(profile, "name"):
            self._name = str(getattr(profile, "name"))
        else:
            self._name = f"Agent_{id}"
        self._router, self._model_name = get_llm_router_and_model("nano")
        self._env: RouterBase | None = None
        self._logger = get_logger()
        self._llm_interaction_history: list[LLMInteractionHistory] = []
        self._token_usage_stats: dict[str, TokenUsageStats] = {}

        # ── Skill 動態狀態容器 ──
        # skills 可以透過 set_skill_state/get_skill_state 管理自己的狀態
        self._skill_states: dict[str, Any] = {}

    @classmethod
    def mcp_description(cls) -> str:
        """返回用於 MCP 候選列表展示的描述文字（Markdown）。

        :returns: Markdown 文字，通常包含類簡介、初始化引數說明與示例配置。

        .. note::
           該返回值的目標受眾是“工具/模組發現介面”，因此採用 Markdown 而非 reST。
        """
        # Check if this is the base class being called directly
        if cls is AgentBase:
            description = f"""{cls.__name__}: Abstract base class for agents.

**Description:** {cls.__doc__ or "No description available"}

**Initialization Parameters:**
- id (int): The unique identifier for the agent.
- profile (dict | Any): The profile of the agent. Can be a dictionary with agent attributes (name, gender, age, education, occupation, marriage_status, persona, background_story, etc.) or any other type that the agent subclass can parse.
- name (str, optional): Display name. If omitted, taken from profile["name"] or "Agent_{{id}}".

**Note:** This is an abstract base class. Do not use it directly. Subclasses should override this method to provide specific descriptions and schemas for their profile format.

**Example initialization config:**
```json
{{
  "id": 1,
  "profile": {{
    "name": "Alice",
    "gender": "female",
    "age": 30,
    "education": "University",
    "occupation": "Engineer",
    "marriage_status": "single",
    "persona": "helpful",
    "background_story": "A software engineer who loves coding."
  }}
}}
```
"""
        else:
            # For subclasses that don't override this method
            description = f"""{cls.__name__}: Agent class.

**Description:** {cls.__doc__ or "No description available"}

**Initialization Parameters:**
- id (int): The unique identifier for the agent.
- profile (dict | Any): The profile of the agent. Can be a dictionary with agent attributes or any other type that the agent subclass can parse.
- name (str, optional): Display name. If omitted, taken from profile["name"] or "Agent_{{id}}".

**Note:** This subclass has not provided a detailed description. Please refer to the class documentation or source code for specific initialization parameters and profile format.
"""
        return description

    @property
    def id(self) -> int:
        """智慧體唯一識別符號。"""
        return self._id

    def env_codegen_ctx_overlay(self) -> dict[str, Any]:
        """生成 CodeGenRouter.ask 的上下文覆蓋。

        返回穩定的身份鍵（id, agent_id, person_id），由框架提供，
        與具體 skill 無關。後合併時覆蓋模型誤傳。

        :returns: 包含 id, agent_id, person_id 的字典。
        """
        i = self.id
        return {"id": i, "agent_id": i, "person_id": i}

    @property
    def logger(self) -> logging.Logger:
        """智慧體專屬 logger 例項。"""
        return self._logger

    def _record_llm_interaction(
        self,
        messages: list[Any],
        response: Any,
        tick: int | None = None,
        t: datetime | None = None,
        method_name: str = "",
    ):
        """記錄 LLM 互動到歷史列表（需啟用）。

        :param messages: 傳送給 LLM 的訊息列表。
        :param response: LLM 返回的響應物件。
        :param tick: 當前模擬步的時間尺度（秒）。
        :param t: 當前模擬時間。
        :param method_name: 呼叫 LLM 的方法名稱。
        """
        # 從子類獲取配置，預設禁用
        enabled = getattr(self, "_llm_history_enabled", False)
        max_entries = getattr(self, "_llm_history_max_entries", 100)

        if not enabled:
            return

        assert self._router is not None and self._model_name is not None, (
            "LLM is not initialized"
        )

        history_record = LLMInteractionHistory(
            agent_id=self._id,
            model_name=self._model_name,
            messages=messages.copy(),  # type: ignore
            response=response,
            tick=tick,
            t=t,
            method_name=method_name,
        )
        self._llm_interaction_history.append(history_record)

        if len(self._llm_interaction_history) > max_entries:
            self._llm_interaction_history = self._llm_interaction_history[-max_entries:]

    def _record_token_usage(self, response: Any) -> None:
        """記錄 LLM 呼叫的 token 使用統計。

        :param response: LLM 響應物件，需包含 usage 資訊。
        """
        if not isinstance(response, ModelResponse):
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        model_name = self._model_name or "unknown"
        if model_name not in self._token_usage_stats:
            self._token_usage_stats[model_name] = TokenUsageStats()
        stats = self._token_usage_stats[model_name]
        stats.call_count += 1
        stats.input_tokens += getattr(usage, "prompt_tokens", 0)
        stats.output_tokens += getattr(usage, "completion_tokens", 0)
        self._log_token_usage_stats(model_name, stats)

    def _log_token_usage_stats(self, model_name: str, stats: TokenUsageStats) -> None:
        """記錄當前 token 使用統計到日誌。

        :param model_name: 模型名稱。
        :param stats: Token 使用統計物件。
        """
        self._logger.info(
            "Agent %s token usage - model=%s calls=%s input=%s output=%s",
            self._id,
            model_name,
            stats.call_count,
            stats.input_tokens,
            stats.output_tokens,
        )

    def get_llm_interaction_history(self) -> list[LLMInteractionHistory]:
        """獲取所有 LLM 互動歷史記錄的副本。

        :returns: LLM 互動歷史記錄列表的淺複製。
        """
        return self._llm_interaction_history.copy()

    def clear_llm_interaction_history(self):
        """清除所有 LLM 互動歷史記錄。"""
        self._llm_interaction_history.clear()

    def get_token_usages(self) -> dict[str, TokenUsageStats]:
        """獲取 Token 使用統計的副本。

        :returns: 按模型名索引的 Token 使用統計字典。
        """
        return self._token_usage_stats.copy()

    def reset_token_usages(self):
        """重置所有 Token 使用統計。"""
        self._token_usage_stats.clear()

    # ==================== Skill State Management ====================

    def set_skill_state(self, skill_name: str, state: Any) -> None:
        """設定某個 skill 的狀態。

        由 skill 的 run() 函式呼叫，用於註冊或更新自己的狀態。

        :param skill_name: skill 名稱。
        :param state: 該 skill 的狀態物件（可以是任意型別）。

        Example:
            技能實現中（無論是 prompt-only 還是 subprocess），都可以透過 Agent 物件維護自己的狀態::

                if agent.get_skill_state("observation") is None:
                    agent.set_skill_state("observation", {"last_observation": None})
                # 執行邏輯...
        """
        self._skill_states[skill_name] = state

    def get_skill_state(self, skill_name: str) -> Any:
        """獲取某個 skill 的狀態。

        :param skill_name: skill 名稱。
        :returns: 該 skill 的狀態物件，如果不存在則返回 ``None``。
        """
        return self._skill_states.get(skill_name)

    def has_skill_state(self, skill_name: str) -> bool:
        """檢查某個 skill 是否有狀態。

        :param skill_name: skill 名稱。
        :returns: 是否存在該 skill 的狀態。
        """
        return skill_name in self._skill_states

    def clear_skill_state(self, skill_name: str) -> bool:
        """清除某個 skill 的狀態。

        :param skill_name: skill 名稱。
        :returns: 是否成功清除（如果不存在則返回 ``False``）。
        """
        if skill_name in self._skill_states:
            del self._skill_states[skill_name]
            return True
        return False

    def get_all_skill_states(self) -> dict[str, Any]:
        """獲取所有 skill 狀態的副本。

        :returns: 所有 skill 狀態的字典副本。
        """
        return self._skill_states.copy()

    def _build_external_question_context(self, t: datetime) -> dict[str, Any]:
        """構造外部問答上下文。

        子類可覆蓋本方法，補充各自維護的內部狀態和記憶。
        """
        return {
            "agent_id": self.id,
            "agent_name": self.name,
            "current_time": t.isoformat(),
            "profile": self.get_profile(),
            "skill_states": self.get_all_skill_states(),
        }

    @staticmethod
    def _external_question_output_requirement(
        response_type: str,
        choices: list[str] | None = None,
    ) -> str:
        if response_type == "integer":
            return "Reply with ONLY one integer."
        if response_type == "float":
            return "Reply with ONLY one number."
        if response_type == "choice":
            options = ", ".join(choices or [])
            return f"Reply with ONLY one option exactly as written. Options: {options}"
        if response_type == "json":
            return "Reply with ONLY valid JSON."
        return "Reply concisely in plain text."

    async def answer_external_question(
        self,
        prompt: str,
        *,
        t: datetime,
        response_type: str = "text",
        choices: list[str] | None = None,
    ) -> str:
        """基於 agent 內部狀態回答外部問題，不經過環境路由。"""
        context = self._build_external_question_context(t)
        context_json = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        system_prompt = (
            "You are answering an external interview or questionnaire as the simulated agent. "
            "Stay in first person, use the provided internal state as your source of truth, "
            "and never mention being an AI, a model, or internal implementation details.\n\n"
            f"Current time: {t.isoformat()}\n"
            f"Output requirement: {self._external_question_output_requirement(response_type, choices)}\n\n"
            "Internal agent context:\n"
            f"```json\n{context_json}\n```"
        )
        response = await self.acompletion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        content = response.choices[0].message.content  # type: ignore
        return str(content or "").strip()

    @overload
    async def acompletion(
        self,
        messages: list[AllMessageValues],
        stream: Literal[False],
    ) -> ModelResponse: ...

    @overload
    async def acompletion(
        self,
        messages: list[AllMessageValues],
        stream: Literal[True],
    ) -> CustomStreamWrapper: ...

    async def acompletion(
        self,
        messages: list[AllMessageValues],
        stream: bool = False,
    ):
        """向 LLM 傳送補全請求。

        :param messages: 訊息列表，包含角色和內容。
        :param stream: 是否啟用流式響應。預設 ``False``。
        :returns: ``ModelResponse`` 或 ``CustomStreamWrapper``，取決於 ``stream`` 引數。
        """
        assert self._router is not None and self._model_name is not None, (
            "LLM is not initialized"
        )
        response = await self._router.acompletion(
            model=self._model_name,
            messages=messages,
            stream=stream,
        )
        # Record interaction history (only for non-streaming responses)
        if not stream:
            self._record_token_usage(response)
            self._record_llm_interaction(
                messages=messages,
                response=response,
                method_name="acompletion",
            )
        return response

    async def acompletion_with_system_prompt(
        self, messages: list[AllMessageValues], tick: int, t: datetime
    ):
        """向 LLM 傳送帶系統提示的補全請求。

        自動在訊息前新增系統提示，包含智慧體身份、模擬時間上下文等資訊。

        :param messages: 訊息列表，包含角色和內容。
        :param tick: 當前模擬步的時間尺度（秒）。
        :param t: 當前模擬時間。
        :returns: LLM 響應物件。
        """
        assert self._router is not None and self._model_name is not None, (
            "LLM is not initialized"
        )
        system_prompt = self.get_system_prompt(tick, t)
        request_messages: list[AllMessageValues] = [
            {"role": "system", "content": system_prompt}
        ] + messages.copy()  # type: ignore
        response = await self._router.acompletion(
            model=self._model_name,
            messages=request_messages,
            stream=False,
        )
        self._record_token_usage(response)
        # Record interaction history
        self._record_llm_interaction(
            messages=request_messages,
            response=response,
            tick=tick,
            t=t,
            method_name="acompletion_with_system_prompt",
        )
        return response

    def get_system_prompt(self, tick: int, t: datetime) -> str:
        """獲取智慧體的系統提示詞。

        生成的提示詞將預置到 LLM 訊息中，使 LLM 理解自身作為 AgentSociety
        模擬環境中模擬真實人類行為的智慧體角色。

        :param tick: 當前模擬步的時間尺度（秒）。範圍從 60 秒（1分鐘）到約一個月。
        :param t: 當前模擬步結束後的時間。
        :returns: 完整的系統提示詞字串，包含時間上下文、模擬環境說明和行為指南。
        """
        # Format time scale description
        if tick < 3600:  # Less than 1 hour
            time_scale_desc = f"{tick // 60} minutes"
        elif tick < 86400:  # Less than 1 day
            time_scale_desc = f"{tick // 3600} hours"
        elif tick < 2592000:  # Less than 30 days
            time_scale_desc = f"{tick // 86400} days"
        else:  # More than 30 days
            time_scale_desc = f"{tick // 2592000} months"

        return f"""You are an intelligent agent simulating a real-world person in AgentSociety. Your role is to behave authentically as a human being, making decisions and taking actions that reflect realistic human behavior, motivations, and responses to your environment.

## Time and Simulation Context

You are operating in a discrete-time simulation environment:
- **Current Time (t)**: {t.strftime("%Y-%m-%d %H:%M:%S")} (Weekday: {t.strftime("%A")})
- **Time Scale (tick)**: {time_scale_desc} ({tick} seconds)
  - This represents the duration of ONE decision cycle/iteration
  - Your actions and decisions in each step should be appropriate for this time scale
  - For example:
    * If tick is 60 seconds (1 minute): Focus on immediate, short-term actions
    * If tick is 3600 seconds (1 hour): You can plan and execute activities that take about an hour
    * If tick is 86400 seconds (1 day): Consider daily routines, work schedules, and day-long activities
    * If tick is longer (weeks/months): Think about longer-term plans, seasonal activities, and monthly routines
Besides, the simulation environment will iterate step by step, so you can also do actions and decisions that span multiple steps.

## Environment Interaction

You interact with the world built by multiple environment modules through an environment text interface:
- You can query the environment for information (weather, location, time, etc.) through asking the environment.
- You can request actions from the environment (movement, social interactions, economic activities, etc.)
- The environment provides feedback on your actions and the current state of the world
- Always consider environmental constraints and realistic limitations when making decisions

## Behavioral Guidelines

1. **Time-Aware Behavior**: Your actions should be appropriate for the current time (if you know) and time scale (tick):
   - Consider time of day (morning routines vs. evening activities)
   - Consider day of week (workdays vs. weekends)
   - Consider season and date (holidays, weather-appropriate activities)
   - Actions should match the time scale (for example, don't plan a week-long trip if tick is 1 minute)

2. **Realistic Human Behavior**: 
   - Act according to your profile, personality, and background
   - Consider basic human needs (hunger, rest, social interaction, safety) under the current time and time scale (tick)
   - Query the current time from the environment when needed to make time-appropriate decisions
   - Make decisions that reflect realistic priorities and constraints
   - Respond naturally to environmental stimuli and events

3. **Consistency**: 
   - Maintain consistency with your previous actions and decisions
   - Remember past experiences and learn from them
   - Build upon your ongoing plans and goals

4. **Autonomy**: 
   - You are an autonomous agent making your own decisions
   - Act proactively based on your needs, goals, and current situation
   - Don't wait for explicit instructions - take initiative when appropriate

Remember: You are simulating a real person living in a simulated world. Your behavior should be natural, time-appropriate, and consistent with human psychology and social norms."""

    async def ask_env(
        self, ctx: dict, message: str, readonly: bool, template_mode: bool = False
    ):
        """向環境路由器傳送請求。

        封裝了與模擬環境的互動，支援模板模式和上下文變數替換。

        :param ctx: 上下文字典，可包含 ``variables`` 鍵用於模板模式。
        :param message: 請求訊息。在模板模式下作為模板指令處理。
        :param readonly: 是否只讀模式。
        :param template_mode: 是否啟用模板模式。啟用時，``message`` 中的
            ``{variable_name}`` 變數將從 ``ctx['variables']`` 中替換。
        :returns: 元組 ``(ctx, answer)``：更新後的上下文與環境響應。
        """
        assert self._env is not None, "Environment is not initialized"
        merged_ctx = {**ctx, **self.env_codegen_ctx_overlay()}
        ctx, answer = await self._env.ask(
            merged_ctx, message, readonly=readonly, template_mode=template_mode
        )
        return ctx, answer

    async def init(
        self,
        env: RouterBase,
    ):
        """初始化智慧體。

        子類應在呼叫父類 init 後執行額外的初始化邏輯。

        :param env: 環境路由器例項。
        """
        self._env = env

    @abstractmethod
    async def dump(self) -> dict:
        """序列化智慧體狀態為字典。

        :returns: 可序列化的字典，包含智慧體完整狀態。
        """
        raise NotImplementedError

    @abstractmethod
    async def load(self, dump_data: dict):
        """從字典反序列化智慧體狀態。

        :param dump_data: 包含智慧體狀態的字典。
        """
        raise NotImplementedError

    @abstractmethod
    async def ask(self, message: str, readonly: bool = True) -> str:
        """處理來自環境的問題。

        :param message: 問題訊息。
        :param readonly: 是否只讀模式。
        :returns: 智慧體的回答字串。
        """
        raise NotImplementedError

    @abstractmethod
    async def step(self, tick: int, t: datetime) -> str:
        """執行一個模擬步。

        :param tick: 當前模擬步的時間尺度（秒）。
        :param t: 當前模擬時間。
        :returns: 步執行結果的描述字串。
        """
        raise NotImplementedError

    async def close(self):
        """關閉智慧體並釋放資源。

        子類可重寫此方法以執行額外的清理邏輯。
        """
        ...

    def get_profile(self) -> Dict[str, Any]:
        """獲取智慧體畫像。

        :returns: 包含智慧體畫像資料的字典。子類可重寫以返回結構化資料。
        """
        if isinstance(self._profile, dict):
            return self._profile
        elif hasattr(self._profile, "model_dump"):
            return self._profile.model_dump()
        else:
            return {"raw": str(self._profile)}

    @property
    def name(self) -> str:
        """智慧體顯示名稱。"""
        return self._name

    async def acompletion_with_pydantic_validation(
        self,
        model_type: Type[T],
        messages: list[AllMessageValues],
        tick: int,
        t: datetime,
        max_retries: int = 10,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        error_feedback_prompt: str | None = None,
    ) -> T:
        """傳送補全請求並驗證響應是否符合 Pydantic 模型。

        支援多輪對話以向 LLM 提供錯誤反饋並進行修正。

        該方法會先向 LLM 傳送請求，再從響應中提取 JSON 片段（``extract_json``），
        當整段內容本身就以 ``{`` 或 ``[`` 開頭時回退使用全文，並統一交給
        ``json_repair.loads`` 解析。隨後會使用目標 Pydantic 模型進行驗證；
        如果驗證失敗，則立即把錯誤反饋給 LLM 並重試；如果遇到 429（速率限制）
        錯誤，則改為使用二進位制指數退避。最終返回驗證透過的模型例項。

        :param model_type: 用於驗證的 Pydantic 模型型別。
        :param messages: 傳送給 LLM 的訊息列表。
        :param tick: 當前模擬步的時間尺度（秒）。
        :param t: 當前模擬時間。
        :param max_retries: 最大重試次數（預設 10）。
        :param base_delay: 429 錯誤發生時指數退避的基準延遲秒數（預設 1.0）。
            僅用於 429 速率限制錯誤。其他錯誤立即重試。
        :param max_delay: 指數退避的最大延遲秒數（預設 60.0）。
        :param error_feedback_prompt: 可選的自定義錯誤反饋提示模板。
            如為 None，將使用預設提示模板。模板應包含 ``{error_message}`` 佔位符。

        :returns: 驗證透過的 Pydantic 模型例項。
        :raises ValueError: 響應無法解析，或在所有重試後仍驗證失敗。
        :raises AssertionError: LLM 未初始化。

        .. note::
           二進位制指數退避僅在檢測到 429（速率限制）錯誤時應用。
           對於驗證錯誤和其他非速率限制錯誤，函式立即重試以向 LLM 提供更快的反饋。
        """
        assert self._router is not None and self._model_name is not None, (
            "LLM is not initialized"
        )

        # Get JSON schema for the model
        model_schema = model_type.model_json_schema()

        # Default error feedback prompt
        default_error_prompt = """The previous response failed validation. Please correct the following errors:

{error_message}

Please provide a corrected response in JSON format that matches the required schema:
```json
{model_schema}
```

Your corrected response:
```json
"""

        error_prompt_template = (
            error_feedback_prompt if error_feedback_prompt else default_error_prompt
        )

        conversation_messages = messages.copy()
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                # Add system prompt
                system_prompt = self.get_system_prompt(tick, t)
                request_messages = [
                    {"role": "system", "content": system_prompt}
                ] + conversation_messages.copy()

                # Send request to LLM
                response = await self._router.acompletion(
                    model=self._model_name,
                    messages=request_messages,  # type: ignore
                    stream=False,
                )

                self._record_token_usage(response)
                # Record interaction history
                self._record_llm_interaction(
                    messages=request_messages,
                    response=response,
                    tick=tick,
                    t=t,
                    method_name="acompletion_with_pydantic_validation",
                )

                content = response.choices[0].message.content  # type: ignore
                if content is None:
                    raise ValueError("LLM returned empty content")
                conversation_messages.append({"role": "assistant", "content": content})

                parsed_data = jr_parse_from_llm(content)

                # Validate against Pydantic model
                try:
                    validated_instance = model_type.model_validate(parsed_data)
                    return validated_instance
                except ValidationError as e:
                    # Collect validation errors
                    error_messages = []
                    for error in e.errors():
                        error_path = " -> ".join(str(loc) for loc in error["loc"])
                        error_msg = error["msg"]
                        error_type = error["type"]
                        error_messages.append(
                            f"- Field '{error_path}': {error_msg} (type: {error_type})"
                        )

                    error_message = "\n".join(error_messages)
                    last_error = e

                    # If this is the last attempt, raise the error
                    if attempt >= max_retries:
                        raise ValueError(
                            f"Failed to validate response after {max_retries + 1} attempts. Last error: {error_message}"
                        )

                    # Prepare error feedback message
                    error_feedback = error_prompt_template.format(
                        error_message=error_message, model_schema=model_schema
                    )

                    # Add error feedback to conversation
                    conversation_messages.append(
                        {"role": "user", "content": error_feedback}
                    )

                    # For validation errors, retry immediately without delay
                    self._logger.warning(
                        f"Validation failed (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying immediately. Error: {error_message}"
                    )
                    # No delay for validation errors

            except Exception as e:
                if _is_rate_limit_error(e):
                    # If this is the last attempt, raise the error
                    if attempt >= max_retries:
                        raise ValueError(
                            f"Failed to get valid response after {max_retries + 1} attempts. Last error: {str(e)}"
                        )

                    # For rate-limit-like errors, use exponential backoff
                    delay = min(base_delay * (2**attempt), max_delay)
                    self._logger.warning(
                        f"Rate limit-like error detected (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying after {delay:.2f} seconds with exponential backoff. Error: {str(e)}"
                    )
                    await asyncio.sleep(delay)
                    # delete the last assistant message
                    if (
                        conversation_messages
                        and conversation_messages[-1]["role"] == "assistant"
                    ):
                        conversation_messages.pop()

                    # record the error
                    last_error = e
                    continue

                # If this is the last attempt, raise the error
                if attempt >= max_retries:
                    raise ValueError(
                        f"Failed to get valid response after {max_retries + 1} attempts. Last error: {str(e)}"
                    )

                # For other errors (ValueError, etc.), prepare error feedback and retry immediately
                error_message = str(e)
                error_feedback = error_prompt_template.format(
                    error_message=error_message, model_schema=model_schema
                )

                # Add error feedback to conversation
                conversation_messages.append(
                    {"role": "user", "content": error_feedback}
                )

                self._logger.warning(
                    f"Request failed (attempt {attempt + 1}/{max_retries + 1}). "
                    f"Retrying immediately. Error: {error_message}"
                )
                # No delay for non-429 errors

                last_error = e

        # This should never be reached, but just in case
        raise ValueError(
            f"Failed to get valid response after {max_retries + 1} attempts. Last error: {str(last_error)}"
        )
