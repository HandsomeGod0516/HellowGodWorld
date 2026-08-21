"""
Two-Tier ReAct Router Implementation
雙層ReAct模式：先選擇Module，然後再呼叫函式
"""

import json
from typing import Tuple, Dict, Any, List

import json_repair
from litellm import AllMessageValues

from agentsociety2.logger import get_logger
from agentsociety2.env.base import EnvBase
from agentsociety2.env.router_base import RouterBase

__all__ = ["TwoTierReActRouter"]


class TwoTierReActRouter(RouterBase):
    """
    雙層ReAct模式Router：先選擇Module，然後再呼叫該模組的函式。

    工作流程：
    1. 第一層：使用LLM選擇合適的環境模組
    2. 第二層：使用ReAct模式呼叫選中模組的工具
    3. 如果需要多個模組，可以迴圈執行
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

        # 預收集模組資訊和工具資訊
        self._module_info: Dict[str, Dict[str, Any]] = {}
        self._module_tools: Dict[str, List[Dict[str, Any]]] = {}
        self._module_readonly_tools: Dict[str, List[Dict[str, Any]]] = {}
        self._tool_name_to_module: Dict[str, EnvBase] = {}
        self._tool_name_to_tool_obj: Dict[str, Any] = {}

        self._collect_module_info()

    def _collect_module_info(self):
        """收集模組資訊和工具資訊"""
        for module in self.env_modules:
            module_name = module.name
            module_description = module.description

            # 收集該模組的所有工具
            all_tools = []
            readonly_tools = []

            registered_tools = getattr(module.__class__, "_registered_tools", {})
            readonly_tools_dict = getattr(module.__class__, "_readonly_tools", {})

            for tool_name, tool_obj in registered_tools.items():
                # 獲取工具的LLM格式schema
                tool_schema = None
                for llm_tool in module._llm_tools:
                    if llm_tool["function"]["name"] == tool_name:
                        tool_schema = llm_tool
                        break

                if tool_schema:
                    all_tools.append(tool_schema)
                    self._tool_name_to_module[tool_name] = module
                    self._tool_name_to_tool_obj[tool_name] = tool_obj

                    if readonly_tools_dict.get(tool_name, False):
                        readonly_tools.append(tool_schema)

            self._module_info[module_name] = {
                "name": module_name,
                "description": module_description,
                "tool_count": len(all_tools),
            }
            self._module_tools[module_name] = all_tools
            self._module_readonly_tools[module_name] = readonly_tools

    async def ask(
        self,
        ctx: dict,
        instruction: str,
        readonly: bool = False,
        template_mode: bool = False,
    ) -> Tuple[dict, str]:
        """
        使用雙層ReAct模式處理指令。

        Args:
            ctx: 上下文字典
            instruction: 指令字串
            readonly: 是否只讀模式
            template_mode: 模板模式（TwoTierReActRouter 不使用，僅為簽名相容）

        Returns:
            (ctx, answer) 元組
        """
        # 新增當前時間資訊到 ctx，以便工具呼叫可以訪問
        self._add_current_time_to_ctx(ctx)

        get_logger().info(
            f"TwoTierReActRouter: Processing instruction: {instruction}, readonly: {readonly}"
        )

        if not self.env_modules:
            get_logger().warning("No environment modules available")
            results = {"status": "fail", "reason": "No environment modules available"}
            return (
                results,
                "No environment modules available to handle the request.",
            )

        results = {}
        step_count = 0
        used_modules = set()
        execution_log: List[Dict[str, Any]] = []  # 記錄執行歷史
        error = None

        while step_count < self.max_steps:
            step_count += 1
            get_logger().debug(
                f"TwoTierReActRouter: Step {step_count}/{self.max_steps}"
            )

            # 第一層：選擇模組
            selected_module = await self._select_module(
                instruction, ctx, used_modules, readonly
            )

            if not selected_module:
                get_logger().info(
                    "TwoTierReActRouter: No more modules to select, task complete"
                )
                break

            used_modules.add(selected_module)
            get_logger().info(f"TwoTierReActRouter: Selected module: {selected_module}")

            # 記錄模組選擇
            execution_log.append(
                {
                    "step": step_count,
                    "type": "module_selection",
                    "module": selected_module,
                }
            )

            # 第二層：使用ReAct模式呼叫該模組的工具
            module_result, module_answer = await self._react_with_module(
                selected_module, instruction, ctx, readonly
            )

            # 記錄模組執行結果
            execution_log.append(
                {
                    "step": step_count,
                    "type": "module_execution",
                    "module": selected_module,
                    "result": module_result,
                    "answer": module_answer,
                }
            )

            # 合併結果
            results.update(module_result)

            # 檢查是否有明顯的錯誤
            if isinstance(module_result, dict):
                for key, value in module_result.items():
                    if isinstance(value, dict) and "error" in value:
                        error = str(value.get("error"))
                        break

            # 檢查是否還需要其他模組
            if await self._needs_more_modules(
                instruction, ctx, results, used_modules, readonly
            ):
                continue
            else:
                # 構建過程文字
                process_text = (
                    json.dumps(execution_log, indent=2, default=str)
                    if execution_log
                    else ""
                )
                # 使用基類的generate_final_answer生成最終答案
                final_answer, determined_status = await self.generate_final_answer(
                    ctx, instruction, results, process_text, "unknown", error
                )
                results["status"] = determined_status
                if error:
                    results["error"] = error
                return results, final_answer

        # 達到最大步數或沒有更多模組
        # 構建過程文字
        process_text = (
            json.dumps(execution_log, indent=2, default=str) if execution_log else ""
        )
        # 使用基類的generate_final_answer生成最終答案
        final_answer, determined_status = await self.generate_final_answer(
            ctx, instruction, results, process_text, "unknown", error
        )
        results["status"] = determined_status
        if error:
            results["error"] = error
        return results, final_answer

    async def _select_module(
        self, instruction: str, ctx: dict, used_modules: set, readonly: bool
    ) -> str | None:
        """第一層：選擇合適的環境模組"""
        # 構建模組選擇工具
        modules_list = [
            {
                "name": name,
                "description": info["description"],
                "tool_count": info["tool_count"],
            }
            for name, info in self._module_info.items()
            if name not in used_modules
        ]

        if not modules_list:
            return None

        modules_description = "\n".join(
            [
                f"- {m['name']}: {m['description']} ({m['tool_count']} tools available)"
                for m in modules_list
            ]
        )

        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        prompt = f"""You need to select the most appropriate environment module to handle the task.

