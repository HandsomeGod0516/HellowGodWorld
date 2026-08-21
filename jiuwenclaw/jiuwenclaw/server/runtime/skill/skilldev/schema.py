# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDev 模組的核心資料模型.

所有跨模組共享的資料結構定義在此，包括：
- 流程階段列舉（SkillDevStage）
- 任務狀態（SkillDevState）
- 事件型別（SkillDevEventType）及事件體（SkillDevEvent）
- 掛起點配置（SuspensionConfig / SUSPENSION_POINTS）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 階段列舉
# ---------------------------------------------------------------------------


class SkillDevStage(str, Enum):
    """SkillDev Pipeline 的所有階段.

    流程：INIT → PLAN → PLAN_CONFIRM(掛起) → GENERATE → VALIDATE
        → TEST_DESIGN → TEST_RUN → EVALUATE → REVIEW(掛起)
        → IMPROVE → (回到 TEST_RUN 迭代)
        → PACKAGE → DESC_OPTIMIZE_CONFIRM(掛起) → DESC_OPTIMIZE → COMPLETED
    """

    # 主流程
    INIT = "init"
    PLAN = "plan"
    PLAN_CONFIRM = "plan_confirm"  # 掛起點：等待使用者確認 plan
    GENERATE = "generate"
    VALIDATE = "validate"  # 校驗生成的 SKILL.md 格式（YAML frontmatter + 命名規範）
    TEST_DESIGN = "test_design"
    TEST_RUN = "test_run"
    EVALUATE = "evaluate"  # grader 評分 + aggregate_benchmark 聚合 + analyst 分析
    REVIEW = "review"  # 掛起點：等待使用者審閱評測結果
    IMPROVE = "improve"

    # 打包與描述最佳化
    PACKAGE = "package"
    DESC_OPTIMIZE_CONFIRM = "desc_optimize_confirm"  # 掛起點：詢問使用者是否需要描述最佳化
    DESC_OPTIMIZE = "desc_optimize"  # 觸發描述最佳化迴圈

    # 終態
    COMPLETED = "completed"

    # 異常
    ERROR = "error"


class SkillDevTaskMode(str, Enum):
    """任務入口模式（由請求引數自動判斷）."""

    CREATE = "create"  # 純 query 建立
    CREATE_WITH_RESOURCES = "create_with_resources"  # 攜帶資源包建立
    MODIFY = "modify"  # 修改/升級已有 skill


# ---------------------------------------------------------------------------
# 事件型別
# ---------------------------------------------------------------------------


class SkillDevEventType(str, Enum):
    """Pipeline 向前端推送的事件型別.

    設計原則：後端推的每個事件，前端都應能直接對映到一個 UI 動作，
    而非讓前端猜測語義。
    """

    # --- 流程控制 ---
    STAGE_CHANGED = "skilldev.stage_changed"  # 階段切換（內部標識）
    PROGRESS = "skilldev.progress"  # 階段內進度文字（對話流展示）
    ERROR = "skilldev.error"  # 不可恢復錯誤

    # --- 對話流互動 ---
    AGENT_THINKING = "skilldev.agent_thinking"  # Agent 推理流（delta + model_name + elapsed_ms + status）
    TEST_PROGRESS = "skilldev.test_progress"  # 測試執行進度

    # --- 結構化 UI 驅動 ---
    CONFIRM_REQUEST = "skilldev.confirm_request"  # 掛起點：驅動前端彈出確認框
    TODOS_UPDATE = "skilldev.todos_update"  # 驅動右側 Todo 列表
    ARTIFACT_READY = "skilldev.artifact_ready"  # 驅動右側產物/附件列表

    # --- 資料載體（對話流中展示詳情） ---
    EVAL_READY = "skilldev.eval_ready"  # 評測結果（benchmark JSON）
    VALIDATE_RESULT = "skilldev.validate_result"  # SKILL.md 校驗結果
    DESC_OPT_READY = "skilldev.desc_opt_ready"  # 描述最佳化 before/after


