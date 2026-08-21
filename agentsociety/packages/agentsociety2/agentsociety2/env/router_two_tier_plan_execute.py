"""
Two-Tier Plan-and-Execute Router Implementation
雙層Plan-and-Execute模式：先選擇Module並制定計劃，然後執行計劃
"""

import json
import re
from typing import Tuple, Dict, Any, List

import json_repair
from litellm import AllMessageValues

from agentsociety2.logger import get_logger
from agentsociety2.env.base import EnvBase
from agentsociety2.env.router_base import RouterBase

__all__ = ["TwoTierPlanExecuteRouter"]


class TwoTierPlanExecuteRouter(RouterBase):
    """
    雙層Plan-and-Execute模式Router：先選擇Module並制定計劃，然後執行計劃。

    工作流程：
    1. 第一層：選擇合適的環境模組並制定執行計劃
    2. 第二層：按照計劃執行選中模組的工具呼叫
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
        使用雙層Plan-and-Execute模式處理指令。

        Args:
            ctx: 上下文字典
            instruction: 指令字串
            readonly: 是否只讀模式
            template_mode: 模板模式（TwoTierPlanExecuteRouter 不使用，僅為簽名相容）

        Returns:
            (ctx, answer) 元組
        """
        # 新增當前時間資訊到 ctx，以便工具呼叫可以訪問
        self._add_current_time_to_ctx(ctx)

        get_logger().info(
            f"TwoTierPlanExecuteRouter: Processing instruction: {instruction}, readonly: {readonly}"
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
                f"TwoTierPlanExecuteRouter: Step {step_count}/{self.max_steps}"
            )

            # 第一層：選擇模組並制定計劃
            module_plan = await self._select_module_and_plan(
                instruction, ctx, used_modules, readonly
            )

            if not module_plan or not module_plan.get("module"):
                get_logger().info(
                    "TwoTierPlanExecuteRouter: No more modules to process, task complete"
                )
                break

            selected_module = module_plan["module"]
            plan = module_plan.get("plan", [])

            used_modules.add(selected_module)
            get_logger().info(
                f"TwoTierPlanExecuteRouter: Selected module: {selected_module}, plan has {len(plan)} steps"
            )

            # 記錄模組選擇和計劃
            execution_log.append(
                {
                    "step": step_count,
                    "type": "module_selection_and_plan",
                    "module": selected_module,
                    "plan": plan,
                }
            )

            # 第二層：執行計劃
            module_result = await self._execute_module_plan(
                selected_module, plan, ctx, readonly
            )

            # 記錄模組執行結果
            execution_log.append(
                {
                    "step": step_count,
                    "type": "module_execution",
                    "module": selected_module,
                    "result": module_result,
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

            # 檢查是否需要更多模組
            if await self._needs_more_modules(
                instruction, ctx, results, used_modules, readonly
            ):
                continue
            else:
                break

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

    async def _select_module_and_plan(
        self, instruction: str, ctx: dict, used_modules: set, readonly: bool
    ) -> Dict[str, Any]:
        """第一層：選擇模組並制定計劃"""
        # 構建可用模組列表
        modules_list = [
            {
                "name": name,
                "description": info["description"],
                "tool_count": info["tool_count"],
                "tools": self._format_tools_for_module(name, readonly),
            }
            for name, info in self._module_info.items()
            if name not in used_modules
        ]

        if not modules_list:
            return {}

        modules_description = "\n\n".join(
            [
                f"### {m['name']}\n"
                f"Description: {m['description']}\n"
                f"Available Tools:\n{m['tools']}"
                for m in modules_list
            ]
        )

        readonly_note = (
            " (READONLY MODE - you can only use read-only tools)" if readonly else ""
        )
        context_repr = repr(ctx)

        prompt = f"""You need to select ONE environment module and create an execution plan for it.

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
1. Select ONE module that is most suitable for the current task.
2. Create a detailed execution plan using tools from that module.
3. The plan should be a JSON array of steps.
4. Include a final step with tool="set_status" to set the execution status.{readonly_note}

## Output Format
Return a JSON object with:
{{
    "module": "<module_name>",
    "plan": [
        {{
            "step": <step_number>,
            "tool": "<tool_name>",
            "arguments": {{<tool_arguments_as_json>}},
            "description": "<brief_description>"
        }},
        ...
    ]
}}

Your selection and plan:"""

        dialog: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self.acompletion_with_system_prompt(
                model="coder",
                messages=dialog,
            )

            response_text = response.choices[0].message.content or "{}"  # type: ignore
            module_plan = self._extract_json_from_text(response_text)

            if isinstance(module_plan, dict) and module_plan.get("module"):
                module_name = module_plan["module"]
                # 驗證模組是否有效
                if module_name in self._module_info and module_name not in used_modules:
                    # 確保plan是列表格式
                    if not isinstance(module_plan.get("plan"), list):
                        module_plan["plan"] = []
                    return module_plan
                else:
                    get_logger().warning(
                        f"TwoTierPlanExecuteRouter: Invalid module: {module_name}"
                    )

            # Fallback: 返回第一個未使用的模組，空計劃
            for module_name in self._module_info.keys():
                if module_name not in used_modules:
                    return {"module": module_name, "plan": []}

            return {}

        except Exception as e:
            get_logger().error(
                f"TwoTierPlanExecuteRouter: Failed to select module and plan: {str(e)}"
            )
            # Fallback
            for module_name in self._module_info.keys():
                if module_name not in used_modules:
                    return {"module": module_name, "plan": []}
            return {}

    def _format_tools_for_module(self, module_name: str, readonly: bool) -> str:
        """格式化模組的工具列表"""
        tools = (
            self._module_readonly_tools.get(module_name, [])
            if readonly
            else self._module_tools.get(module_name, [])
        )

        if not tools:
            return "  (no tools available)"

        descriptions = []
        for tool in tools:
            func_info = tool["function"]
            descriptions.append(
                f"  - {func_info['name']}: {func_info.get('description', 'No description')}"
            )
        return "\n".join(descriptions)

    def _extract_json_from_text(self, text: str) -> Any:
        """從文字中提取JSON"""

        # 嘗試找到JSON物件
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            try:
                return json_repair.loads(json_match.group())
            except Exception:
                pass

        # 嘗試直接解析
        try:
            return json_repair.loads(text)
        except Exception:
            pass

        return {}

    async def _execute_module_plan(
        self, module_name: str, plan: List[Dict[str, Any]], ctx: dict, readonly: bool
    ) -> Dict[str, Any]:
        """第二層：執行模組計劃"""
        results = {}

        for step in plan:
            tool_name = step.get("tool")
            arguments = step.get("arguments", {})

            if not tool_name:
                continue

            # 處理set_status工具
            if tool_name == "set_status":
                status = arguments.get("status", "unknown")
                reason = arguments.get("reason", "")
                results["status"] = status
                if reason:
                    results["reason"] = reason
                get_logger().info(f"TwoTierPlanExecuteRouter: Status set to {status}")
                continue

            try:
                result = await self._execute_tool(tool_name, arguments, readonly)
                results[tool_name] = result
                get_logger().debug(
                    f"TwoTierPlanExecuteRouter: Executed {tool_name} in {module_name}"
                )
            except Exception as e:
                error_msg = f"Error executing {tool_name}: {str(e)}"
                get_logger().error(f"TwoTierPlanExecuteRouter: {error_msg}")
                results[f"{tool_name}_error"] = error_msg

        return results

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

    async def _needs_more_modules(
        self,
        instruction: str,
        ctx: dict,
        results: dict,
        used_modules: set,
        readonly: bool,
    ) -> bool:
        """判斷是否還需要更多模組"""
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