## Agent Input

### Instruction

The instruction is the task that the agent needs to accomplish:

<instruction>{instruction}</instruction>

### Context

The context is a Python dictionary containing the agent input data. You can access values from context when calling tools:

```python
ctx = {context_repr}
```

## Available Modules
{modules_description}

## Instructions
Select ONE module that is most suitable for the current task. Consider:
- What the task requires
- What each module provides
- Which module's tools are most relevant{readonly_note}

## Output Format
Return ONLY the module name (exactly as shown in the list above), nothing else.

Selected module:"""

        dialog: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self.acompletion_with_system_prompt(
                model="coder",
                messages=dialog,
            )

            selected = (response.choices[0].message.content or "").strip()  # type: ignore

            # 驗證選擇的模組是否有效
            if selected in self._module_info and selected not in used_modules:
                return selected
            else:
                get_logger().warning(
                    f"TwoTierReActRouter: Invalid module selection: {selected}"
                )
                # 如果選擇無效，返回第一個未使用的模組
                for module_name in self._module_info.keys():
                    if module_name not in used_modules:
                        return module_name
                return None

        except Exception as e:
            get_logger().error(f"TwoTierReActRouter: Failed to select module: {str(e)}")
            # 返回第一個未使用的模組作為fallback
            for module_name in self._module_info.keys():
                if module_name not in used_modules:
                    return module_name
            return None

    async def _react_with_module(
        self, module_name: str, instruction: str, ctx: dict, readonly: bool
    ) -> Tuple[dict, str]:
        """第二層：使用ReAct模式呼叫選中模組的工具"""
        # 獲取該模組的工具
        available_tools = (
            self._module_readonly_tools.get(module_name, [])
            if readonly
            else self._module_tools.get(module_name, [])
        )

        if not available_tools:
            return {}, f"No available tools in module {module_name}."

        # 新增set_status工具
        set_status_tool = {
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
        tools_with_status = available_tools + [set_status_tool]

        # 構建ReAct對話
        dialog: List[AllMessageValues] = [
            {
                "role": "user",
                "content": self._build_module_react_prompt(
                    module_name, instruction, ctx, readonly
                ),
            }
        ]

        module_results = {}
        react_steps = 0
        max_react_steps = 5  # 每個模組最多5步ReAct迴圈

        while react_steps < max_react_steps:
            react_steps += 1

            # 呼叫LLM
            try:
                # 只在第一步提供tools，後續步驟透過對話歷史傳遞工具呼叫資訊
                tools_for_call = tools_with_status if react_steps == 1 else None
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
                get_logger().error(f"TwoTierReActRouter: LLM call failed: {str(e)}")
                return module_results, f"Error during module execution: {str(e)}"

            # 檢查tool calls
            message = response.choices[0].message  # type: ignore
            tool_calls = getattr(message, "tool_calls", None) or []

            # 新增assistant響應
            dialog.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
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

            # 如果沒有tool calls，說明完成
            if not tool_calls:
                final_answer = (
                    message.content or f"Module {module_name} processing completed."
                )
                return module_results, final_answer

            # 執行tool calls
            tool_results = []
            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments

                try:
                    func_args = json_repair.loads(func_args_str)
                except Exception as e:
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": json.dumps(
                                {"error": f"Invalid JSON arguments: {str(e)}"}
                            ),
                        }
                    )
                    continue

                # 處理set_status工具
                if func_name == "set_status":
                    status = func_args.get("status", "unknown")
                    reason = func_args.get("reason", "")
                    module_results["status"] = status
                    if reason:
                        module_results["reason"] = reason
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
                    get_logger().info(f"TwoTierReActRouter: Status set to {status}")
                else:
                    # 執行工具
                    try:
                        result = await self._execute_tool(
                            func_name, func_args, readonly
                        )
                        result_str = json.dumps(result, default=str)
                        tool_results.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": func_name,
                                "content": result_str,
                            }
                        )
                        module_results[func_name] = result
                    except Exception as e:
                        error_msg = f"Error executing {func_name}: {str(e)}"
                        tool_results.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": func_name,
                                "content": json.dumps({"error": error_msg}),
                            }
                        )

            dialog.extend(tool_results)

        # 達到最大ReAct步數
        return (
            module_results,
            f"Module {module_name} processing incomplete (max steps reached).",
        )

    def _build_module_react_prompt(
        self, module_name: str, instruction: str, ctx: dict, readonly: bool
    ) -> str:
        """構建模組ReAct提示詞"""
        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        return f"""You are working with the {module_name} module to accomplish part of a larger task.

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
1. Use tools from the {module_name} module to accomplish the task.
2. Think step by step and use tools as needed.
3. **Call set_status** when you have completed the task or determined the outcome:
   - Use "success" when the task is completed successfully
   - Use "in_progress" when the task is still executing and more steps are needed
   - Use "fail" when the task cannot be completed (e.g., unsupported instruction, missing data)
   - Use "error" when an error occurred during execution
