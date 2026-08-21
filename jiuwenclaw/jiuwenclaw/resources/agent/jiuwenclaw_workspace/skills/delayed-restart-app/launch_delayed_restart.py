# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""以 detached 方式啟動 delayed_restart_app，供 bash 透過 skill 呼叫。

本指令碼會立即 spawn 子程序並退出，子程序與當前程序樹脫離，因此當 app 被終止時
子程序不會隨之結束，可以正常完成延遲重啟。

用法:
    python launch_delayed_restart.py --pid <PID> [--delay 5]
    （需在技能目錄或指定指令碼路徑下執行）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="以 detached 方式啟動延遲重啟")
    parser.add_argument("--pid", type=int, required=True, help="要終止的 app 程序 PID")
    parser.add_argument("--delay", type=float, default=5, help="延遲秒數（預設 5）")
    args = parser.parse_args()

    try:
        from jiuwenclaw.paths import get_root_dir
        root = get_root_dir()
    except Exception:
        root = Path.cwd()

    cmd = [
        sys.executable,
        "-m",
        "jiuwenclaw.scripts.delayed_restart_app",
        "--pid", str(args.pid),
        "--delay", str(max(1, min(args.delay, 300))),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    subprocess.Popen(
        cmd,
        cwd=str(root),
        creationflags=creationflags if sys.platform == "win32" else 0,
        start_new_session=(sys.platform != "win32"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
