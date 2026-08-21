# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""PyInstaller 打包入口：根據引數分發到主應用或子命令。"""

from __future__ import annotations

import sys


def _pop_flag(flag: str) -> bool:
    if flag not in sys.argv:
        return False
    sys.argv.remove(flag)
    return True


def main() -> None:
    # 子命令：初始化工作區（首次使用需執行 jiuwenclaw.exe init）
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "init":
        sys.argv.pop(1)
        from jiuwenclaw.init_workspace import main as init_main
        init_main()
        return
    # 子命令：CLI 命令分發
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "acp":
        from jiuwenclaw.app_cli import main as cli_main
        cli_main()
        return
    if _pop_flag("--desktop-run-app"):
        from jiuwenclaw.app import main as app_main
        app_main()
        return
    if _pop_flag("--desktop-run-web"):
        from jiuwenclaw.app_web import main as web_main
        web_main()
        return
    # 子命令：瀏覽器啟動（供主程序 subprocess 呼叫）
    if "--browser-start-client" in sys.argv:
        idx = sys.argv.index("--browser-start-client")
        sys.argv.pop(idx)
        from jiuwenclaw.agentserver.tools.browser_start_client import main as browser_main
        raise SystemExit(browser_main())
    # 預設執行桌面應用。
    from jiuwenclaw.desktop_app import main as desktop_main
    desktop_main()


if __name__ == "__main__":
    main()
