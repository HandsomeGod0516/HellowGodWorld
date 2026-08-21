"""
JSON 配置檔案生成器

為掃描到的自定義模組生成 .agentsociety 目錄下的 JSON 配置檔案。
"""

import json
from pathlib import Path
from typing import Dict, Any


class CustomModuleJsonGenerator:
    """為自定義模組生成 .agentsociety JSON 配置檔案"""

    def __init__(self, workspace_path: str):
        """
        初始化生成器

        Args:
            workspace_path: 工作區路徑
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.agent_classes_dir = self.workspace_path / ".agentsociety/agent_classes"
        self.env_modules_dir = self.workspace_path / ".agentsociety/env_modules"

    def generate_all(self, scan_result: Dict[str, Any]) -> Dict[str, int]:
        """
        生成所有發現的模組的 JSON 檔案

        Args:
            scan_result: 掃描結果

        Returns:
            生成統計資訊
        """
        counts = {"agents_generated": 0, "envs_generated": 0, "errors": 0}

        # 確保目錄存在
        self.agent_classes_dir.mkdir(parents=True, exist_ok=True)
        self.env_modules_dir.mkdir(parents=True, exist_ok=True)

        # 生成 Agent JSON
        for agent in scan_result.get("agents", []):
            if self._generate_agent_json(agent):
                counts["agents_generated"] += 1
            else:
                counts["errors"] += 1

        # 生成環境模組 JSON
        for env in scan_result.get("envs", []):
            if self._generate_env_json(env):
                counts["envs_generated"] += 1
            else:
                counts["errors"] += 1

        return counts

    def _generate_agent_json(self, agent_info: Dict[str, Any]) -> bool:
        """
        生成單個 Agent 的 JSON 檔案

        Args:
            agent_info: Agent 資訊字典

        Returns:
            是否成功生成
        """
        try:
            file_path = self.agent_classes_dir / f"{agent_info['type'].lower()}.json"

            # 檢查是否已存在非自定義的檔案
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if not existing.get("is_custom"):
                        # 不覆蓋內建模組
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
        生成單個環境模組的 JSON 檔案

        Args:
            env_info: 環境模組資訊字典

        Returns:
            是否成功生成
        """
        try:
            file_path = self.env_modules_dir / f"{env_info['type'].lower()}.json"

            # 檢查是否已存在非自定義的檔案
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
        刪除所有標記為自定義的 JSON 檔案

        Returns:
            刪除的檔案數量
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

        # 清理環境模組 JSON
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
