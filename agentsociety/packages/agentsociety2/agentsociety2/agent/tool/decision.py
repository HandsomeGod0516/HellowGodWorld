"""工具決策模型。

定義 LLM 輸出的工具決策結構。

.. important::
   這裡不對 ``tool_name`` 做 ``Literal[...]`` 級別的強校驗：LLM 偶發的拼寫/變形會觸發
   Pydantic ValidationError，進而引發重試，浪費 token。

   - **結構校驗**：交給 Pydantic（欄位存在、型別正確、extra forbid）
   - **語義校驗**：在執行時執行（PersonAgent 工具迴圈）並返回可恢復的錯誤物件
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


VALID_TOOL_NAMES = (
    "activate_skill",
    "read_skill",
    "execute_skill",
    "workspace_read",
    "workspace_write",
    "workspace_list",
    "enable_skill",
    "disable_skill",
    "bash",
    "glob",
    "grep",
    "codegen",
    "batch",
    "done",
)


class ToolDecision(BaseModel):
    """單輪工具決策輸出模型。

    由 LLM 生成並透過 Pydantic 校驗，作為工具迴圈的唯一執行輸入。

    :ivar tool_name: 工具名稱，必須是有效工具之一。
    :ivar arguments: 工具引數字典。
    :ivar done: 是否結束當前模擬步。
    :ivar summary: 執行摘要。
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(
        description=(
            "Exactly one of: activate_skill, read_skill, execute_skill, workspace_read, workspace_write, "
            "workspace_list, enable_skill, disable_skill, bash, glob, grep, codegen, batch, done. "
            "activate_skill with arguments.skill_name set to the skill name."
        )
    )
    arguments: dict[str, Any] = Field(default_factory=dict)
    done: bool = Field(
        default=False,
        description="Set true when this simulation step should end after the current tool runs.",
    )
    summary: str = ""
