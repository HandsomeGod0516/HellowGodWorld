"""
程式碼生成器（LLM -> Python 指令碼）。

該模組提供 :class:`~agentsociety2.code_executor.code_generator.CodeGenerator`，用於把“任務描述 + 可選參考檔案/上下文”
轉成一段 **可執行** 的 Python 程式碼字串。
"""

import os
from typing import Optional, List

from litellm import AllMessageValues

from agentsociety2.logger import get_logger
from agentsociety2.config import get_llm_router_and_model

logger = get_logger()


class CodeGenerator:
    """基於大模型的 Python 程式碼生成器。

    該類內部透過 :func:`agentsociety2.config.get_llm_router_and_model` 讀取 ``coder`` 路由配置，
    並使用 LiteLLM Router 發起非同步補全請求。
    """

    def __init__(
        self,
    ):
        """初始化程式碼生成器。"""
        self._router, self._model_name = get_llm_router_and_model("coder")

    async def generate(
        self,
        description: str,
        input_files: Optional[list[str]] = None,
        additional_context: Optional[str] = None,
    ) -> str:
        """生成 Python 程式碼。

        :param description: 任務描述/約束條件。
        :param input_files: 可選。參考檔案路徑列表；存在的檔案會被讀入提示詞。
        :param additional_context: 可選。追加上下文（例如執行環境、輸入輸出約定等）。
        :returns: 生成的 Python 程式碼（儘量為純程式碼文字；若模型返回 Markdown，會自動提取程式碼塊）。
        :raises Exception: 當底層 LLM 呼叫失敗或返回空內容時丟擲。
        """
        # 構建提示詞
        prompt = self._build_prompt(description, input_files, additional_context)

        logger.info(f"開始生成程式碼，使用模型: {self._model_name}")

        # 呼叫大模型
        messages: list[AllMessageValues] = [{"role": "user", "content": prompt}]

        try:
            response = await self._router.acompletion(
                model=self._model_name,
                messages=messages,
                stream=False,
            )

            generated_code = response.choices[0].message.content  # type: ignore

            if not generated_code:
                raise ValueError("模型返回空內容")

            # 提取程式碼塊（如果返回的是markdown格式）
            code = self._extract_code(generated_code)

            logger.info(f"程式碼生成成功，長度: {len(code)} 字元")
            return code

        except Exception as e:
            logger.error(f"程式碼生成失敗: {e}")
            raise

    def _build_prompt(
        self,
        description: str,
        input_files: Optional[list[str]] = None,
        additional_context: Optional[str] = None,
    ) -> str:
        """構建生成程式碼所用提示詞。"""
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
                        logger.warning(f"無法讀取檔案 {file_path}: {e}")
                else:
                    logger.warning(f"檔案不存在: {file_path}")

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
        """從模型輸出中提取“可直接執行”的程式碼文字。"""
        import re

        # 嘗試提取markdown程式碼塊
        # 匹配 ```python ... ``` 或 ``` ... ```
        code_block_pattern = r"```(?:python|py)?\s*\n(.*?)```"
        matches = re.findall(code_block_pattern, generated_text, re.DOTALL)

        if matches:
            # 返回最長的程式碼塊（更可能是完整程式碼）
            code = max(matches, key=len).strip()
            return code

        # 如果沒有程式碼塊，返回原文字（去除首尾空白）
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
        """帶反饋的多輪生成（失敗後可攜帶錯誤資訊重試）。

        :param initial_description: 初始任務描述。
        :param input_files: 可選。參考檔案路徑列表。
        :param additional_context: 可選。追加上下文。
        :param max_retries: 最大重試次數（不含首次嘗試）。
        :param error_feedback: 可選。上一輪執行/校驗得到的錯誤資訊列表。
        :param previous_code: 可選。上一輪生成的程式碼文字。
        :returns: ``(code, ok)``，其中 ``ok`` 表示是否成功得到非空程式碼。
        """
        # 構建初始提示詞
        initial_prompt = self._build_prompt(
            initial_description, input_files, additional_context
        )

        # 初始化對話歷史
        messages: list[AllMessageValues] = [{"role": "user", "content": initial_prompt}]

        # 如果有之前的程式碼和錯誤反饋，新增到對話歷史
        if previous_code and error_feedback:
            messages.append({"role": "assistant", "content": previous_code})
            error_message = self._build_error_feedback_message(error_feedback)
            messages.append({"role": "user", "content": error_message})

        retry_count = 0
        while retry_count <= max_retries:
            try:
                logger.info(
                    f"開始生成程式碼（嘗試 {retry_count + 1}/{max_retries + 1}），使用模型: {self._model_name}"
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
                            f"模型返回空內容，重試 {retry_count}/{max_retries}"
                        )
                        continue
                    return "", False

                # 提取程式碼塊（如果返回的是markdown格式）
                code = self._extract_code(generated_code)

                logger.info(f"程式碼生成成功，長度: {len(code)} 字元")
                return code, True

            except Exception as e:
                logger.error(f"程式碼生成失敗: {e}")
                if retry_count < max_retries:
                    retry_count += 1
                    logger.warning(f"程式碼生成異常，重試 {retry_count}/{max_retries}")
                    continue
                return "", False

        return "", False

    def _build_error_feedback_message(self, errors: List[str]) -> str:
        """把錯誤列表整理成可用於下一輪生成的提示詞片段。"""
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
