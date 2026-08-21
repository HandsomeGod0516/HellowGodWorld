"""CLI：將執行時資料初始化到使用者資料根目錄（與 ``get_user_workspace_dir()`` 一致）。

預設根目錄為 ``~/.jiuwenclaw``；若程序環境中已設定 ``JIUWENCLAW_DATA_DIR``（須為可用絕對路徑，
且應在啟動本指令碼前注入，見 ``jiuwenclaw.utils`` 中的 ``JIUWENCLAW_DATA_DIR``），則初始化到該路徑下。

無論是透過 pip/whl 安裝，還是在原始碼目錄裡直接執行：
- 執行本指令碼會先詢問語言偏好（zh/en），寫入 config 的 preferred_language；
- 同時複製 config.yaml、builtin_rules.yaml、將 ``.env.template`` 複製為 ``<使用者資料根>/config/.env``、agent 模板等到 ``<使用者資料根>``；
- 根據語言偏好複製多語言檔案（AGENT.md、HEARTBEAT.md、IDENTITY.md、SOUL.md 等），
  原始檔使用 _ZH/_EN 字尾，目標檔案不帶字尾。

使用方式:
- jiuwenclaw-init -f: 強制清理，刪除整個使用者資料根目錄後重新初始化
- jiuwenclaw-init: 保留原有資料，執行遷移合併
- jiuwenclaw-init --name alice: 建立命名例項 alice
- jiuwenclaw-init -f --name alice: 強制重建命名例項 alice
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from jiuwenclaw.common.utils import get_user_home, init_user_workspace, get_user_workspace_dir
from jiuwenclaw.instance_manager import (
    create_bootstrap_env,
    get_default_instance_status,
    get_instance_config,
    get_instance_status,
    get_instance_workspace_path,
    get_instance_index,
    calculate_instance_ports,
    update_instances_yaml,
    validate_instance_name,
    InstanceConfig,
)


def run_init(force: bool = False, name: Optional[str] = None) -> int:
    """Run workspace initialization.

    Args:
        force: Force clean initialization, delete entire workspace before init
        name: Named instance name (e.g., alice, bob)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 1. Validate instance name if provided
    if name:
        validation_error = validate_instance_name(name)
        if validation_error:
            print(f"[jiuwenclaw-init] ERROR: {validation_error}")
            return 1

    # 2. Determine target workspace path and set env var
    if name:
        workspace_path = get_instance_workspace_path(name)
        print(f"[jiuwenclaw-init] Creating instance: {name}")
        print(f"[jiuwenclaw-init] Workspace: {workspace_path}")
    else:
        workspace_path = get_user_home() / ".jiuwenclaw"
        print(f"[jiuwenclaw-init] Initializing default workspace")
        print(f"[jiuwenclaw-init] Workspace: {workspace_path}")

    # 3. Check if instance is running (for named instances, always check)
    if name:
        # For named instance, check if it's running
        config = get_instance_config(name)
        if config is None:
            # Instance not in instances.yaml yet, use default config
            workspace_path = get_instance_workspace_path(name)
            ports = calculate_instance_ports(1)  # Will be recalculated when added to yaml
            config = InstanceConfig(name=name, workspace=workspace_path, ports=ports)

        status = get_instance_status(config)
        if status.running:
            print(f"[jiuwenclaw-init] ERROR: Instance '{name}' is running (PID={status.pid}).")
            print(f"[jiuwenclaw-init] Stop it first with: jiuwenclaw-start --stop {name}")
            return 1
    elif force:
        # For default instance, use get_default_instance_status which includes port detection
        status = get_default_instance_status()
        if status.running:
            print(f"[jiuwenclaw-init] ERROR: Default instance is running (PID={status.pid or '-'}).")
            print(f"[jiuwenclaw-init] Stop it first with: jiuwenclaw-start --stop default")
            return 1

    # 4. Call init_user_workspace with workspace path
    #    (deletion and confirmation handled by init_user_workspace)
    target = init_user_workspace(overwrite=force, workspace_dir=workspace_path)

    # 5. Post-init: create bootstrap .env and update instances.yaml for named instance
    if name and target != "cancelled":
        # Calculate ports (using same index as update_instances_yaml will use)
        index = get_instance_index(name)
        ports = calculate_instance_ports(index)

        # Update YAML with full configuration (workspace + ports)
        update_instances_yaml(name, workspace_path, ports)

        # Create bootstrap .env with the same ports
        config = InstanceConfig(name=name, workspace=workspace_path, ports=ports)
        create_bootstrap_env(config)

        print(f"[jiuwenclaw-init] Instance '{name}' initialized successfully.")
        return 0

    if target == "cancelled":
        return 1

    print(f"[jiuwenclaw-init] initialized: {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize jiuwenclaw workspace directory (~/.jiuwenclaw)"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force clean initialization: delete entire workspace before init",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Create a named instance workspace (e.g., alice, bob)",
    )
    # Use parse_known_args so that calling main() under pytest (which leaves
    # test paths in sys.argv) does not fail with SystemExit on unknown args.
    args, _ = parser.parse_known_args()
    return run_init(force=args.force, name=args.name)


if __name__ == "__main__":
    sys.exit(main())
