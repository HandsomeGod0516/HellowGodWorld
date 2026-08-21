"""
原生代碼執行器

直接使用當前Python直譯器執行程式碼，不使用Docker。

主要入口為 :meth:`~agentsociety2.code_executor.local_executor.LocalCodeExecutor.execute`，
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
    """原生代碼執行器（子程序執行）。

    :param work_dir: 工作目錄。程式碼會寫入該目錄並在該目錄下執行；執行產生的新增檔案會記錄為 artifacts。
    """

    def __init__(self, work_dir: Path):
        """建立執行器並確保工作目錄存在。"""
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
        """在當前 Python 直譯器中執行程式碼。

        :param code: Python 程式碼文字。
        :param dependencies: 可選。需要安裝的依賴包名列表。
            依賴安裝器由環境變數 ``CODE_EXECUTOR_DEPS_INSTALLER`` 控制：

            - ``pip``（預設）：``python -m pip install --quiet ...``
            - ``uv``：若檢測到 ``uv`` 命令則使用 ``uv pip install --quiet ...``
            - ``conda``：若檢測到 ``conda`` 命令則使用 ``conda install -y ...``
            - ``0/false/no/off/none/never``：禁用安裝（直接執行）
        :param timeout: 超時時間（秒）。
        :param input_data: 可選。作為 stdin 輸入的字串。
        :param extra_files: 可選。額外輸入檔案路徑（會複製到 ``work_dir`` 根目錄；同名檔案已存在則跳過以避免覆蓋）。
        :param program_args: 可選。傳給指令碼的命令列引數。
        :returns: 執行結果（包含 ``stdout``/``stderr``/``return_code``/``execution_time`` 以及新增檔案列表 ``artifacts``）。
        """
        start_time = time.time()
        deps_installer = os.getenv("CODE_EXECUTOR_DEPS_INSTALLER", "pip").strip().lower()

        # 建立臨時檔案儲存程式碼
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
            # 準備環境變數：顯式傳遞父程序的環境變數
            env = os.environ.copy()

            # 複製額外輸入檔案到工作目錄
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

            # 基線：準備完輸入後記錄檔案集合，artifact 只統計“執行產生的新增檔案”
            files_before = {p for p in self._work_dir.rglob("*") if p.is_file()}

            # 如果需要安裝依賴
            if dependencies:
                deps_list = [dep.strip() for dep in dependencies if dep.strip()]
                if deps_list:
                    if deps_installer in ("0", "false", "no", "off", "none", "never"):
                        logger.info("跳過依賴安裝（CODE_EXECUTOR_DEPS_INSTALLER 禁用）")
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
                                f"無法安裝依賴（installer={deps_installer} 不可用或未找到命令），繼續執行程式碼"
                            )
                        else:
                            logger.info(f"安裝依賴（{deps_installer}）: {deps_list}")
                            install_process = await asyncio.create_subprocess_exec(
                                *installer_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env=env,
                            )
                            await install_process.wait()
                            if install_process.returncode != 0:
                                logger.warning("依賴安裝失敗，但繼續執行程式碼")

            # 執行程式碼
            logger.info(f"執行程式碼檔案: {tmp_file_path}")

            # 使用subprocess執行，捕獲輸出
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(tmp_file_path),
                *list(program_args or []),
                stdin=subprocess.PIPE if input_data else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._work_dir),
                env=env,  # 顯式傳遞環境變數
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
                stderr = f"執行超時（超過{timeout}秒）".encode("utf-8")
                success = False
                logger.error(f"程式碼執行超時: {tmp_file_path}")

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
            # 清理臨時檔案
            try:
                tmp_file_path.unlink()
            except Exception as e:
                logger.warning(f"清理臨時檔案失敗: {e}")

