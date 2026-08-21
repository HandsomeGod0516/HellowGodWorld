"""
本地代码执行器

直接使用当前Python解释器执行代码，不使用Docker。

主要入口为 :meth:`~agentsociety2.code_executor.local_executor.LocalCodeExecutor.execute`，
返回 :class:`~agentsociety2.code_executor.models.ExecutionResult`。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from agentsociety2.code_executor.models import ExecutionResult
from agentsociety2.logger import get_logger

logger = get_logger()


class LocalCodeExecutor:
    """本地代码执行器（子进程执行）。

    :param work_dir: 工作目录。代码会写入该目录并在该目录下运行；执行产生的新增文件会记录为 artifacts。
    """

    def __init__(self, work_dir: Path):
        """创建执行器并确保工作目录存在。"""
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def execute(
        self,
        code: str,
        *,
        dependencies: Optional[Iterable[str]] = None,
        timeout: int = 300,
        input_data: Optional[str] = None,
        extra_files: Optional[Iterable[Path | str]] = None,
        program_args: Optional[Iterable[str]] = None,
    ) -> ExecutionResult:
        """在当前 Python 解释器中执行代码。

        :param code: Python 代码文本。
        :param dependencies: 可选。需要安装的依赖包名列表。
            依赖安装器由环境变量 ``CODE_EXECUTOR_DEPS_INSTALLER`` 控制：

            - ``pip``（默认）：``python -m pip install --quiet ...``
            - ``uv``：若检测到 ``uv`` 命令则使用 ``uv pip install --quiet ...``
            - ``conda``：若检测到 ``conda`` 命令则使用 ``conda install -y ...``
            - ``0/false/no/off/none/never``：禁用安装（直接执行）
        :param timeout: 超时时间（秒）。
        :param input_data: 可选。作为 stdin 输入的字符串。
        :param extra_files: 可选。额外输入文件路径（会复制到 ``work_dir`` 根目录；同名文件已存在则跳过以避免覆盖）。
        :param program_args: 可选。传给脚本的命令行参数。
        :returns: 执行结果（包含 ``stdout``/``stderr``/``return_code``/``execution_time`` 以及新增文件列表 ``artifacts``）。
        """
        start_time = time.time()
        deps_installer = os.getenv("CODE_EXECUTOR_DEPS_INSTALLER", "pip").strip().lower()

        # 创建临时文件保存代码
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            dir=self._work_dir,
            delete=False,
            encoding="utf-8",
        ) as tmp_file:
            tmp_file.write(code)
            tmp_file_path = Path(tmp_file.name)

        try:
            # 准备环境变量：显式传递父进程的环境变量
            env = os.environ.copy()

            # 复制额外输入文件到工作目录
            extra_files = list(extra_files or [])
            for file_path in extra_files:
                src = Path(file_path)
                if not src.exists() or not src.is_file():
                    continue
                dst = self._work_dir / src.name
                if dst.resolve() == src.resolve():
                    continue
                if dst.exists():
                    continue
                shutil.copy2(src, dst)

            # 基线：准备完输入后记录文件集合，artifact 只统计“执行产生的新增文件”
            files_before = {p for p in self._work_dir.rglob("*") if p.is_file()}

            # 如果需要安装依赖
            if dependencies:
                deps_list = [dep.strip() for dep in dependencies if dep.strip()]
                if deps_list:
                    if deps_installer in ("0", "false", "no", "off", "none", "never"):
                        logger.info("跳过依赖安装（CODE_EXECUTOR_DEPS_INSTALLER 禁用）")
                    else:
                        installer_cmd: list[str] | None = None
                        if deps_installer == "pip":
                            installer_cmd = [sys.executable, "-m", "pip", "install", "--quiet", *deps_list]
                        elif deps_installer == "uv":
                            if shutil.which("uv"):
                                installer_cmd = ["uv", "pip", "install", "--quiet", *deps_list]
                        elif deps_installer == "conda":
                            if shutil.which("conda"):
                                installer_cmd = ["conda", "install", "-y", *deps_list]

                        if installer_cmd is None:
                            logger.warning(
                                f"无法安装依赖（installer={deps_installer} 不可用或未找到命令），继续执行代码"
                            )
                        else:
                            logger.info(f"安装依赖（{deps_installer}）: {deps_list}")
                            install_process = await asyncio.create_subprocess_exec(
                                *installer_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env=env,
                            )
                            await install_process.wait()
                            if install_process.returncode != 0:
                                logger.warning("依赖安装失败，但继续执行代码")

            # 执行代码
            logger.info(f"执行代码文件: {tmp_file_path}")

            # 使用subprocess执行，捕获输出
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(tmp_file_path),
                *list(program_args or []),
                stdin=subprocess.PIPE if input_data else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._work_dir),
                env=env,  # 显式传递环境变量
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=input_data.encode("utf-8") if input_data else None),
                    timeout=timeout,
                )
                success = process.returncode == 0
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                stdout = b""
                stderr = f"执行超时（超过{timeout}秒）".encode("utf-8")
                success = False
                logger.error(f"代码执行超时: {tmp_file_path}")

            execution_time = time.time() - start_time

            files_after = {p for p in self._work_dir.rglob("*") if p.is_file()}
            artifacts = sorted(
                str(p.relative_to(self._work_dir)) for p in (files_after - files_before)
            )

            return ExecutionResult(
                success=success,
                stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                return_code=process.returncode if process.returncode is not None else -1,
                execution_time=execution_time,
                artifacts_path=str(self._work_dir),
                artifacts=artifacts,
            )

        finally:
            # 清理临时文件
            try:
                tmp_file_path.unlink()
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")

