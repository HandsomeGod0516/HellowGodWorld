"""
JSON 配置文件生成器

为扫描到的自定义模块生成 .agentsociety 目录下的 JSON 配置文件。
"""

import json
from pathlib import Path
from typing import Dict, Any


class CustomModuleJsonGenerator:
    """为自定义模块生成 .agentsociety JSON 配置文件"""

    def __init__(self, workspace_path: str):
        """
        初始化生成器

        Args:
            workspace_path: 工作区路径
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.agent_classes_dir = self.workspace_path / ".agentsociety/agent_classes"
        self.env_modules_dir = self.workspace_path / ".agentsociety/env_modules"

    def generate_all(self, scan_result: Dict[str, Any]) -> Dict[str, int]:
        """
        生成所有发现的模块的 JSON 文件

        Args:
            scan_result: 扫描结果

        Returns:
            生成统计信息
        """
        counts = {"agents_generated": 0, "envs_generated": 0, "errors": 0}

        # 确保目录存在
        self.agent_classes_dir.mkdir(parents=True, exist_ok=True)
        self.env_modules_dir.mkdir(parents=True, exist_ok=True)

        # 生成 Agent JSON
        for agent in scan_result.get("agents", []):
            if self._generate_agent_json(agent):
                counts["agents_generated"] += 1
            else:
                counts["errors"] += 1

        # 生成环境模块 JSON
        for env in scan_result.get("envs", []):
            if self._generate_env_json(env):
                counts["envs_generated"] += 1
            else:
                counts["errors"] += 1

        return counts

    def _generate_agent_json(self, agent_info: Dict[str, Any]) -> bool:
        """
        生成单个 Agent 的 JSON 文件

        Args:
            agent_info: Agent 信息字典

        Returns:
            是否成功生成
        """
        try:
            file_path = self.agent_classes_dir / f"{agent_info['type'].lower()}.json"

            # 检查是否已存在非自定义的文件
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if not existing.get("is_custom"):
                        # 不覆盖内置模块
                        return False

            data = {
                "type": agent_info["type"],
                "class_name": agent_info["class_name"],
                "description": agent_info["description"],
                "is_custom": True,
                "module_path": agent_info.get("module_path", ""),
                "file_path": agent_info.get("file_path", ""),
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return True
        except Exception:
            return False

    def _generate_env_json(self, env_info: Dict[str, Any]) -> bool:
        """
        生成单个环境模块的 JSON 文件

        Args:
            env_info: 环境模块信息字典

        Returns:
            是否成功生成
        """
        try:
            file_path = self.env_modules_dir / f"{env_info['type'].lower()}.json"

            # 检查是否已存在非自定义的文件
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if not existing.get("is_custom"):
                        return False

            data = {
                "type": env_info["type"],
                "class_name": env_info["class_name"],
                "description": env_info["description"],
                "is_custom": True,
                "module_path": env_info.get("module_path", ""),
                "file_path": env_info.get("file_path", ""),
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return True
        except Exception:
            return False

    def remove_custom_modules(self) -> int:
        """
        删除所有标记为自定义的 JSON 文件

        Returns:
            删除的文件数量
        """
        count = 0

        # 清理 Agent JSON
        if self.agent_classes_dir.exists():
            for json_file in self.agent_classes_dir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("is_custom"):
                            json_file.unlink()
                            count += 1
                except Exception:
                    pass

        # 清理环境模块 JSON
        if self.env_modules_dir.exists():
            for json_file in self.env_modules_dir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("is_custom"):
                            json_file.unlink()
                            count += 1
                except Exception:
                    pass

        return count
