"""工具决策模型。

定义 LLM 输出的工具决策结构。

.. important::
   这里不对 ``tool_name`` 做 ``Literal[...]`` 级别的强校验：LLM 偶发的拼写/变形会触发
   Pydantic ValidationError，进而引发重试，浪费 token。

   - **结构校验**：交给 Pydantic（字段存在、类型正确、extra forbid）
   - **语义校验**：在运行时执行（PersonAgent 工具循环）并返回可恢复的错误对象
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
    """单轮工具决策输出模型。

    由 LLM 生成并通过 Pydantic 校验，作为工具循环的唯一执行输入。

    :ivar tool_name: 工具名称，必须是有效工具之一。
    :ivar arguments: 工具参数字典。
    :ivar done: 是否结束当前仿真步。
    :ivar summary: 执行摘要。
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