@dataclass
class SkillDevEvent:
    """Pipeline 內部事件，最終被序列化為 AgentResponseChunk 推送給前端."""

    event_type: SkillDevEventType
    payload: dict[str, Any]
    task_id: str = ""


# ---------------------------------------------------------------------------
# 執行時狀態（Source of Truth，駐記憶體）
# ---------------------------------------------------------------------------


@dataclass
class SkillDevState:
    """Pipeline 執行時狀態，在請求執行期間駐記憶體，在階段邊界透過 StateStore checkpoint."""

    task_id: str
    stage: SkillDevStage = SkillDevStage.INIT
    mode: SkillDevTaskMode = SkillDevTaskMode.CREATE
    iteration: int = 0  # 當前改進輪次（從 0 開始）

    # 輸入
    input: dict[str, Any] = field(default_factory=dict)

    # 中間產物
    reference_texts: list[str] = field(default_factory=list)  # 資原始檔解析後的文字
    existing_skill_md: str | None = None  # 已有 SKILL.md 內容
    plan: dict[str, Any] | None = None  # PLAN 階段產出
    plan_confirmed_at: str | None = None
    evals: dict[str, Any] | None = None  # TEST_DESIGN 階段產出
    eval_results: dict[str, Any] | None = None  # EVALUATE 階段產出
    feedback_history: list[dict] = field(default_factory=list)  # 每輪改進的使用者反饋

    # 描述最佳化
    desc_optimize_result: dict[str, Any] | None = (
        None  # run_loop 輸出（best_description, history）
    )

    # 輸出
    zip_path: str | None = None
    zip_size: int = 0

    # 後設資料
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    error: str | None = None

    def touch(self) -> None:
        """更新 updated_at 時間戳."""
        self.updated_at = _now_iso()

    def to_checkpoint_dict(self) -> dict:
        """序列化為可持久化的字典（用於 StateStore）."""
        return {
            "task_id": self.task_id,
            "stage": self.stage.value,
            "mode": self.mode.value,
            "iteration": self.iteration,
            "input": self.input,
            "reference_texts": self.reference_texts,
            "existing_skill_md": self.existing_skill_md,
            "plan": self.plan,
            "plan_confirmed_at": self.plan_confirmed_at,
            "evals": self.evals,
            "eval_results": self.eval_results,
            "feedback_history": self.feedback_history,
            "desc_optimize_result": self.desc_optimize_result,
            "zip_path": self.zip_path,
            "zip_size": self.zip_size,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @classmethod
    def from_checkpoint_dict(cls, data: dict) -> "SkillDevState":
        """從持久化字典恢復狀態."""
        state = cls(task_id=data["task_id"])
        state.stage = SkillDevStage(data["stage"])
        state.mode = SkillDevTaskMode(data.get("mode", "create"))
        state.iteration = data.get("iteration", 0)
        state.input = data.get("input", {})
        state.reference_texts = data.get("reference_texts", [])
        state.existing_skill_md = data.get("existing_skill_md")
        state.plan = data.get("plan")
        state.plan_confirmed_at = data.get("plan_confirmed_at")
        state.evals = data.get("evals")
        state.eval_results = data.get("eval_results")
        state.feedback_history = data.get("feedback_history", [])
        state.desc_optimize_result = data.get("desc_optimize_result")
        state.zip_path = data.get("zip_path")
        state.zip_size = data.get("zip_size", 0)
        state.created_at = data.get("created_at", _now_iso())
        state.updated_at = data.get("updated_at", _now_iso())
        state.error = data.get("error")
        return state

    def to_status_dict(self) -> dict:
        """序列化為前端可展示的狀態摘要."""
        return {
            "task_id": self.task_id,
            "stage": self.stage.value,
            "mode": self.mode.value,
            "iteration": self.iteration,
            "plan": self.plan,
            "eval_results": self.eval_results,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 掛起點配置
# ---------------------------------------------------------------------------


@dataclass
class SuspensionConfig:
    """掛起點的宣告式配置.

    Pipeline 到達掛起點時：
    1. 推送 CONFIRM_REQUEST 事件（前端據此彈出確認框）
    2. Checkpoint 當前狀態並暫停

    恢復時（前端透過 skilldev.respond 統一入口）：
    1. 呼叫 on_resume 更新狀態
    2. 跳轉到 next_stage
    """

    confirm_type: str  # 標識確認型別（前端用於區分彈框樣式）
    title: str  # 彈框標題
    message: str  # 彈框描述文字
    actions: list[
        dict[str, str]
    ]  # 按鈕列表 [{"id": "confirm", "label": "確認", "style": "primary"}]
    extract_data: Callable  # (state) → dict，從 state 提取展示給前端的資料
    on_resume: Callable  # (state, data) → None，根據使用者響應更新 state
    next_stage: SkillDevStage | Callable  # 下一階段（可以是函式，根據 data 動態決定）


# ---------------------------------------------------------------------------
# 各掛起點的 extract_data / on_resume / next_stage 實現
# ---------------------------------------------------------------------------


def _plan_extract_data(state: SkillDevState) -> dict:
    return {"plan": state.plan}


def _plan_confirm_on_resume(state: SkillDevState, data: dict) -> None:
    if "plan" in data:
        state.plan = data["plan"]
    state.plan_confirmed_at = _now_iso()


def _review_extract_data(state: SkillDevState) -> dict:
    return {
        "benchmark": (state.eval_results or {}).get("benchmark"),
        "report": (state.eval_results or {}).get("report"),
        "iteration": state.iteration,
    }


def _review_on_resume(state: SkillDevState, data: dict) -> None:
    if data.get("feedback"):
        state.feedback_history.append(
            {
                "iteration": state.iteration,
                "feedback": data["feedback"],
            }
        )


def _review_next_stage(data: dict) -> SkillDevStage:
    action = data.get("action", "improve")
    return SkillDevStage.IMPROVE if action == "improve" else SkillDevStage.PACKAGE


def _desc_opt_extract_data(state: SkillDevState) -> dict:
    plan = state.plan or {}
    return {"current_description": plan.get("description", "")}


def _desc_optimize_confirm_on_resume(state: SkillDevState, data: dict) -> None:
    pass


def _desc_optimize_confirm_next_stage(data: dict) -> SkillDevStage:
    action = data.get("action", "skip")
    return (
        SkillDevStage.DESC_OPTIMIZE if action == "optimize" else SkillDevStage.COMPLETED
    )


SUSPENSION_POINTS: dict[SkillDevStage, SuspensionConfig] = {
    SkillDevStage.PLAN_CONFIRM: SuspensionConfig(
        confirm_type="plan_confirm",
        title="請審閱開發計劃",
        message="以下是生成的開發計劃，請確認或修改",
        actions=[
            {"id": "confirm", "label": "確認", "style": "primary"},
            {"id": "modify", "label": "修改", "style": "secondary"},
        ],
        extract_data=_plan_extract_data,
        on_resume=_plan_confirm_on_resume,
        next_stage=SkillDevStage.GENERATE,
    ),
    SkillDevStage.REVIEW: SuspensionConfig(
        confirm_type="review",
        title="評測結果審閱",
        message="請審閱評測結果並決定下一步",
        actions=[
            {"id": "accept", "label": "透過，進入打包", "style": "primary"},
            {"id": "improve", "label": "繼續改進", "style": "secondary"},
        ],
        extract_data=_review_extract_data,
        on_resume=_review_on_resume,
        next_stage=_review_next_stage,
    ),
    SkillDevStage.DESC_OPTIMIZE_CONFIRM: SuspensionConfig(
        confirm_type="desc_optimize_confirm",
        title="描述最佳化",
        message="Skill 已打包完成。是否需要最佳化觸發描述以提高觸發準確率？",
        actions=[
            {"id": "optimize", "label": "最佳化", "style": "primary"},
            {"id": "skip", "label": "跳過", "style": "secondary"},
        ],
        extract_data=_desc_opt_extract_data,
        on_resume=_desc_optimize_confirm_on_resume,
        next_stage=_desc_optimize_confirm_next_stage,
    ),
}


# ---------------------------------------------------------------------------
# 評測相關資料結構（對齊官方 skill-creator 的 JSON schema）
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    """單個測試用例."""

    id: int
    prompt: str  # 模擬真實使用者輸入
    expected_output: str = ""  # 預期結果的人可讀描述
    files: list[str] = field(default_factory=list)  # 輸入檔案路徑
    expectations: list[str] = field(default_factory=list)  # 可客觀驗證的宣告

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "expected_output": self.expected_output,
            "files": self.files,
            "expectations": self.expectations,
        }


@dataclass
class EvalSet:
    """完整的測試集."""

    skill_name: str
    evals: list[EvalCase] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "evals": [e.to_dict() for e in self.evals],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvalSet":
        return cls(
            skill_name=data.get("skill_name", ""),
            evals=[EvalCase(**e) for e in data.get("evals", [])],
        )


@dataclass
class GradingExpectation:
    """單條 assertion 的評分結果."""

    text: str  # assertion 原文
    passed: bool  # 是否透過
    evidence: str = ""  # 具體證據引用


@dataclass
class GradingResult:
    """單次執行的評分結果（grading.json）."""

    expectations: list[GradingExpectation] = field(default_factory=list)
    pass_rate: float = 0.0
    passed_count: int = 0
    failed_count: int = 0

    def to_dict(self) -> dict:
        return {
            "expectations": [
                {"text": e.text, "passed": e.passed, "evidence": e.evidence}
                for e in self.expectations
            ],
            "summary": {
                "passed": self.passed_count,
                "failed": self.failed_count,
                "total": self.passed_count + self.failed_count,
                "pass_rate": self.pass_rate,
            },
        }


@dataclass
class RunTiming:
    """單次執行的耗時資料（timing.json）."""

    total_tokens: int = 0
    duration_ms: int = 0
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "total_duration_seconds": self.total_duration_seconds,
        }