4. When finished with this module, provide a summary without calling more tools.{readonly_note}

## Status Meanings

- **success**: The task has been completed successfully. All required operations finished without errors.
- **in_progress**: The task is still being executed or more steps are needed. The agent need to check whether it is done in the next steps.
- **fail**: The task could not be completed (e.g., unsupported instruction, missing data, invalid input). Include detailed reason.
- **error**: An error occurred during code execution. Must include error details.

Let's start!"""

    async def _needs_more_modules(
        self,
        instruction: str,
        ctx: dict,
        results: dict,
        used_modules: set,
        readonly: bool,
    ) -> bool:
        """判斷是否還需要更多模組"""
        # 簡單策略：如果還有未使用的模組，詢問LLM是否需要
        unused_modules = set(self._module_info.keys()) - used_modules
        if not unused_modules:
            return False

        prompt = f"""Based on the task and current results, determine if more modules are needed.

## Task
{instruction}

## Current Results
{json.dumps(results, indent=2, default=str)}

## Unused Modules
{', '.join(unused_modules)}

## Question
Do you need to use more modules to complete the task? Answer with "yes" or "no" only.

Answer:"""

        dialog: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self.acompletion_with_system_prompt(
                model="coder",
                messages=dialog,
            )

            answer = (response.choices[0].message.content or "no").strip().lower()  # type: ignore
            return answer.startswith("yes")
        except Exception:
            return False

    async def _execute_tool(self, tool_name: str, args: dict, readonly: bool) -> Any:
        """執行工具呼叫"""
        module = self._tool_name_to_module.get(tool_name)
        if not module:
            raise ValueError(f"Tool {tool_name} not found")

        readonly_tools = getattr(module.__class__, "_readonly_tools", {})
        if readonly and not readonly_tools.get(tool_name, False):
            raise ValueError(
                f"Tool {tool_name} is not readonly, but readonly mode is enabled"
            )

        tool_obj = self._tool_name_to_tool_obj.get(tool_name)
        if not tool_obj:
            raise ValueError(f"Tool object for {tool_name} not found")

        tool_func = tool_obj.fn
        if not tool_func:
            raise ValueError(f"Tool function for {tool_name} not found")

        import inspect

        if inspect.iscoroutinefunction(tool_func):
            result = await tool_func(module, **args)
        else:
            result = tool_func(module, **args)

        return result
