"""
Plan-and-Execute Router Implementation
使用Plan-and-Execute模式：先制定計劃，然後執行計劃中的步驟
"""

import json
from typing import Tuple, Dict, Any, List

import json_repair
from litellm import AllMessageValues
from openai.types.chat import ChatCompletionToolParam

from agentsociety2.logger import get_logger
from agentsociety2.env.base import EnvBase
from agentsociety2.env.router_base import RouterBase

__all__ = ["PlanExecuteRouter"]


class PlanExecuteRouter(RouterBase):
    """
    Plan-and-Execute模式Router：先制定計劃，然後執行計劃中的步驟。

    工作流程：
    1. 收集所有環境模組的工具資訊
    2. 第一階段：使用LLM制定執行計劃（不呼叫工具）
    3. 第二階段：按照計劃逐步執行工具呼叫
    4. 根據執行結果生成最終答案
    """

    def __init__(
        self,
        env_modules: list[EnvBase],
        max_steps: int = 10,
        max_llm_call_retry: int = 10,
    ):
        super().__init__(
            env_modules=env_modules,
            max_steps=max_steps,
            max_llm_call_retry=max_llm_call_retry,
        )

        # 預收集所有工具
        self._all_tools: List[ChatCompletionToolParam] = []
        self._all_readonly_tools: List[ChatCompletionToolParam] = []
        self._tool_name_to_module: Dict[str, EnvBase] = {}
        self._tool_name_to_tool_obj: Dict[str, Any] = {}

        self._collect_all_tools()

    def _collect_all_tools(self):
        """收集所有模組的所有工具"""
        for module in self.env_modules:
            registered_tools = getattr(module.__class__, "_registered_tools", {})

            for tool_name, tool_obj in registered_tools.items():
                # 獲取工具的LLM格式schema
                tool_schema: ChatCompletionToolParam | None = None
                for llm_tool in module._llm_tools:
                    if llm_tool["function"]["name"] == tool_name:
                        tool_schema = llm_tool
                        break

                if tool_schema:
                    self._all_tools.append(tool_schema)
                    self._tool_name_to_module[tool_name] = module
                    self._tool_name_to_tool_obj[tool_name] = tool_obj

                    # 檢查是否是readonly工具
                    readonly_tools = getattr(module.__class__, "_readonly_tools", {})
                    if readonly_tools.get(tool_name, False):
                        self._all_readonly_tools.append(tool_schema)

    async def ask(
        self,
        ctx: dict,
        instruction: str,
        readonly: bool = False,
        template_mode: bool = False,
    ) -> Tuple[dict, str]:
        """
        使用Plan-and-Execute模式處理指令。

        Args:
            ctx: 上下文字典
            instruction: 指令字串
            readonly: 是否只讀模式
            template_mode: 模板模式（PlanExecuteRouter 不使用，僅為簽名相容）

        Returns:
            (ctx, answer) 元組
        """
        # 新增當前時間資訊到 ctx，以便工具呼叫可以訪問
        self._add_current_time_to_ctx(ctx)

        get_logger().info(
            f"PlanExecuteRouter: Processing instruction: {instruction}, readonly: {readonly}"
        )

        if not self.env_modules:
            get_logger().warning("No environment modules available")
            results = {"status": "fail", "reason": "No environment modules available"}
            return (
                results,
                "No environment modules available to handle the request.",
            )

        # 選擇可用的工具列表
        available_tools = self._all_readonly_tools if readonly else self._all_tools

        if not available_tools:
            results = {
                "status": "fail",
                "reason": "No available tools to handle the request",
            }
            return results, "No available tools to handle the request."

        # 第一階段：制定計劃
        plan = await self._create_plan(instruction, ctx, readonly, available_tools)
        if not plan:
            results = {"status": "fail", "reason": "Failed to create execution plan"}
            return results, "Failed to create execution plan."

        get_logger().info(f"PlanExecuteRouter: Created plan with {len(plan)} steps")

        # 第二階段：執行計劃（支援replan）
        results = {}
        execution_log = []
        status = "unknown"  # 初始狀態，LLM可以在執行過程中設定
        plan_attempts = 0
        max_plan_attempts = 3  # 最多允許3次replan

        while plan_attempts < max_plan_attempts:
            plan_attempts += 1
            get_logger().info(
                f"PlanExecuteRouter: Executing plan attempt {plan_attempts}/{max_plan_attempts}"
            )

            # 執行當前計劃
            plan_completed = False
            for step_idx, step in enumerate(plan):
                if step_idx >= self.max_steps:
                    get_logger().warning(
                        f"PlanExecuteRouter: Reached max steps at step {step_idx}"
                    )
                    break

                get_logger().debug(
                    f"PlanExecuteRouter: Executing step {step_idx + 1}/{len(plan)}: {step.get('description', 'N/A')}"
                )

                # 執行步驟
                step_result = await self._execute_step(
                    step, ctx, readonly, results, execution_log, instruction
                )
                execution_log.append(
                    {
                        "step": step_idx + 1,
                        "plan_attempt": plan_attempts,
                        "description": step.get("description", ""),
                        "result": step_result,
                    }
                )

                # 檢查是否是replan請求或需要replan的錯誤
                should_replan = False
                replan_reason = ""

                if isinstance(step_result, dict):
                    if step_result.get("_replan_requested"):
                        should_replan = True
                        replan_reason = step_result.get("reason", "Replan requested")
                        step_result.pop("_replan_requested", None)
                    elif step_result.get("_error_suggests_replan"):
                        should_replan = True
                        replan_reason = step_result.get(
                            "error", "Recoverable error encountered"
                        )
                        step_result.pop("_error_suggests_replan", None)
                        step_result.pop("_recoverable_error", None)

                if should_replan:
                    get_logger().info(
                        f"PlanExecuteRouter: Replan triggered at step {step_idx + 1}: {replan_reason}"
                    )
                    # 重新制定計劃
                    new_plan = await self._create_replan(
                        instruction,
                        ctx,
                        readonly,
                        available_tools,
                        results,
                        execution_log,
                    )
                    if new_plan:
                        plan = new_plan
                        get_logger().info(
                            f"PlanExecuteRouter: Created new plan with {len(plan)} steps"
                        )
                        # 記錄replan事件
                        execution_log.append(
                            {
                                "step": step_idx + 1,
                                "plan_attempt": plan_attempts,
                                "type": "replan",
                                "description": f"Replanning due to: {replan_reason}",
                                "new_plan_steps": len(plan),
                            }
                        )
                        plan_completed = False
                        break  # 跳出當前計劃執行，開始執行新計劃
                    else:
                        get_logger().warning(
                            "PlanExecuteRouter: Failed to create replan, continuing with current plan"
                        )

                # 更新results
                if isinstance(step_result, dict):
                    results.update(step_result)
                    # 檢查是否設定了status
                    if "status" in step_result:
                        status = step_result["status"]
                        # 如果status是success或fail，計劃完成
                        if status in ["success", "fail", "error"]:
                            plan_completed = True
                            break

            # 如果計劃完成或達到最大嘗試次數，退出迴圈
            if plan_completed or plan_attempts >= max_plan_attempts:
                break

        # 初步檢查是否有明顯的錯誤
        error = None
        for log_entry in execution_log:
            step_result = log_entry.get("result", {})
            if isinstance(step_result, dict) and "error" in step_result:
                error = step_result.get("error")
                break

        # 構建過程文字
        process_text = (
            json.dumps(execution_log, indent=2, default=str) if execution_log else ""
        )
        # 使用基類的generate_final_answer生成最終答案
        final_answer, determined_status = await self.generate_final_answer(
            ctx, instruction, results, process_text, status, error
        )
        results["status"] = determined_status
        if error:
            results["error"] = error

        return results, final_answer

    async def _create_plan(
        self,
        instruction: str,
        ctx: dict,
        readonly: bool,
        available_tools: List[ChatCompletionToolParam],
    ) -> List[Dict[str, Any]]:
        """建立執行計劃"""
        # 構建工具列表描述
        tools_description = self._format_tools_description(available_tools)

        # 使用改進的prompt構建方法
        base_prompt = self._build_plan_prompt_with_status(instruction, ctx, readonly)

        prompt = f"""{base_prompt}

## Available Tools
{tools_description}

## Instructions
Create a detailed execution plan with the following format:
- Each step should specify: tool name, arguments (as a JSON object), and a brief description
- Steps should be ordered logically
- You can reference results from previous steps using step numbers
- The plan should be complete enough to accomplish the task
- Include a final step with tool="set_status" to set the execution status
- If you encounter issues during execution (e.g., missing parameters, invalid inputs), you can use tool="replan" to request a new plan based on current results

## Output Format
Return a JSON array of steps, where each step is:
{{
    "step": <step_number>,
    "tool": "<tool_name>",
    "arguments": {{<tool_arguments_as_json>}},
    "description": "<brief_description>"
}}

Example:
[
    {{
        "step": 1,
        "tool": "get_user_info",
        "arguments": {{"user_id": 123}},
        "description": "Get user information"
    }},
    {{
        "step": 2,
        "tool": "update_user_status",
        "arguments": {{"user_id": 123, "status": "active"}},
        "description": "Update user status based on step 1 results"
    }},
    {{
        "step": 3,
        "tool": "set_status",
        "arguments": {{"status": "success", "reason": "Task completed successfully"}},
        "description": "Set final execution status"
    }}
]

Note: If during execution you encounter issues like missing parameters or invalid inputs, you can add a step with tool="replan" and arguments={{"reason": "explanation of why replan is needed"}} to request a new plan.

Your plan:"""

        dialog: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self.acompletion_with_system_prompt(
                model="coder",
                messages=dialog,
            )

            plan_text = response.choices[0].message.content or "[]"  # type: ignore

            # 嘗試從響應中提取JSON
            plan = self._extract_json_from_text(plan_text)

            if not isinstance(plan, list):
                get_logger().error(f"PlanExecuteRouter: Invalid plan format: {plan}")
                return []

            return plan

        except Exception as e:
            get_logger().error(f"PlanExecuteRouter: Failed to create plan: {str(e)}")
            return []

    async def _create_replan(
        self,
        instruction: str,
        ctx: dict,
        readonly: bool,
        available_tools: List[ChatCompletionToolParam],
        current_results: dict,
        execution_log: list,
    ) -> List[Dict[str, Any]]:
        """建立重新計劃，基於當前執行結果和錯誤資訊"""
        # 構建工具列表描述
        tools_description = self._format_tools_description(available_tools)

        # 構建當前執行狀態摘要
        execution_summary = self._build_execution_summary(
            execution_log, current_results
        )

        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        prompt = f"""You are a planning assistant. Create a NEW execution plan based on the current execution state and encountered issues.

## Agent Input

### Instruction

The instruction is the task that the agent needs to accomplish:

<instruction>{instruction}</instruction>

### Context

The context is a Python dictionary containing the agent input data. You can access values from context when calling tools:

```python
ctx = {context_repr}
```

## Current Execution State

### Execution Summary
{execution_summary}

### Current Results
```python
{json.dumps(current_results, indent=2, default=str)}
```

## Available Tools
{tools_description}

## Instructions

Create a NEW execution plan that:
1. Takes into account the current execution state and results
2. Addresses any issues encountered in previous execution
3. Uses information gathered so far to complete the task
4. Each step should specify: tool name, arguments (as a JSON object), and a brief description
5. Steps should be ordered logically
6. You can reference results from previous execution using the current results
7. Include a final step with tool="set_status" to set the execution status{readonly_note}

## Status Meanings

- **success**: The task has been completed successfully. All required operations finished without errors.
- **in_progress**: The task is still being executed or more steps are needed. The agent need to check whether it is done in the next steps.
- **fail**: The task could not be completed (e.g., unsupported instruction, missing data, invalid input). Include detailed reason.
- **error**: An error occurred during code execution. Must include error details.

## Output Format

Return a JSON array of steps, where each step is:
{{
    "step": <step_number>,
    "tool": "<tool_name>",
    "arguments": {{<tool_arguments_as_json>}},
    "description": "<brief_description>"
}}

Your new plan:"""

        dialog: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self.acompletion_with_system_prompt(
                model="coder",
                messages=dialog,
            )

            plan_text = response.choices[0].message.content or "[]"  # type: ignore

            # 嘗試從響應中提取JSON
            plan = self._extract_json_from_text(plan_text)

            if not isinstance(plan, list):
                get_logger().error(f"PlanExecuteRouter: Invalid replan format: {plan}")
                return []

            return plan

        except Exception as e:
            get_logger().error(f"PlanExecuteRouter: Failed to create replan: {str(e)}")
            return []

    def _build_execution_summary(
        self, execution_log: list, current_results: dict
    ) -> str:
        """構建執行摘要，用於replan"""
        summary_lines = []

        # 統計執行步驟
        completed_steps = [log for log in execution_log if log.get("type") != "replan"]
        summary_lines.append(f"- Total steps executed: {len(completed_steps)}")

        # 檢查錯誤
        errors = []
        for log_entry in execution_log:
            result = log_entry.get("result", {})
            if isinstance(result, dict) and "error" in result:
                errors.append(
                    {
                        "step": log_entry.get("step", "unknown"),
                        "description": log_entry.get("description", ""),
                        "error": result.get("error", ""),
                    }
                )

        if errors:
            summary_lines.append(f"\n- Errors encountered: {len(errors)}")
            for error_info in errors[:3]:  # 只顯示前3個錯誤
                summary_lines.append(
                    f"  Step {error_info['step']}: {error_info.get('description', 'N/A')}"
                )
                summary_lines.append(f"    Error: {error_info['error']}")

        # 檢查成功執行的步驟
        successful_steps = [
            log
            for log in execution_log
            if isinstance(log.get("result"), dict)
            and "error" not in log.get("result", {})
            and log.get("type") != "replan"
        ]
        if successful_steps:
            summary_lines.append(f"\n- Successful steps: {len(successful_steps)}")
            for step in successful_steps[:3]:  # 只顯示前3個成功步驟
                summary_lines.append(
                    f"  Step {step.get('step', 'unknown')}: {step.get('description', 'N/A')}"
                )

        # 檢查是否有status設定
        if "status" in current_results:
            summary_lines.append(
                f"\n- Current status: {current_results.get('status', 'unknown')}"
            )

        return (
            "\n".join(summary_lines)
            if summary_lines
            else "No execution history available."
        )

    def _extract_json_from_text(self, text: str) -> Any:
        """從文字中提取JSON"""
        import re

        # 嘗試找到JSON陣列
        json_match = re.search(r"\[[\s\S]*\]", text)
        if json_match:
            try:
                return json_repair.loads(json_match.group())
            except Exception:
                pass

        # 嘗試直接解析整個文字
        try:
            return json_repair.loads(text)
        except Exception:
            pass

        # 如果都失敗，返回空列表
        return []

    def _format_tools_description(self, tools: List[ChatCompletionToolParam]) -> str:
        """格式化工具描述"""
        descriptions = []
        for tool in tools:
            func_info = tool["function"]
            descriptions.append(
                f"- {func_info['name']}: {func_info.get('description', 'No description')}\n"
                f"  Parameters: {json.dumps(func_info.get('parameters', {}), indent=2)}"
            )
        return "\n".join(descriptions)

    async def _execute_step(
        self,
        step: Dict[str, Any],
        ctx: dict,
        readonly: bool,
        results: dict,
        execution_log: list,
        instruction: str,
    ) -> Any:
        """執行計劃中的一個步驟"""
        tool_name = step.get("tool")
        arguments = step.get("arguments", {})

        if not tool_name:
            return {"error": "Step missing tool name"}

        # 處理set_status工具
        if tool_name == "set_status":
            status = arguments.get("status", "unknown")
            reason = arguments.get("reason", "")
            result = {"status": status}
            if reason:
                result["reason"] = reason
            get_logger().info(f"PlanExecuteRouter: Status set to {status}")
            return result

        # 處理replan工具
        if tool_name == "replan":
            reason = arguments.get("reason", "Execution issues encountered")
            get_logger().info(f"PlanExecuteRouter: Replan requested: {reason}")
            return {"_replan_requested": True, "reason": reason}

        try:
            result = await self._execute_tool(tool_name, arguments, readonly)
            # 檢查結果中是否有錯誤，如果有且是引數錯誤等可恢復的錯誤，標記為可能需要replan
            if isinstance(result, dict) and "error" in result:
                error_msg = result.get("error", "")
                # 檢查是否是引數相關的錯誤（可能需要replan）
                replan_keywords = [
                    "missing",
                    "required",
                    "invalid",
                    "not found",
                    "cannot",
                    "not available",
                    "not exist",
                ]
                if any(keyword in error_msg.lower() for keyword in replan_keywords):
                    get_logger().warning(
                        f"PlanExecuteRouter: Potential replan trigger - error: {error_msg}"
                    )
                    # 標記錯誤為可恢復的，但不在這一步自動觸發replan
                    # 讓計劃執行迴圈檢查是否需要replan
                    result["_recoverable_error"] = "true"  # type: ignore
                    result["_error_suggests_replan"] = "true"  # type: ignore
            return result
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {str(e)}"
            get_logger().error(f"PlanExecuteRouter: {error_msg}")
            # 檢查異常是否是引數相關的
            error_str = str(e).lower()
            replan_keywords = [
                "missing",
                "required",
                "invalid",
                "not found",
                "cannot",
                "not available",
            ]
            result: Dict[str, Any] = {"error": error_msg}
            if any(keyword in error_str for keyword in replan_keywords):
                result["_recoverable_error"] = "true"
                result["_error_suggests_replan"] = "true"
            return result

    async def _execute_tool(self, tool_name: str, args: dict, readonly: bool) -> Any:
        """執行工具呼叫"""
        # 獲取工具所屬的模組
        module = self._tool_name_to_module.get(tool_name)
        if not module:
            raise ValueError(f"Tool {tool_name} not found")

        # 檢查readonly約束
        readonly_tools = getattr(module.__class__, "_readonly_tools", {})
        if readonly and not readonly_tools.get(tool_name, False):
            raise ValueError(
                f"Tool {tool_name} is not readonly, but readonly mode is enabled"
            )

        # 獲取工具物件
        tool_obj = self._tool_name_to_tool_obj.get(tool_name)
        if not tool_obj:
            raise ValueError(f"Tool object for {tool_name} not found")

        # 獲取工具函式
        tool_func = tool_obj.fn
        if not tool_func:
            raise ValueError(f"Tool function for {tool_name} not found")

        # 執行工具函式（可能是async）
        import inspect

        if inspect.iscoroutinefunction(tool_func):
            result = await tool_func(module, **args)
        else:
            result = tool_func(module, **args)

        return result

    def _build_plan_prompt_with_status(
        self, instruction: str, ctx: dict, readonly: bool
    ) -> str:
        """構建包含status設定說明的計劃prompt"""
        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        return f"""You are a planning assistant. Create a step-by-step execution plan to accomplish the given task.

## Agent Input

### Instruction

The instruction is the task that the agent needs to accomplish:

<instruction>{instruction}</instruction>

### Context

The context is a Python dictionary containing the agent input data. You can access values from context when calling tools:

```python
ctx = {context_repr}
```

## Instructions

When creating the plan, you can include a special step to set the execution status:
- Add a step with tool="set_status" and arguments={{"status": "success|in_progress|fail|error", "reason": "optional reason"}}
- This should typically be the last step in your plan
- Use "success" when the task will be completed successfully
- Use "in_progress" when the task requires more steps
- Use "fail" when the task cannot be completed
- Use "error" when an error is expected{readonly_note}

## Status Meanings

- **success**: The task has been completed successfully. All required operations finished without errors.
- **in_progress**: The task is still being executed or more steps are needed. The agent need to check whether it is done in the next steps.
- **fail**: The task could not be completed (e.g., unsupported instruction, missing data, invalid input). Include detailed reason.
- **error**: An error occurred during code execution. Must include error details."""