@dataclass
class MetricStats:
    """某指標的統計摘要."""

    mean: float = 0.0
    stddev: float = 0.0
    min: float = 0.0
    max: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mean": self.mean,
            "stddev": self.stddev,
            "min": self.min,
            "max": self.max,
        }


@dataclass
class BenchmarkRun:
    """benchmark.json 中的一條 run 記錄."""

    eval_id: int
    eval_name: str
    configuration: str  # "with_skill" | "baseline"
    run_number: int = 1
    pass_rate: float = 0.0
    time_seconds: float = 0.0
    tokens: int = 0
    expectations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eval_id": self.eval_id,
            "eval_name": self.eval_name,
            "configuration": self.configuration,
            "run_number": self.run_number,
            "result": {
                "pass_rate": self.pass_rate,
                "time_seconds": self.time_seconds,
                "tokens": self.tokens,
            },
            "expectations": self.expectations,
        }


@dataclass
class Benchmark:
    """完整的 benchmark 結果."""

    skill_name: str
    runs: list[BenchmarkRun] = field(default_factory=list)
    run_summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict:
        return {
            "metadata": {"skill_name": self.skill_name, "timestamp": self.timestamp},
            "runs": [r.to_dict() for r in self.runs],
            "run_summary": self.run_summary,
            "notes": self.notes,
        }


