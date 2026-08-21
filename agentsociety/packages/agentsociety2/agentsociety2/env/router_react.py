"""
ReAct Router Implementation
使用ReAct模式（Reasoning + Acting）直接透過function calling選擇並呼叫所有模組的所有函式
"""

import json
from typing import Tuple, Dict, Any, List

import json_repair
from litellm import AllMessageValues
from openai.types.chat import ChatCompletionToolParam

from agentsociety2.logger import get_logger
from agentsociety2.env.base import EnvBase
from agentsociety2.env.router_base import RouterBase

__all__ = ["ReActRouter"]


class ReActRouter(RouterBase):
    """
    ReAct模式Router：透過Reasoning-Acting迴圈，使用function calling直接呼叫所有模組的所有函式。

    工作流程：
    1. 收集所有環境模組的工具資訊
    2. 使用LLM的function calling能力選擇並呼叫工具
    3. 執行工具呼叫，獲取結果
    4. 將結果反饋給LLM，繼續下一輪Reasoning-Acting迴圈
    5. 直到LLM判斷任務完成或達到最大步數
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

        # 預收集所有工具（包括readonly和非readonly）
        self._all_tools: List[ChatCompletionToolParam] = []
        self._all_readonly_tools: List[ChatCompletionToolParam] = []
        self._tool_name_to_module: Dict[str, EnvBase] = {}
        self._tool_name_to_tool_obj: Dict[str, Any] = {}

        self._collect_all_tools()

        # 建立set_status工具的schema
        self._set_status_tool_schema = self._create_set_status_tool_schema()

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

    def _create_set_status_tool_schema(self) -> ChatCompletionToolParam:
        """建立set_status工具的schema"""
        return {
            "type": "function",
            "function": {
                "name": "set_status",
                "description": "Set the execution status for the current task. Call this when you have completed the task or determined the outcome.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["success", "in_progress", "fail", "error"],
                            "description": "The execution status: 'success' (task completed successfully), 'in_progress' (task still executing, more steps needed), 'fail' (task cannot be completed), 'error' (error occurred during execution)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Optional reason or explanation for the status (especially for 'fail' or 'error' status)",
                        },
                    },
                    "required": ["status"],
                },
            },
        }

    async def ask(
        self,
        ctx: dict,
        instruction: str,
        readonly: bool = False,
        template_mode: bool = False,
    ) -> Tuple[dict, str]:
        """
        使用ReAct模式處理指令。

        Args:
            ctx: 上下文字典
            instruction: 指令字串
            readonly: 是否只讀模式
            template_mode: 模板模式（ReActRouter 不使用，僅為簽名相容）

        Returns:
            (ctx, answer) 元組
        """
        # 新增當前時間資訊到 ctx，以便工具呼叫可以訪問
        self._add_current_time_to_ctx(ctx)

        get_logger().info(
            f"ReActRouter: Processing instruction: {instruction}, readonly: {readonly}"
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

        # 構建初始對話，包含ctx和instruction
        initial_prompt = self._build_initial_prompt(instruction, ctx, readonly)
        dialog: List[AllMessageValues] = [{"role": "user", "content": initial_prompt}]

        # 新增set_status工具到可用工具列表
        tools_with_status = available_tools + [self._set_status_tool_schema]

        # ReAct迴圈
        step_count = 0
        results = {}
        execution_log: List[Dict[str, Any]] = []  # 記錄執行歷史
        # status 表示使用者的指令在環境模組中是否被有效地完成了，還是需要等待一段時間後由使用者主動檢測指令的完成性
        status = "success"
        error: str | None = None

        while step_count < self.max_steps:
            step_count += 1
            get_logger().debug(f"ReActRouter: Step {step_count}/{self.max_steps}")

            # 呼叫LLM，允許function calling
            try:
                # 只在第一步提供tools，後續步驟透過對話歷史傳遞工具呼叫資訊
                tools_for_call = tools_with_status if step_count == 1 else None
                call_kwargs = {
                    "model": "coder",
                    "messages": dialog,
                }
                # 只有在提供tools時才設定tool_choice
                if tools_for_call:
                    call_kwargs["tools"] = tools_for_call
                    call_kwargs["tool_choice"] = "auto"

                response = await self.acompletion_with_system_prompt(**call_kwargs)
            except Exception as e:
                get_logger().error(f"ReActRouter: LLM call failed: {str(e)}")
                status = "error"
                error_msg = str(e)
                results["status"] = status
                results["error"] = error_msg
                # 構建過程文字
                process_text = (
                    json.dumps(execution_log, indent=2, default=str)
                    if execution_log
                    else ""
                )
                # 使用基類的generate_final_answer生成最終答案
                final_answer, determined_status = await self.generate_final_answer(
                    ctx, instruction, results, process_text, status, error_msg
                )
                results["status"] = determined_status
                return results, final_answer

            # 檢查是否有tool calls
            message = response.choices[0].message  # type: ignore
            tool_calls = getattr(message, "tool_calls", None) or []
            assistant_content = message.content or ""

            # 記錄assistant的響應
            execution_log.append(
                {
                    "step": step_count,
                    "type": "assistant_response",
                    "content": assistant_content,
                    "tool_calls_count": len(tool_calls),
                }
            )

            # 新增assistant的響應到對話
            dialog.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": (
                        [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ]
                        if tool_calls
                        else None
                    ),
                }
            )

            # 如果沒有tool calls，說明LLM認為任務完成
            if not tool_calls:
                get_logger().info(
                    f"ReActRouter: Task completed after {step_count} steps"
                )
                # 構建過程文字
                process_text = (
                    json.dumps(execution_log, indent=2, default=str)
                    if execution_log
                    else ""
                )
                # 使用基類的generate_final_answer生成最終答案
                final_answer, determined_status = await self.generate_final_answer(
                    ctx, instruction, results, process_text, status, error
                )
                results["status"] = determined_status
                if error:
                    results["error"] = error
                return results, final_answer

            # 執行所有tool calls
            tool_results = []
            step_tool_calls = []
            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments

                try:
                    func_args = json_repair.loads(func_args_str)
                except Exception as e:
                    get_logger().error(
                        f"ReActRouter: Failed to parse tool arguments: {e}"
                    )
                    error_result = {"error": f"Invalid JSON arguments: {str(e)}"}
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": json.dumps(error_result),
                        }
                    )
                    step_tool_calls.append(
                        {
                            "tool": func_name,
                            "arguments": func_args_str,
                            "result": error_result,
                            "success": False,
                        }
                    )
                    continue

                # 處理set_status工具呼叫
                if func_name == "set_status":
                    status = func_args.get("status", "unknown")
                    reason = func_args.get("reason", "")
                    results["status"] = status
                    if reason:
                        results["reason"] = reason
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": json.dumps(
                                {"status": status, "message": "Status set successfully"}
                            ),
                        }
                    )
                    step_tool_calls.append(
                        {
                            "tool": func_name,
                            "arguments": func_args,
                            "result": {"status": status},
                            "success": True,
                        }
                    )
                    get_logger().info(f"ReActRouter: Status set to {status}")
                    continue

                # 執行工具呼叫
                try:
                    result = await self._execute_tool(func_name, func_args, readonly)
                    result_str = json.dumps(result, default=str)
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": result_str,
                        }
                    )
                    results[func_name] = result
                    step_tool_calls.append(
                        {
                            "tool": func_name,
                            "arguments": func_args,
                            "result": result,
                            "success": True,
                        }
                    )
                    get_logger().debug(
                        f"ReActRouter: Executed tool {func_name}, result: {result_str[:200]}"
                    )
                except Exception as e:
                    error_msg = f"Error executing {func_name}: {str(e)}"
                    get_logger().error(f"ReActRouter: {error_msg}")
                    error_result = {"error": error_msg}
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": json.dumps(error_result),
                        }
                    )
                    step_tool_calls.append(
                        {
                            "tool": func_name,
                            "arguments": func_args,
                            "result": error_result,
                            "success": False,
                        }
                    )
                    # 如果工具執行失敗，標記為 fail
                    status = "fail"
                    error = error_msg

            # 記錄工具呼叫結果
            execution_log.append(
                {
                    "step": step_count,
                    "type": "tool_execution",
                    "tool_calls": step_tool_calls,
                }
            )

            # 將工具執行結果新增到對話
            dialog.extend(tool_results)

        # 達到最大步數
        get_logger().warning(f"ReActRouter: Reached max steps ({self.max_steps})")
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

    def _build_initial_prompt(self, instruction: str, ctx: dict, readonly: bool) -> str:
        """構建初始prompt，包含ctx和instruction"""
        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        prompt = f"""You are an AI assistant helping an agent accomplish tasks in a virtual world simulation environment.

## Agent Input

### Instruction

The instruction is the task that the agent needs to accomplish:

<instruction>{instruction}</instruction>

### Context

The context is a Python dictionary containing the agent input data. You can access values from context when calling tools:

```python
ctx = {context_repr}
```

## Available Tools

You have access to various environment tools. Use function calling to interact with them.

## Instructions

1. **Think step by step** about how to accomplish the task
2. **Use tools** as needed to gather information or perform actions
3. **Call set_status** when you have completed the task or determined the outcome:
   - Use "success" when the task is completed successfully
   - Use "in_progress" when the task is still executing and more steps are needed
   - Use "fail" when the task cannot be completed (e.g., unsupported instruction, missing data)
   - Use "error" when an error occurred during execution
4. **Continue the conversation** until the task is complete or you determine it cannot be completed{readonly_note}

## Status Meanings

- **success**: The task has been completed successfully. All required operations finished without errors.
- **in_progress**: The task is still being executed or more steps are needed. The agent need to check whether it is done in the next steps.
- **fail**: The task could not be completed (e.g., unsupported instruction, missing data, invalid input). Include detailed reason.
- **error**: An error occurred during code execution. Must include error details.

Let's start!"""

        return prompt
