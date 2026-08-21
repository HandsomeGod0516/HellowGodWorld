"""
代码生成器（LLM -> Python 脚本）。

该模块提供 :class:`~agentsociety2.code_executor.code_generator.CodeGenerator`，用于把“任务描述 + 可选参考文件/上下文”
转成一段 **可执行** 的 Python 代码字符串。
"""

import os
from typing import Optional, List

from litellm import AllMessageValues

from agentsociety2.logger import get_logger
from agentsociety2.config import get_llm_router_and_model

logger = get_logger()


class CodeGenerator:
    """基于大模型的 Python 代码生成器。

    该类内部通过 :func:`agentsociety2.config.get_llm_router_and_model` 读取 ``coder`` 路由配置，
    并使用 LiteLLM Router 发起异步补全请求。
    """

    def __init__(
        self,
    ):
        """初始化代码生成器。"""
        self._router, self._model_name = get_llm_router_and_model("coder")

    async def generate(
        self,
        description: str,
        input_files: Optional[list[str]] = None,
        additional_context: Optional[str] = None,
    ) -> str:
        """生成 Python 代码。

        :param description: 任务描述/约束条件。
        :param input_files: 可选。参考文件路径列表；存在的文件会被读入提示词。
        :param additional_context: 可选。追加上下文（例如运行环境、输入输出约定等）。
        :returns: 生成的 Python 代码（尽量为纯代码文本；若模型返回 Markdown，会自动提取代码块）。
        :raises Exception: 当底层 LLM 调用失败或返回空内容时抛出。
        """
        # 构建提示词
        prompt = self._build_prompt(description, input_files, additional_context)

        logger.info(f"开始生成代码，使用模型: {self._model_name}")

        # 调用大模型
        messages: list[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self._router.acompletion(
                model=self._model_name,
                messages=messages,
                stream=False,
            )

            generated_code = response.choices[0].message.content  # type: ignore

            if not generated_code:
                raise ValueError("模型返回空内容")

            # 提取代码块（如果返回的是markdown格式）
            code = self._extract_code(generated_code)

            logger.info(f"代码生成成功，长度: {len(code)} 字符")
            return code

        except Exception as e:
            logger.error(f"代码生成失败: {e}")
            raise

    def _build_prompt(
        self,
        description: str,
        input_files: Optional[list[str]] = None,
        additional_context: Optional[str] = None,
    ) -> str:
        """构建生成代码所用提示词。"""
        prompt_parts = []

        # Base prompt
        prompt_parts.append(
            """You are a professional Python code generation assistant. Please generate complete, executable Python code based on the following requirements.

Requirements:
1. The generated code should be a complete, executable Python script
2. The code should include necessary import statements
3. If the code requires command-line arguments, use argparse
4. The code should include appropriate error handling
5. The code should have good readability and comments

"""
        )

        # Add description
        prompt_parts.append(f"## Task Description\n{description}\n\n")

        # Add input file contents (if provided)
        if input_files:
            prompt_parts.append("## Reference Files\n")
            for file_path in input_files:
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        prompt_parts.append(
                            f"### File: {file_path}\n```\n{content}\n```\n\n"
                        )
                    except Exception as e:
                        logger.warning(f"无法读取文件 {file_path}: {e}")
                else:
                    logger.warning(f"文件不存在: {file_path}")

        # Add additional context
        if additional_context:
            prompt_parts.append(f"## Additional Context\n{additional_context}\n\n")

        # Add output requirements
        prompt_parts.append(
            """## Output Requirements

Please output Python code directly, without markdown code block markers (```python, etc.).
If you must use code blocks, ensure the code can be copied and used directly.

Generated code:
"""
        )

        return "".join(prompt_parts)

    def _extract_code(self, generated_text: str) -> str:
        """从模型输出中提取“可直接执行”的代码文本。"""
        import re

        # 尝试提取markdown代码块
        # 匹配 ```python ... ``` 或 ``` ... ```
        code_block_pattern = r"```(?:python|py)?\s*\n(.*?)```"
        matches = re.findall(code_block_pattern, generated_text, re.DOTALL)

        if matches:
            # 返回最长的代码块（更可能是完整代码）
            code = max(matches, key=len).strip()
            return code

        # 如果没有代码块，返回原文本（去除首尾空白）
        return generated_text.strip()

    async def generate_with_feedback(
        self,
        initial_description: str,
        input_files: Optional[list[str]] = None,
        additional_context: Optional[str] = None,
        max_retries: int = 3,
        error_feedback: Optional[List[str]] = None,
        previous_code: Optional[str] = None,
    ) -> tuple[str, bool]:
        """带反馈的多轮生成（失败后可携带错误信息重试）。

        :param initial_description: 初始任务描述。
        :param input_files: 可选。参考文件路径列表。
        :param additional_context: 可选。追加上下文。
        :param max_retries: 最大重试次数（不含首次尝试）。
        :param error_feedback: 可选。上一轮执行/校验得到的错误信息列表。
        :param previous_code: 可选。上一轮生成的代码文本。
        :returns: ``(code, ok)``，其中 ``ok`` 表示是否成功得到非空代码。
        """
        # 构建初始提示词
        initial_prompt = self._build_prompt(
            initial_description, input_files, additional_context
        )

        # 初始化对话历史
        messages: list[AllMessageValues] = [{"role": "user", "content": initial_prompt}]

        # 如果有之前的代码和错误反馈，添加到对话历史
        if previous_code and error_feedback:
            messages.append({"role": "assistant", "content": previous_code})
            error_message = self._build_error_feedback_message(error_feedback)
            messages.append({"role": "user", "content": error_message})

        retry_count = 0
        while retry_count <= max_retries:
            try:
                logger.info(
                    f"开始生成代码（尝试 {retry_count + 1}/{max_retries + 1}），使用模型: {self._model_name}"
                )

                response = await self._router.acompletion(
                    model=self._model_name,
                    messages=messages,
                    stream=False,
                )

                generated_code = response.choices[0].message.content  # type: ignore

                if not generated_code:
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.warning(
                            f"模型返回空内容，重试 {retry_count}/{max_retries}"
                        )
                        continue
                    return "", False

                # 提取代码块（如果返回的是markdown格式）
                code = self._extract_code(generated_code)

                logger.info(f"代码生成成功，长度: {len(code)} 字符")
                return code, True

            except Exception as e:
                logger.error(f"代码生成失败: {e}")
                if retry_count < max_retries:
                    retry_count += 1
                    logger.warning(f"代码生成异常，重试 {retry_count}/{max_retries}")
                    continue
                return "", False

        return "", False

    def _build_error_feedback_message(self, errors: List[str]) -> str:
        """把错误列表整理成可用于下一轮生成的提示词片段。"""
        error_parts = [
            "The previous code execution failed with the following error(s):",
            "",
        ]

        for i, error in enumerate(errors, 1):
            error_parts.append(f"Error {i}:")
            error_parts.append("```")
            error_parts.append(error)
            error_parts.append("```")
            error_parts.append("")

        error_parts.extend(
            [
                "Please fix the code based on the error messages above.",
                "Generate the corrected Python code:",
            ]
        )

        return "\n".join(error_parts)