@dataclass
class TriggerEvalQuery:
    """描述最佳化階段的單個觸發測試查詢."""

    query: str
    should_trigger: bool

    def to_dict(self) -> dict:
        return {"query": self.query, "should_trigger": self.should_trigger}


@dataclass
class DescOptimizeIteration:
    """描述最佳化的單輪迭代結果."""

    iteration: int
    description: str
    train_passed: int = 0
    train_total: int = 0
    test_passed: int | None = None
    test_total: int | None = None

    def to_dict(self) -> dict:
        d = {
            "iteration": self.iteration,
            "description": self.description,
            "train_passed": self.train_passed,
            "train_total": self.train_total,
        }
        if self.test_passed is not None:
            d["test_passed"] = self.test_passed
            d["test_total"] = self.test_total
        return d


# ---------------------------------------------------------------------------
# 階段展示配置（後端驅動，決定哪些階段對使用者可見、如何分組）
# ---------------------------------------------------------------------------


@dataclass
class _StageGroup:
    """一組後端階段的展示配置."""

    id: str
    label: str
    stages: frozenset[SkillDevStage]
    modes: frozenset[SkillDevTaskMode] | None = None  # None = 所有模式都展示


# 後端定義的階段分組。前端只負責渲染，不決定內容。
# 掛起點（PLAN_CONFIRM / REVIEW / DESC_OPTIMIZE_CONFIRM）歸入其所屬的邏輯階段。
_STAGE_GROUPS: list[_StageGroup] = [
    _StageGroup(
        id="plan",
        label="需求分析與規劃",
        stages=frozenset(
            {SkillDevStage.INIT, SkillDevStage.PLAN, SkillDevStage.PLAN_CONFIRM}
        ),
    ),
    _StageGroup(
        id="generate",
        label="技能生成與校驗",
        stages=frozenset({SkillDevStage.GENERATE, SkillDevStage.VALIDATE}),
    ),
    _StageGroup(
        id="test",
        label="測試與評測",
        stages=frozenset(
            {
                SkillDevStage.TEST_DESIGN,
                SkillDevStage.TEST_RUN,
                SkillDevStage.EVALUATE,
                SkillDevStage.REVIEW,
            }
        ),
    ),
    _StageGroup(
        id="improve",
        label="最佳化改進",
        stages=frozenset({SkillDevStage.IMPROVE}),
    ),
    _StageGroup(
        id="package",
        label="打包",
        stages=frozenset({SkillDevStage.PACKAGE}),
    ),
    _StageGroup(
        id="desc_optimize",
        label="描述最佳化",
        stages=frozenset(
            {SkillDevStage.DESC_OPTIMIZE_CONFIRM, SkillDevStage.DESC_OPTIMIZE}
        ),
    ),
]


