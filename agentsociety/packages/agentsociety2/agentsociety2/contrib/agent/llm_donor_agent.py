import asyncio
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from agentsociety2.agent.base import AgentBase
from mem0 import AsyncMemory
from mem0.memory.main import MemoryConfig


class LLMDonorAgent(AgentBase):
    """LLM 驅動的捐贈者智慧體

    核心特性：
    1. 基於 LLM 動態決策
    2. 整合記憶系統（mem0）用於學習
    3. 週期性觀察頂尖智慧體並學習
    4. 根據情境資訊（聲譽、收益、記憶）做出決策

    Profile 欄位（字典格式）：
    - learning_frequency: int (可選) -> 學習頻率，預設5（每5步學習一次）
    - personality: str (可選) -> 智慧體個性描述
    - initial_mood: float (可選) -> 初始情緒值（-1.0 ~ 1.0）
    - risk_tolerance: float (可選) -> 風險偏好（0.0 ~ 1.0）
    """

    # 常量定義
    DEFAULT_POPULATION_SIZE = 5
    DEFAULT_LEARNING_FREQUENCY = 5
    MOOD_DECAY_RATE = 0.95
    MOOD_ADJUSTMENT_STEP = 0.1
    PAYOFF_THRESHOLD_HIGH = 50.0
    PAYOFF_THRESHOLD_LOW = 0.0
    MOOD_THRESHOLD_NEGATIVE = -0.3
    RISK_THRESHOLD_LOW = 0.3
    RECENT_MEMORIES_COUNT = 5
    DECISION_HISTORY_LIMIT = 100
    TOP_AGENTS_COUNT = 5

    @classmethod
    def mcp_description(cls) -> str:
        """
        Return a description text for MCP agent module candidate list.
        Includes parameter descriptions.
        """
        description = f"""{cls.__name__}: LLM-driven donor agent with memory and learning capabilities.

**Description:** LLM-driven donor agent that makes donation decisions based on reputation, payoffs, and memory. Features dynamic mood and risk tolerance that influence decision-making.

**Initialization Parameters:**
- id (int): The unique identifier for the agent.
- profile (dict | Any): The profile of the agent. Can include custom_fields with:
  - learning_frequency (int, optional): How often to learn from top agents (default: 5)
  - personality (str, optional): Agent personality description (affects decision-making)
  - initial_mood (float, optional): Initial mood value from -1.0 (negative) to 1.0 (positive)
  - risk_tolerance (float, optional): Risk preference from 0.0 (conservative) to 1.0 (risk-taking)
- memory_config (dict | None, optional): Configuration for mem0 memory system
- learning_frequency (int, optional): Learning frequency override (default: 5)

**Game Rules:**
This agent participates in a reputation-based donation game where it can choose to cooperate (donate) or defect. Donations cost 1 point but give the recipient 5 points. The agent considers reputation, past experiences, mood, and risk tolerance when making decisions.

**Example initialization config:**
```json
{{
  "id": 1,
  "profile": {{
    "custom_fields": {{
      "learning_frequency": 5,
      "personality": "You are a rational and cautious agent, tending to maximize long-term benefits.",
      "initial_mood": 0.5,
      "risk_tolerance": 0.6
    }}
  }},
  "memory_config": null,
  "learning_frequency": 5
}}
```
"""
        return description

    def __init__(
        self,
        id: int,
        profile: Any,
        memory_config: dict | None = None,
        learning_frequency: int = 5,
    ):
        super().__init__(id=id, profile=profile)

        # 記憶系統（mem0）
        self._memory_user_id = f"agent_{id}"
        self._memory: AsyncMemory | None = None
        
        # Initialize memory from config if provided
        if memory_config is not None:
            memory_config_obj = MemoryConfig.model_validate(memory_config)
            self._memory = AsyncMemory(config=memory_config_obj)

        # 學習配置
        custom_fields = self._extract_custom_fields(profile)
        learning_freq = (
            custom_fields.get("learning_frequency")
            if isinstance(custom_fields, dict)
            else None
        )
        self._learning_frequency = self._coerce_int(
            learning_freq,
            learning_frequency if learning_frequency is not None else self.DEFAULT_LEARNING_FREQUENCY,
        )

        # 狀態追蹤
        self._step_count = 0
        self._decision_history: List[Dict] = []

        # 快取 Z 值（種群大小），第一次使用時從環境查詢
        self._cached_Z: Optional[int] = None

        # 主觀情緒/個性狀態（用於影響決策）
        self._personality = custom_fields.get(
            "personality", self._generate_random_personality()
        )
        self._mood = self._coerce_float(
            custom_fields.get("initial_mood"), random.uniform(-1.0, 1.0)
        )  # -1.0 (消極) 到 1.0 (積極)
        self._risk_tolerance = self._coerce_float(
            custom_fields.get("risk_tolerance"), random.uniform(0.0, 1.0)
        )  # 0.0 (保守) 到 1.0 (冒險)

    def _generate_random_personality(self) -> str:
        """生成隨機個性描述（僅在未提供 personality 時使用，作為後備方案）"""
        # 預設個性列表（僅在未提供 personality 時使用）
        personalities = [
            "You are a rational and cautious agent, tending to maximize long-term benefits.",
            "You are an emotional agent, and your decisions are influenced by your current emotional state.",
            "You are a fair-minded agent, tending to help those with good reputation and refusing to help those with bad reputation.",
            "You are an altruistic agent, more willing to help others even if it may harm your short-term benefits.",
            "You are a selfish agent, mainly focusing on your own benefits and not caring much about others' reputation.",
            "You are a vengeful agent, if others treat you badly, you will remember and take revenge.",
            "You are an optimistic agent, believing that cooperation will bring better results.",
            "You are a pessimistic agent, tending to protect yourself and not trusting others much.",
        ]
        return random.choice(personalities)

    @staticmethod
    def _extract_custom_fields(profile: Any) -> Dict[str, Any]:
        """從 profile 中提取 custom_fields"""
        if isinstance(profile, dict):
            return profile.get("custom_fields", {}) or {}
        else:
            custom_fields = getattr(profile, "custom_fields", {}) or {}
            if isinstance(custom_fields, dict):
                return custom_fields
            else:
                # 如果不是字典，嘗試轉換為字典
                return {"raw": str(custom_fields)}

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        """將值轉換為整數，失敗時返回預設值"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        """將值轉換為浮點數，失敗時返回預設值"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def step(self, tick: int, t: datetime) -> str:
        """每個 tick 執行一次

        流程：
        1. 隨機選擇接受者
        2. 收集情境資訊（聲譽、收益、記憶）
        3. LLM 決策（合作/背叛）
        4. 執行捐贈
        5. 週期性學習
        """
        self._step_count += 1

        # 1. 隨機選擇接受者
        target_id = await self._select_recipient()

        # 2. 收集情境資訊
        context_info = await self._gather_context(target_id)

        # 3. LLM 決策
        decision = await self._make_decision(context_info, target_id)

        # 4. 執行捐贈
        result = await self._execute_donation(target_id, decision)

        # 5. 週期性學習
        if self._step_count % self._learning_frequency == 0:
            await self._learn_from_top_agents()

        return result

    async def _get_population_size(self) -> int:
        """從環境獲取種群大小 Z，並快取結果"""
        if self._cached_Z is not None:
            return self._cached_Z

        # 從環境查詢 Z 值
        try:
            ctx, ans, _ = await self.ask_env(
                {}, "Query the population size (Z) of the environment.", readonly=True
            )
            Z = ctx.get("population_size") or ctx.get("Z")
            if Z is not None:
                self._cached_Z = int(Z)
                return self._cached_Z
        except Exception:
            # 如果查詢失敗，使用預設值
            pass

        # 如果查詢失敗，使用預設值（向後相容）
        self._cached_Z = self.DEFAULT_POPULATION_SIZE
        return self._cached_Z

    async def _select_recipient(self) -> int:
        """選擇接受者（隨機選擇）"""
        # 從環境獲取 Z 值（帶快取）
        Z = await self._get_population_size()
        candidates = [i for i in range(Z) if i != self.id]
        if not candidates:
            # 如果只有自己，丟擲異常而不是返回無效值
            raise ValueError(
                f"Agent {self.id}: No valid recipients available (population size: {Z})"
            )
        return random.choice(candidates)

    async def _query_agent_reputation(self, agent_id: int) -> str:
        """查詢智慧體的聲譽"""
        ctx, _, _ = await self.ask_env(
            {"agent_id": agent_id},
            f"Query the current reputation of Agent {agent_id}. Use agent_id={agent_id} exactly.",
            readonly=True,
        )
        return ctx.get("reputation", "good")

    async def _query_agent_payoff(self, agent_id: int) -> float:
        """查詢智慧體的收益"""
        ctx, _, _ = await self.ask_env(
            {"agent_id": agent_id},
            f"Query the cumulative payoff of Agent {agent_id}. Use agent_id={agent_id} exactly.",
            readonly=True,
        )
        return self._coerce_float(ctx.get("payoff"), 0.0)

    async def _get_recent_memories(self) -> str:
        """獲取最近的記憶摘要"""
        if not self._memory:
            return ""
        try:
            memories = await self._memory.get_all(user_id=self._memory_user_id)
            if isinstance(memories, dict):
                items = memories.get("memories", []) or memories.get("data", [])
                if items:
                    # 取最近N條記憶
                    recent_memories = (
                        items[-self.RECENT_MEMORIES_COUNT :]
                        if len(items) > self.RECENT_MEMORIES_COUNT
                        else items
                    )
                    return "\n".join(
                        [f"- {m.get('memory', '')}" for m in recent_memories]
                    )
        except Exception:
            # 記憶查詢失敗不影響主流程，靜默處理
            pass
        return ""

    async def _gather_context(self, recipient_id: int) -> Dict[str, Any]:
        """收集決策所需的情境資訊（並行查詢以提高效率）"""
        # 並行查詢自己的聲譽和收益，以及接受者的聲譽
        my_rep_task = self._query_agent_reputation(self.id)
        my_payoff_task = self._query_agent_payoff(self.id)
        recipient_rep_task = self._query_agent_reputation(recipient_id)
        memory_task = self._get_recent_memories()

        # 等待所有查詢完成
        my_rep, my_payoff, recipient_rep, memory_summary = await asyncio.gather(
            my_rep_task, my_payoff_task, recipient_rep_task, memory_task
        )

        return {
            "my_id": self.id,
            "my_reputation": my_rep,
            "my_payoff": my_payoff,
            "recipient_id": recipient_id,
            "recipient_reputation": recipient_rep,
            "memory_summary": memory_summary,
        }

    async def _make_decision(
        self, context_info: Dict[str, Any], recipient_id: int
    ) -> str:
        """LLM 決策：合作或背叛（基於主觀情緒和個性）"""

        # 根據當前收益和情緒狀態更新情緒
        self._update_mood(context_info)

        # 構建情緒描述
        mood_description = self._get_mood_description()
        risk_description = self._get_risk_description()

        prompt = f"""You are an agent participating in a donation game.

{self._personality}

Current situation:
- Your ID: {context_info['my_id']}
- Your current reputation: {context_info['my_reputation']}
- Your cumulative payoff: {context_info['my_payoff']:.2f}
- Recipient ID: {recipient_id}
- Recipient's reputation: {context_info['recipient_reputation']}

Your subjective state:
- Current mood: {mood_description} (mood value: {self._mood:.2f}, range: -1.0 to 1.0)
- Risk preference: {risk_description} (risk tolerance: {self._risk_tolerance:.2f}, range: 0.0 to 1.0)

Historical memories:
{context_info['memory_summary'] if context_info['memory_summary'] else '(No memories yet)'}

Action options:
1. Cooperate: Pay cost c=1, recipient gains benefit b=5
2. Defect: Pay no cost, recipient gains no benefit

Important notes:
- Your decision should be influenced by your personality, current mood, and risk preference
- Don't always choose to cooperate, make authentic decisions based on your subjective feelings
- If you are in a negative mood or have low risk preference, you may tend to protect yourself (defect)
- If you are in a positive mood or have high risk preference, you may be more willing to try cooperation
- Consider the recipient's reputation: if the recipient has a bad reputation, you may be less willing to help them

Please make an authentic decision based on your personality, mood state, risk preference, and current situation.
Only return "cooperate" or "defect", do not return any other content.
"""

        response = await self.acompletion(
            [{"role": "user", "content": prompt}], stream=False
        )

        # 安全地提取響應內容
        if not response or not response.choices or not response.choices[0].message:
            # 如果響應無效，使用預設決策邏輯
            if self._mood < self.MOOD_THRESHOLD_NEGATIVE or self._risk_tolerance < self.RISK_THRESHOLD_LOW:
                decision = "defect"
            else:
                decision = "cooperate"
            decision_text = decision
        else:
            content = response.choices[0].message.content
            decision_text = (content or "").strip().lower()

        # 解析決策
        if "cooperate" in decision_text or "合作" in decision_text:
            decision = "cooperate"
        elif (
            "defect" in decision_text
            or "背叛" in decision_text
            or "拒絕" in decision_text
        ):
            decision = "defect"
        else:
            # 根據情緒和風險偏好決定預設行為，而不是總是合作
            # 如果情緒消極或風險容忍度低，預設背叛；否則預設合作
            if (
                self._mood < self.MOOD_THRESHOLD_NEGATIVE
                or self._risk_tolerance < self.RISK_THRESHOLD_LOW
            ):
                decision = "defect"
            else:
                decision = "cooperate"

        # 記錄決策歷史
        self._decision_history.append(
            {
                "step": self._step_count,
                "recipient_id": recipient_id,
                "decision": decision,
                "context": context_info,
                "mood": self._mood,
                "risk_tolerance": self._risk_tolerance,
            }
        )

        return decision

    def _update_mood(self, context_info: Dict[str, Any]):
        """根據當前情境更新情緒狀態"""
        # 如果收益較低，情緒可能變差
        my_payoff = context_info.get("my_payoff", 0.0)

        # 簡單的情緒更新規則：
        # - 如果收益低於平均值，情緒可能變差
        # - 如果收益高於平均值，情緒可能變好
        # - 情緒會逐漸迴歸到中性（0.0）

        # 情緒衰減（逐漸迴歸中性）
        self._mood *= self.MOOD_DECAY_RATE

        # 根據收益調整情緒（假設平均收益約為 0）
        if my_payoff < self.PAYOFF_THRESHOLD_LOW:
            self._mood -= self.MOOD_ADJUSTMENT_STEP
        elif my_payoff > self.PAYOFF_THRESHOLD_HIGH:
            self._mood += self.MOOD_ADJUSTMENT_STEP

        # 限制情緒範圍
        self._mood = max(-1.0, min(1.0, self._mood))

    def _get_mood_description(self) -> str:
        """獲取情緒的文字描述"""
        if self._mood > 0.5:
            return "very positive and optimistic"
        elif self._mood > 0.2:
            return "positive and optimistic"
        elif self._mood > -0.2:
            return "neutral, emotionally stable"
        elif self._mood > -0.5:
            return "negative and pessimistic"
        else:
            return "very negative and pessimistic"

    def _get_risk_description(self) -> str:
        """獲取風險偏好的文字描述"""
        if self._risk_tolerance > 0.7:
            return "high risk preference, willing to take risks"
        elif self._risk_tolerance > 0.4:
            return "moderate risk preference"
        else:
            return "low risk preference, tending to be conservative"

    async def _execute_donation(self, recipient_id: int, decision: str) -> str:
        """執行捐贈決策"""
        ctx = {
            "donor_id": self.id,
            "recipient_id": recipient_id,
            "action": decision,
        }
        message = (
            f"Execute donation: I am the donor with ID={self.id}, "
            f"the recipient is Agent {recipient_id}, "
            f"my decision is {decision}. "
            f"Use donor_id={self.id} and recipient_id={recipient_id} exactly."
        )

        ctx2, ans, _ = await self.ask_env(ctx, message, readonly=False)

        # 儲存記憶
        if self._memory:
            try:
                action_text = "cooperated" if decision == "cooperate" else "defected"
                await self._memory.add(
                    f"At step {self._step_count}, I {action_text} with Agent {recipient_id}. "
                    f"Result: {ans}",
                    user_id=self._memory_user_id,
                )
            except Exception:
                # 記憶儲存失敗不影響主流程，靜默處理
                pass

        return ans

    async def _learn_from_top_agents(self):
        """從頂尖智慧體學習"""
        # 查詢頂尖智慧體
        ctx, ans, _ = await self.ask_env(
            {"top_k": self.TOP_AGENTS_COUNT},
            f"Query the summary of top {self.TOP_AGENTS_COUNT} agents by payoff, including their reputation and recent actions. Use top_k={self.TOP_AGENTS_COUNT}.",
            readonly=True,
        )

        top_agents = ctx.get("top_agents", [])
        if not top_agents:
            return

        # LLM 分析成功模式
        analysis_prompt = f"""Analyze the common characteristics of the following top-performing agents:

{ans}

Please summarize their success patterns and provide suggestions: How should I adjust my strategy?
"""

        try:
            analysis_response = await self.acompletion(
                [{"role": "user", "content": analysis_prompt}], stream=False
            )

            # 安全地提取分析內容
            if (
                analysis_response
                and analysis_response.choices
                and analysis_response.choices[0].message
            ):
                analysis = analysis_response.choices[0].message.content or ""
            else:
                analysis = "Failed to analyze top agents' patterns."

            # 儲存學習結果到記憶
            if self._memory:
                try:
                    await self._memory.add(
                        f"Learned success patterns: {analysis}",
                        user_id=self._memory_user_id,
                    )
                except Exception:
                    pass
        except Exception:
            # 學習失敗不影響主流程，靜默處理
            pass

    async def ask(self, message: str, readonly: bool = True) -> str:
        """Answer a question using LLM and optionally memory.

        Args:
            message: The question to answer
            readonly: Whether this is a readonly operation (default: True)

        Returns:
            The answer as a string
        """
        # Try to get relevant memories if available
        memory_context = ""
        if self._memory:
            try:
                results = await self._memory.search(
                    message, user_id=self._memory_user_id, limit=3
                )
                if results and "results" in results:
                    memory_items = []
                    for result in results["results"][:3]:
                        memory_items.append(f"- {result.get('memory', '')}")
                    if memory_items:
                        memory_context = "\n".join(memory_items)
            except Exception:
                pass

        # Build prompt with memory context
        if memory_context:
            prompt = f"""You are an LLM Donor Agent participating in a reputation game.

Relevant memories:
{memory_context}

Question: {message}

Please answer the question based on your knowledge and memories."""
        else:
            prompt = f"""You are an LLM Donor Agent participating in a reputation game.

Question: {message}

Please answer the question."""

        try:
            response = await self.acompletion(
                [{"role": "user", "content": prompt}], stream=False
            )

            # 安全地提取響應內容
            if (
                response
                and response.choices
                and response.choices[0].message
                and response.choices[0].message.content
            ):
                return str(response.choices[0].message.content)
            else:
                return "I'm sorry, I couldn't generate a response."
        except Exception as e:
            return f"I encountered an error while processing your question: {str(e)}"

    async def dump(self) -> dict:
        """序列化狀態"""
        profile_data = self._profile
        if hasattr(self._profile, "to_dict"):
            profile_data = self._profile.to_dict()
        elif not isinstance(profile_data, dict):
            profile_data = {"raw": str(profile_data)}
        return {
            "profile": profile_data,
            "decision_history": self._decision_history[-self.DECISION_HISTORY_LIMIT :],
            "step_count": self._step_count,
            "personality": self._personality,
            "mood": self._mood,
            "risk_tolerance": self._risk_tolerance,
        }

    async def load(self, dump_data: dict):
        """反序列化狀態"""
        await super().load(dump_data)
        self._decision_history = dump_data.get("decision_history", [])
        self._step_count = dump_data.get("step_count", 0)
        self._personality = dump_data.get(
            "personality", self._generate_random_personality()
        )
        self._mood = dump_data.get("mood", 0.0)
        self._risk_tolerance = dump_data.get("risk_tolerance", 0.5)

    async def close(self):
        """清理資源"""
        return None