def compute_todos(
    current_stage: SkillDevStage,
    mode: SkillDevTaskMode | None = None,
) -> list[dict[str, str]]:
    """根據當前階段和任務模式，計算面向使用者的 Todo 列表.

    後端是步驟定義的唯一權威來源。前端只做渲染。
    """
    groups = _STAGE_GROUPS
    if mode is not None:
        groups = [g for g in groups if g.modes is None or mode in g.modes]

    if current_stage == SkillDevStage.COMPLETED:
        return [{"id": g.id, "label": g.label, "status": "completed"} for g in groups]
    if current_stage == SkillDevStage.ERROR:
        return [{"id": g.id, "label": g.label, "status": "cancelled"} for g in groups]

    found_current = False
    result: list[dict[str, str]] = []
    for g in groups:
        if current_stage in g.stages:
            status = "in_progress"
            found_current = True
        elif found_current:
            status = "pending"
        else:
            status = "completed"
        result.append({"id": g.id, "label": g.label, "status": status})
    return result


# ---------------------------------------------------------------------------
# SKILL.md 校驗相關常量
# ---------------------------------------------------------------------------

ALLOWED_FRONTMATTER_KEYS = frozenset(
    {
        "name",
        "description",
        "license",
        "allowed-tools",
        "metadata",
        "compatibility",
    }
)

SKILL_NAME_MAX_LEN = 64
SKILL_DESC_MAX_LEN = 1024


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """返回當前 UTC 時間的 ISO 8601 字串."""
    import datetime

    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_task_id() -> str:
    """生成唯一 task_id，格式：sd_{timestamp}_{random}."""
    import secrets

    ts = int(time.time())
    rand = secrets.token_hex(4)
    return f"sd_{ts}_{rand}"


def determine_task_mode(params: dict) -> SkillDevTaskMode:
    """根據請求引數自動判斷任務模式."""
    if params.get("existing_skill"):
        return SkillDevTaskMode.MODIFY
    if params.get("resources"):
        return SkillDevTaskMode.CREATE_WITH_RESOURCES
    return SkillDevTaskMode.CREATE
