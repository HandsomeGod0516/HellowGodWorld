# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillManager - 管理 skills 的載入、安裝、解除安裝與 marketplace 操作."""

from __future__ import annotations

import logging
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
import zipfile
from datetime import datetime, date, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlparse
import yaml
import urllib3
import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from openjiuwen.agent_evolving.checkpointing.evolution_store import (
    EvolutionLog as EvolutionFile,
    EvolutionRecord as EvolutionEntry,
)
from jiuwenclaw.common.utils import (
    get_agent_root_dir,
    get_agent_skills_dir,
    get_builtin_skills_dir,
    is_package_installation,
)

logger = logging.getLogger(__name__)

_SKILLNET_DOWNLOAD_TIMEOUT: int = int(os.environ.get("SKILLNET_DOWNLOAD_TIMEOUT", "60"))
_SKILLNET_MAX_RETRIES: int = int(os.environ.get("SKILLNET_MAX_RETRIES", "3"))
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_SKILLNET_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_SKILLNET_NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
_FREE_SEARCH_DEFAULT_NO_PROXY = "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com"

# Team Skills Hub（僅 TEAM_SKILLS_HUB_* 環境變數）
_TEAM_SKILLS_HUB_MARKET_TIMEOUT: float = float(os.environ.get("TEAM_SKILLS_HUB_TIMEOUT", "60"))
_TEAM_SKILLS_HUB_BASE_URL_DEFAULT = "https://teamskills.openjiuwen.com"
_TEAM_SKILLS_HUB_DEFAULT_ALLOWED_DOWNLOAD_HOSTS: tuple[str, ...] = (
    "openjiuwen-market.obs.*.myhuaweicloud.com",
    "127.0.0.1",
    "localhost",
)
_IMPORT_LOCAL_REMOTE_TIMEOUT: float = float(os.environ.get("IMPORT_LOCAL_REMOTE_TIMEOUT", "60"))
_IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS: tuple[str, ...] = ("*.obs.*.myhuaweicloud.com",)


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _ImportLocalTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ssl_version=ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# ---------------------------------------------------------------------------
# 預設路徑
# ---------------------------------------------------------------------------
_EVOLUTION_FILENAME = "evolutions.json"


def _get_agent_root_dir() -> "Path":
    return get_agent_root_dir()


def _get_marketplace_dir() -> "Path":
    return get_agent_skills_dir() / "_marketplace"


def _get_state_file() -> "Path":
    return get_agent_skills_dir() / "skills_state.json"


class SkillNetEmptyDownloadError(Exception):
    """skillnet-ai ``download()`` returned None; 前端用 detail_key 做多語言。"""

    def __init__(self, *, github_context: str = "") -> None:
        self.github_context = (github_context or "").strip()
        self.detail_key = "skills.skillNet.errors.emptyDownloadResult"
        hint = f"\n{self.github_context[:800]}" if self.github_context else ""
        self.detail_params = {"hint": hint}
        super().__init__(self.github_context or "empty download path")


def _is_valid_http_mirror_url(url: str) -> bool:
    """Return True if url is a plausible http(s) mirror base (for SkillDownloader)."""
    s = url.strip()
    if not s or len(s) > 2048:
        return False
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _get_free_search_proxy_url() -> str:
    return str(os.environ.get(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _free_search_ssl_verify() -> bool:
    return _env_bool(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _disable_insecure_request_warning() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _skillnet_proxy_mapping() -> dict[str, str]:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


def _configure_skillnet_requests_session(session: Any) -> None:
    proxies = _skillnet_proxy_mapping()
    if proxies:
        session.proxies.update(proxies)
    verify = _free_search_ssl_verify()
    session.verify = verify
    if verify is False:
        _disable_insecure_request_warning()


@contextmanager
def _skillnet_network_context():
    """Expose the configured proxy to third-party SkillNet clients during one call."""
    proxy_url = _get_free_search_proxy_url()
    env_keys = (*_SKILLNET_PROXY_ENV_KEYS, *_SKILLNET_NO_PROXY_ENV_KEYS)
    previous = {key: os.environ.get(key) for key in env_keys}
    try:
        if proxy_url:
            for key in _SKILLNET_PROXY_ENV_KEYS:
                os.environ[key] = proxy_url
            if not os.environ.get("NO_PROXY") and not os.environ.get("no_proxy"):
                for key in _SKILLNET_NO_PROXY_ENV_KEYS:
                    os.environ[key] = _FREE_SEARCH_DEFAULT_NO_PROXY
        if _free_search_ssl_verify() is False:
            _disable_insecure_request_warning()
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _safe_path_name(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"invalid {label} name")
    path_value = Path(raw)
    invalid_name_checks = (
        raw in (".", ".."),
        "/" in raw,
        "\\" in raw,
        path_value.is_absolute(),
        PureWindowsPath(raw).is_absolute(),
    )
    if any(invalid_name_checks):
        raise ValueError(f"invalid {label} name: {raw}")
    return raw


def _safe_child_path(base: Path, name: Any, label: str) -> Path:
    safe_name = _safe_path_name(name, label)
    base_resolved = base.resolve()
    candidate = (base / safe_name).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"invalid {label} path: {safe_name}") from exc
    return candidate


def _log_rejected_name(operation: str, label: str, value: Any, exc: ValueError) -> None:
    logger.warning(
        "rejected invalid %s name: operation=%s value=%r error=%s",
        label,
        operation,
        value,
        exc,
    )


def _safe_rmtree(path: Path) -> bool:
    """安全地刪除目錄樹，處理 Windows 上的 git 檔案鎖定問題."""
    if not path.exists():
        return True

    import time
    import stat

    max_retries = 3
    retry_delay = 0.2

    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            return True
        except OSError as exc:
            logger.debug("刪除目錄失敗（嘗試 %d/%d）: %s", attempt + 1, max_retries, exc)

            # 最後一次嘗試失敗，直接返回 False
            if attempt == max_retries - 1:
                logger.warning("刪除目錄失敗（已重試 %d 次）: %s", max_retries, path)
                return False

            # 檢查是否是 Windows 上的許可權問題
            # 嘗試修改檔案許可權
            if os.name == "nt":
                try:
                    # 嘗試遞迴修改許可權
                    for root, dirs, files in os.walk(path):
                        for name in files + dirs:
                            filepath = Path(root) / name
                            try:
                                # 移除只讀屬性
                                if os.name == "nt":
                                    os.chmod(filepath, stat.S_IWRITE)
                                elif os.name == "posix":
                                    os.chmod(filepath, 0o777)
                                # 對目錄，嘗試刪除其中的檔案
                                if filepath.is_dir():
                                    try:
                                        shutil.rmtree(filepath)
                                    except OSError:
                                        pass  # 忽略子目錄刪除失敗，外層會重試
                                elif filepath.is_file():
                                    try:
                                        os.unlink(filepath)
                                    except PermissionError:
                                        pass  # 忽略檔案刪除失敗
                                # 小延遲
                                time.sleep(0.01)
                            except OSError:
                                pass  # 忽略許可權修改失敗
                except Exception:
                    pass  # 忽略其他異常

            # 等待後重試
            time.sleep(retry_delay)
            retry_delay *= 2

    return False


class SkillManager:
    """Skill 管理器，對應 skills.* 請求方法."""

    def __init__(self, workspace_dir: str | None = None) -> None:
        # 若傳入 workspace_dir（harness adapter 使用），優先透過 Workspace/WorkspaceNode
        # 解析 skills 路徑；否則使用全域性預設路徑（react adapter 或無引數時）。
        if workspace_dir is not None:
            try:
                from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode

                workspace = Workspace(root_path=workspace_dir)
                skills_path = workspace.get_node_path(WorkspaceNode.SKILLS)
                self._skills_dir: Path = (
                    skills_path if skills_path is not None else Path(workspace_dir) / WorkspaceNode.SKILLS.value
                )
            except ImportError:
                self._skills_dir = Path(workspace_dir) / "skills"
            self._agent_root: Path = Path(workspace_dir)
            self._marketplace_dir: Path = self._skills_dir / "_marketplace"
            self._state_file: Path = self._skills_dir / "skills_state.json"
        else:
            self._agent_root = _get_agent_root_dir()
            self._skills_dir = get_agent_skills_dir()
            self._marketplace_dir = _get_marketplace_dir()
            self._state_file = _get_state_file()
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load_state()
        # SkillNet 非同步安裝：install 立即返回 install_id，後臺下載；完成後呼叫 hook 過載 Agent
        self._skillnet_install_jobs: dict[str, dict[str, Any]] = {}
        self._skillnet_install_complete_hook: Callable[[], Awaitable[None]] | None = None

    def set_skillnet_install_complete_hook(self, hook: Callable[[], Awaitable[None]] | None) -> None:
        """安裝成功落盤後回撥（通常為過載 Agent 例項）."""
        self._skillnet_install_complete_hook = hook

    # -----------------------------------------------------------------------
    # 公開 handler
    # -----------------------------------------------------------------------

    async def handle_skills_list(self, params: dict) -> dict:
        """返回所有可用 skill（本地 + marketplace 中未安裝的）.

        params:
            refresh_marketplaces: bool (可選, 預設 False)
                為 True 時，先對已配置 marketplace 執行 clone/pull，再掃描列表。
            with_installed: bool (可選, 預設 False)
                為 True 時，同一次響應中附帶 plugins（與 skills.installed 一致），
                避免閘道器序列處理兩次 RPC 導致列表重新整理超時或排隊過久。
        """
        refresh_marketplaces = bool(params.get("refresh_marketplaces", False))
        if refresh_marketplaces:
            await self._sync_marketplace_repos()
        local = self._scan_local_skills()
        builtin = self._scan_builtin_skills()
        marketplace = self._scan_marketplace_skills()
        out: dict[str, Any] = {"skills": local + builtin + marketplace}
        if bool(params.get("with_installed", False)):
            installed = await self.handle_skills_installed(params)
            out["plugins"] = installed.get("plugins") or []
        return out

    async def handle_skills_installed(self, params: dict) -> dict:
        """返回已安裝的 marketplace 外掛列表.

        按前端期望格式返回：plugin_name, marketplace, spec, version, installed_at, git_commit, skills[]
        """
        raw_plugins = self._get_installed_plugins()
        plugins = []
        for p in raw_plugins:
            name = p.get("name", "")
            marketplace = p.get("marketplace", "")
            # 構造 spec (plugin_name@marketplace_name)
            spec = f"{name}@{marketplace}" if marketplace else name
            # 轉換欄位名以符合前端期望
            plugin = {
                "plugin_name": name,
                "marketplace": marketplace,
                "spec": spec,
                "version": p.get("version", ""),
                "installed_at": p.get("installed_at", ""),
                "git_commit": p.get("commit", ""),
                # skills 陣列：通常一個 plugin 包含同名 skill
                "skills": [name] if name else [],
            }
            plugins.append(plugin)
        return {"plugins": plugins}

    async def handle_skills_get(self, params: dict) -> dict:
        """獲取單個 skill 詳情（name 必填）.

        返回欄位轉換：body -> content, path -> file_path
        """
        name = params.get("name")
        if not name:
            raise ValueError("缺少引數: name")

        # 先在本地 skills 目錄中查詢
        for child in self._skills_dir.iterdir():
            if child.name.startswith("_") or not child.is_dir():
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta and meta.get("name") == name:
                # 欄位轉換以符合前端期望
                meta["content"] = meta.pop("body", "")
                meta["file_path"] = meta.pop("path", "")
                meta["source"] = self._resolve_skill_source(meta.get("name", ""))
                meta["is_builtin"] = self._is_builtin_skill(meta.get("name", ""), self._get_installed_plugins(), child)
                builtin_dir = get_builtin_skills_dir()
                if builtin_dir.exists():
                    builtin_skill_path = builtin_dir / child.name
                    meta["is_builtin_source"] = builtin_skill_path.exists() and builtin_skill_path.is_dir()
                else:
                    meta["is_builtin_source"] = False
                meta["has_evolutions"] = (child / _EVOLUTION_FILENAME).is_file()
                return meta

        # 再在 marketplace 目錄中查詢
        if self._marketplace_dir.exists():
            for repo_dir in self._marketplace_dir.iterdir():
                if not repo_dir.is_dir():
                    continue
                for plugin_dir in repo_dir.iterdir():
                    if not plugin_dir.is_dir():
                        continue
                    md = self._try_find_skill_file(plugin_dir)
                    if md is None:
                        continue
                    meta = self._parse_skill_md(md)
                    if meta and meta.get("name") == name:
                        # 欄位轉換以符合前端期望
                        meta["content"] = meta.pop("body", "")
                        meta["file_path"] = meta.pop("path", "")
                        marketplace_name = repo_dir.name
                        meta["source"] = marketplace_name
                        meta["marketplace"] = marketplace_name
                        meta["is_builtin"] = False
                        meta["is_builtin_source"] = False
                        meta["has_evolutions"] = False
                        return meta

        raise ValueError(f"未找到 skill: {name}")

    async def handle_skills_evolution_status(self, params: dict) -> dict:
        """檢查某個 skill 是否存在 evolutions.json."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少引數: name")
        try:
            name = _safe_path_name(name, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.evolution.status", "skill", name, exc)
            raise ValueError(str(exc)) from exc
        evo_path = self._get_skill_evolution_path(name)
        return {
            "name": name,
            "exists": bool(evo_path and evo_path.is_file()),
        }

    async def handle_skills_evolution_get(self, params: dict) -> dict:
        """獲取某個 skill 的 evolutions.json 內容（重點返回 entries）."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少引數: name")
        try:
            name = _safe_path_name(name, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.evolution.get", "skill", name, exc)
            raise ValueError(str(exc)) from exc

        evo_path = self._get_skill_evolution_path(name)
        if evo_path is None or not evo_path.is_file():
            return {
                "name": name,
                "exists": False,
                "valid": True,
                "skill_id": name,
                "version": "1.0.0",
                "updated_at": "",
                "entries": [],
            }

        try:
            raw = json.loads(evo_path.read_text(encoding="utf-8"))
            evo_file = EvolutionFile.from_dict(raw)
            return {
                "name": name,
                "exists": True,
                "valid": True,
                **evo_file.to_dict(),
            }
        except Exception as exc:
            logger.warning("讀取 evolutions.json 失敗: skill=%s error=%s", name, exc)
            return {
                "name": name,
                "exists": True,
                "valid": False,
                "detail": "evolutions.json 格式錯誤或讀取失敗",
                "skill_id": name,
                "version": "1.0.0",
                "updated_at": "",
                "entries": [],
            }

    async def handle_skills_evolution_save(self, params: dict) -> dict:
        """儲存某個 skill 的 evolutions.json 條目列表."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少引數: name")
        try:
            name = _safe_path_name(name, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.evolution.save", "skill", name, exc)
            raise ValueError(str(exc)) from exc

        if not self._resolve_local_skill_dir(name):
            raise ValueError(f"未找到 skill: {name}")

        entries = params.get("entries")
        if not isinstance(entries, list):
            raise ValueError("引數 entries 必須是陣列")

        normalized_entries: list[EvolutionEntry] = []
        for idx, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"entries[{idx}] 必須是物件")
            entry_id = str(item.get("id") or "").strip()
            content = item.get("change", {}).get("content") if isinstance(item.get("change"), dict) else None
            if not entry_id:
                raise ValueError(f"entries[{idx}].id 不能為空")
            if not isinstance(content, str):
                raise ValueError(f"entries[{idx}].change.content 必須是字串")
            normalized_entries.append(EvolutionEntry.from_dict(item))

        evo_path = self._get_skill_evolution_path(name)
        evo_file = EvolutionFile.empty(skill_id=name)
        if evo_path and evo_path.is_file():
            try:
                current = json.loads(evo_path.read_text(encoding="utf-8"))
                evo_file = EvolutionFile.from_dict(current)
            except Exception as exc:
                logger.warning("讀取原 evolutions.json 失敗，將以新內容覆蓋: skill=%s error=%s", name, exc)

        evo_file.entries = normalized_entries
        evo_file.updated_at = datetime.now(timezone.utc).isoformat()
        if not evo_file.skill_id:
            evo_file.skill_id = name

        if evo_path is None:
            raise ValueError(f"未找到 skill: {name}")
        evo_path.parent.mkdir(parents=True, exist_ok=True)
        evo_path.write_text(
            json.dumps(evo_file.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "success": True,
            "name": name,
            "entry_count": len(evo_file.entries),
            "updated_at": evo_file.updated_at,
        }

    async def handle_skills_marketplace_list(self, params: dict) -> dict:
        """列出已配置的 marketplace 源.

        返回格式符合前端期望：name, url, install_location?, last_updated?
        """
        marketplaces = self._get_marketplaces()
        # 為每個 marketplace 新增前端期望的可選欄位
        result = []
        for m in marketplaces:
            item = {
                "name": m.get("name", ""),
                "url": m.get("url", ""),
                "enabled": bool(m.get("enabled", True)),
                "install_location": m.get("install_location"),
                "last_updated": m.get("last_updated"),
            }
            result.append(item)
        return {"marketplaces": result}

    async def handle_skills_install(self, params: dict) -> dict:
        """安裝 marketplace 中的 skill.

        params:
            spec: "plugin_name@marketplace_name"
            force: bool (可選, 預設 False)
        """
        spec = params.get("spec", "")
        force = params.get("force", False)

        if "@" not in spec:
            return {"success": False, "detail": "spec 格式應為 plugin@marketplace"}

        plugin_name, marketplace_name = spec.rsplit("@", 1)
        if not plugin_name or not marketplace_name:
            return {"success": False, "detail": "plugin 或 marketplace 名稱為空"}
        try:
            plugin_name = _safe_path_name(plugin_name, "plugin")
            marketplace_name = _safe_path_name(marketplace_name, "marketplace")
        except ValueError as exc:
            _log_rejected_name("skills.install", "plugin/marketplace", spec, exc)
            return {"success": False, "detail": str(exc)}

        if marketplace_name == "builtin":
            return await self.handle_skills_install_builtin({"name": plugin_name})

        # 查詢 marketplace 配置
        marketplace = None
        for m in self._get_marketplaces():
            if m.get("name") == marketplace_name:
                marketplace = m
                break
        if marketplace is None:
            return {"success": False, "detail": f"未找到 marketplace: {marketplace_name}"}

        git_url = marketplace.get("url", "")
        if not git_url:
            return {"success": False, "detail": f"marketplace {marketplace_name} 缺少 url"}

        # 確保 marketplace 倉庫已 clone
        repo_dir = _safe_child_path(self._marketplace_dir, marketplace_name, "marketplace")
        if repo_dir.exists():
            await self._git_pull(repo_dir)
        else:
            commit = await self._git_clone(git_url, repo_dir)
            if commit is None:
                return {"success": False, "detail": f"git clone 失敗: {git_url}"}

        # 在倉庫中查詢 plugin 目錄
        plugin_src = repo_dir / "skills" / plugin_name

        # 相容單skill模式
        if not plugin_src.exists() or not plugin_src.is_dir():
            plugin_src = repo_dir
        if not plugin_src.is_dir():
            return {"success": False, "detail": f"在 marketplace 倉庫中未找到 plugin: {plugin_name}"}

        md = self._try_find_skill_file(plugin_src)
        if md is None:
            return {"success": False, "detail": f"plugin {plugin_name} 缺少 SKILL.md"}

        # 複製到本地 skills 目錄
        dest = _safe_child_path(self._skills_dir, plugin_name, "skill")
        if dest.exists():
            if not force:
                return {"success": False, "detail": f"skill {plugin_name} 已存在"}
            _safe_rmtree(dest)
        shutil.copytree(plugin_src, dest)

        # 解析後設資料並記錄（新增 installed_at 時間戳）
        meta = self._parse_skill_md(self._try_find_skill_file(dest)) or {}
        commit_hash = await self._git_get_commit(repo_dir)
        self._add_installed_plugin(
            {
                "name": plugin_name,
                "marketplace": marketplace_name,
                "version": meta.get("version", ""),
                "commit": commit_hash or "",
                "source": marketplace_name,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._refresh_agent_data_indexes()

        return {"success": True}

    async def handle_skills_install_builtin(self, params: dict) -> dict:
        """安裝內建技能.

        params:
            name: skill 名稱
        """
        name = params.get("name", "")
        if not name:
            return {"success": False, "detail": "缺少引數: name"}
        try:
            name = _safe_path_name(name, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.install_builtin", "skill", name, exc)
            return {"success": False, "detail": str(exc)}

        builtin_dir = get_builtin_skills_dir()
        if not builtin_dir.exists():
            return {"success": False, "detail": "內建技能目錄不存在"}

        src = _safe_child_path(builtin_dir, name, "skill")
        if not src.exists() or not src.is_dir():
            return {"success": False, "detail": f"未找到內建技能: {name}"}

        # 檢查是否已經安裝
        dest = _safe_child_path(self._skills_dir, name, "skill")
        if dest.exists() and dest.is_dir():
            return {"success": False, "detail": f"技能 {name} 已經安裝"}

        # 複製技能到使用者目錄
        try:
            shutil.copytree(src, dest)
        except Exception as exc:
            logger.error("安裝內建技能失敗: %s", exc)
            return {"success": False, "detail": f"安裝失敗: {exc}"}

        # 記錄安裝資訊到狀態檔案
        meta = self._parse_skill_md(self._try_find_skill_file(dest)) or {}
        self._add_installed_plugin(
            {
                "name": name,
                "marketplace": "builtin",
                "version": meta.get("version", ""),
                "commit": "",
                "source": "builtin",
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        # 重新整理索引
        self._refresh_agent_data_indexes()

        return {"success": True}

    async def handle_skills_skillnet_search(self, params: dict) -> dict:
        """線上搜尋 SkillNet 技能."""
        query = str(params.get("q", "")).strip()
        if not query:
            return {"success": False, "detail": "缺少引數: q"}

        # 儘量與 SkillNet API 對齊，便於前端透傳。
        search_kwargs: dict[str, Any] = {"q": query}
        if params.get("mode"):
            search_kwargs["mode"] = params.get("mode")
        if params.get("category"):
            search_kwargs["category"] = params.get("category")
        if params.get("limit") is not None:
            try:
                search_kwargs["limit"] = int(params.get("limit"))
            except Exception:
                return {"success": False, "detail": "引數 limit 必須是整數"}
        if params.get("page") is not None:
            try:
                search_kwargs["page"] = int(params.get("page"))
            except Exception:
                return {"success": False, "detail": "引數 page 必須是整數"}
        if params.get("min_stars") is not None:
            try:
                search_kwargs["min_stars"] = int(params.get("min_stars"))
            except Exception:
                return {"success": False, "detail": "引數 min_stars 必須是整數"}
        if params.get("sort_by"):
            search_kwargs["sort_by"] = params.get("sort_by")
        if params.get("threshold") is not None:
            try:
                search_kwargs["threshold"] = float(params.get("threshold"))
            except Exception:
                return {"success": False, "detail": "引數 threshold 必須是數字"}

        try:
            raw_results = await asyncio.to_thread(self._skillnet_search_sync, search_kwargs)
        except Exception as exc:
            logger.error("SkillNet 搜尋失敗: %s", exc)
            raw = str(exc).strip()
            if raw:
                return {"success": False, "detail": raw}
            return {
                "success": False,
                "detail": "搜尋失敗，請稍後重試。",
                "detail_key": "skills.skillNet.errors.searchFailedFallback",
            }

        normalized: list[dict[str, Any]] = []
        for item in raw_results:
            if hasattr(item, "dict"):
                try:
                    item = item.dict()
                except Exception:
                    item = vars(item)
            elif not isinstance(item, dict):
                item = vars(item)

            normalized.append(
                {
                    "skill_name": item.get("skill_name", item.get("name", "")),
                    "skill_description": item.get("skill_description", item.get("description", "")),
                    "author": item.get("author", ""),
                    "stars": item.get("stars", 0),
                    "skill_url": item.get("skill_url", item.get("url", "")),
                    "category": item.get("category", ""),
                }
            )

        return {
            "success": True,
            "query": query,
            "count": len(normalized),
            "skills": normalized,
        }

    async def handle_skills_skillnet_install(self, params: dict) -> dict:
        """從 SkillNet URL 非同步安裝：立即返回 install_id，不阻塞閘道器佇列.

        前端應輪詢 skills.skillnet.install_status 直至 status 為 done/failed。
        """
        skill_url = str(params.get("url", "")).strip()
        force = bool(params.get("force", False))
        if not skill_url:
            return {"success": False, "detail": "缺少引數: url"}

        mirror_url: str | None = None
        raw_mirror = params.get("mirror_url")
        if raw_mirror is not None:
            ms = str(raw_mirror).strip()
            if ms:
                if not _is_valid_http_mirror_url(ms):
                    return {
                        "success": False,
                        "detail": "mirror_url 不是有效的 http(s) 地址",
                        "detail_key": "skills.skillNet.errors.invalidMirrorUrl",
                    }
                mirror_url = ms

        install_id = uuid.uuid4().hex
        self._skillnet_install_jobs[install_id] = {"status": "pending"}
        asyncio.create_task(
            self._skillnet_install_background(install_id, skill_url, force, mirror_url),
            name=f"skillnet_install_{install_id[:8]}",
        )
        return {
            "success": True,
            "pending": True,
            "install_id": install_id,
        }

    async def handle_skills_skillnet_install_status(self, params: dict) -> dict:
        """查詢 SkillNet 非同步安裝狀態."""
        install_id = str(params.get("install_id", "")).strip()
        if not install_id:
            return {"success": False, "detail": "缺少引數: install_id"}
        job = self._skillnet_install_jobs.get(install_id)
        if job is None:
            return {
                "success": False,
                "detail": "安裝會話已過期，請重新點選安裝。",
                "detail_key": "skills.skillNet.errors.sessionExpired",
            }

        status = job.get("status", "pending")
        if status == "pending":
            return {"success": True, "status": "pending"}
        if status == "failed":
            out: dict[str, Any] = {
                "success": False,
                "status": "failed",
                "detail": job.get("detail", "安裝失敗"),
            }
            if "detail_key" in job:
                out["detail_key"] = job["detail_key"]
            if "detail_params" in job:
                out["detail_params"] = job["detail_params"]
            return out
        # done
        return {
            "success": True,
            "status": "done",
            "skill": job.get("skill"),
        }

    async def handle_skills_skillnet_evaluate(self, params: dict) -> dict:
        """使用 skillnet-ai 的 evaluate，LLM 配置複用 react.model_client_config + model_name."""
        skill_url = str(params.get("url", "")).strip()
        if not skill_url:
            return {"success": False, "detail": "缺少引數: url"}

        try:
            out = await asyncio.to_thread(SkillManager._skillnet_evaluate_sync, skill_url)
        except Exception as exc:
            logger.error("SkillNet 評估失敗: %s", exc)
            raw = str(exc).strip()
            return {
                "success": False,
                "detail": raw or "評估失敗，請稍後重試。",
                "detail_key": "skills.skillNet.errors.evaluateFailedFallback",
            }

        if not out.get("ok"):
            detail = str(out.get("detail", "")).strip() or "評估失敗，請稍後重試。"
            resp: dict[str, Any] = {"success": False, "detail": detail}
            if out.get("detail_key"):
                resp["detail_key"] = out["detail_key"]
            return resp

        return {"success": True, "evaluation": out.get("evaluation")}

    async def handle_skills_clawhub_get_token(self, params: dict) -> dict:
        """獲取 ClawHub CLI token（已掩碼）."""
        token = self._get_clawhub_token()
        return {
            "success": True,
            "token": self._mask_clawhub_token(token),
            "has_token": bool(token),
        }

    async def handle_skills_clawhub_set_token(self, params: dict) -> dict:
        """設定 ClawHub CLI token."""
        token = str(params.get("token", "")).strip()
        self._set_clawhub_token(token)
        return {
            "success": True,
            "token": self._mask_clawhub_token(token),
        }

    async def handle_skills_clawhub_search(self, params: dict) -> dict:
        """從 ClawHub 搜尋技能.

        params:
            q: 搜尋查詢字串 (必需)
            limit: 結果數量限制 (可選)
        """
        query = str(params.get("q", "")).strip()
        if not query:
            return {"success": False, "detail": "缺少引數: q"}

        token = self._get_clawhub_token()
        if not token:
            return {
                "success": False,
                "detail": "未配置 ClawHub CLI token，請先配置",
                "detail_key": "skills.clawhub.errors.tokenNotConfigured",
            }

        limit = params.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                return {"success": False, "detail": "引數 limit 必須是整數"}

        try:
            base_url = "https://clawhub.ai"
            search_url = f"{base_url}/api/v1/search"

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            search_params = {"q": query}
            if limit is not None:
                search_params["limit"] = limit

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    search_url,
                    params=search_params,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                normalized = []
                for item in results:
                    normalized.append(
                        {
                            "slug": item.get("slug", ""),
                            "display_name": item.get("displayName", ""),
                            "summary": item.get("summary", ""),
                            "version": item.get("version", ""),
                            "updated_at": item.get("updatedAt", 0),
                        }
                    )

                return {
                    "success": True,
                    "query": query,
                    "count": len(normalized),
                    "skills": normalized,
                }
        except httpx.HTTPStatusError as exc:
            logger.error("ClawHub 搜尋 HTTP 錯誤: %s", exc)
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return {
                "success": False,
                "detail": detail,
                "detail_key": "skills.clawhub.errors.httpError",
            }
        except Exception as exc:
            logger.error("ClawHub 搜尋失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.clawhub.errors.searchFailed",
            }

    async def handle_skills_clawhub_download(self, params: dict) -> dict:
        """從 ClawHub 下載技能.

        params:
            slug: skill slug (必需)
            version: 版本號 (可選，預設 latest)
            tag: 標籤 (可選，如 latest)
            force: 強制覆蓋 (可選，預設 False)
        """
        slug = str(params.get("slug", "")).strip()
        if not slug:
            return {"success": False, "detail": "缺少引數: slug"}
        try:
            slug = _safe_path_name(slug, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.clawhub.download", "skill", slug, exc)
            return {"success": False, "detail": str(exc)}

        token = self._get_clawhub_token()
        if not token:
            return {
                "success": False,
                "detail": "未配置 ClawHub CLI token，請先配置",
                "detail_key": "skills.clawhub.errors.tokenNotConfigured",
            }

        version = params.get("version")
        tag = params.get("tag")
        force = bool(params.get("force", False))

        # 檢查 skill 是否已安裝
        dest = _safe_child_path(self._skills_dir, slug, "skill")
        if dest.exists() and not force:
            return {
                "success": False,
                "detail": f"技能 {slug} 已安裝",
                "detail_key": "skills.clawhub.errors.skillAlreadyInstalled",
            }

        try:
            base_url = "https://clawhub.ai"
            download_url = f"{base_url}/api/v1/download"

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            download_params = {"slug": slug}
            if version:
                download_params["version"] = version
            if tag:
                download_params["tag"] = tag

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    download_url,
                    params=download_params,
                    headers=headers,
                )
                response.raise_for_status()

                # 解壓下載的內容
                with tempfile.TemporaryDirectory(prefix="jiuwenclari_clawhub_") as tmpdir:
                    tmp_path = Path(tmpdir)

                    # 儲存 zip 檔案
                    zip_content = io.BytesIO(response.content)
                    with zipfile.ZipFile(zip_content, "r") as zip_ref:
                        zip_ref.extractall(tmp_path)

                    # 查詢 skill 目錄
                    skill_dir = self._locate_skill_dir(tmp_path)
                    if skill_dir is None:
                        return {
                            "success": False,
                            "detail": "下載內容不完整，未找到 SKILL.md",
                            "detail_key": "skills.clawhub.errors.skillMdNotFound",
                        }

                    # 解析後設資料
                    md = self._try_find_skill_file(skill_dir)
                    meta = self._parse_skill_md(md) if md else None
                    if meta is None:
                        return {
                            "success": False,
                            "detail": "無法解析下載的技能檔案",
                            "detail_key": "skills.clawhub.errors.parseSkillFailed",
                        }

                    # 刪除已存在的
                    if dest.exists():
                        if not force:
                            return {
                                "success": False,
                                "detail": f"技能 {slug} 已安裝",
                                "detail_key": "skills.clawhub.errors.skillAlreadyInstalled",
                            }
                        _safe_rmtree(dest)

                    # 複製到 skills 目錄
                    shutil.copytree(skill_dir, dest)
                    for mirror_root in self._get_mirror_skills_dirs():
                        mirror_dest = _safe_child_path(mirror_root, slug, "skill")
                        if mirror_dest.exists():
                            if not force:
                                continue
                            _safe_rmtree(mirror_dest)
                        mirror_root.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(skill_dir, mirror_dest)

                    # 記錄安裝資訊
                    skill_name = meta.get("name", slug)
                    self._add_local_skill(
                        {
                            "name": skill_name,
                            "origin": f"clawhub:{slug}",
                            "source": "clawhub",
                            "installed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    self._add_installed_plugin(
                        {
                            "name": skill_name,
                            "marketplace": "clawhub",
                            "version": meta.get("version", ""),
                            "commit": "",
                            "source": "clawhub",
                            "installed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    self._refresh_agent_data_indexes()
                    _safe_rmtree(skill_dir)
                    return {
                        "success": True,
                        "skill": {"name": skill_name, "source": "clawhub"},
                    }

        except httpx.HTTPStatusError as exc:
            logger.error("ClawHub 下載 HTTP 錯誤: %s", exc)
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return {
                "success": False,
                "detail": detail,
                "detail_key": "skills.clawhub.errors.httpError",
            }
        except Exception as exc:
            logger.error("ClawHub 下載失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.clawhub.errors.downloadFailed",
            }

    async def handle_skills_team_skills_hub_init(self, params: dict) -> dict:
        """初始化 TeamSkills 模板目錄（最小可用腳手架）。"""
        name_raw = str(params.get("name") or "").strip()
        if not name_raw:
            return {"success": False, "detail": "缺少引數: name"}
        try:
            skill_name = _safe_path_name(name_raw, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.teamskillshub.init", "skill", name_raw, exc)
            return {"success": False, "detail": str(exc)}

        parent_raw = str(params.get("path") or ".").strip() or "."
        skill_type = str(
            params.get("skill_type")
            or params.get("plugin_type")
            or params.get("type")
            or "teamskills"
        ).strip().lower()
        if skill_type not in {"teamskills", "skill"}:
            return {"success": False, "detail": "type 僅支援: teamskills 或 skill"}
        force = bool(params.get("force", False))
        parent_dir = Path(parent_raw).expanduser().resolve()
        if not parent_dir.exists() or not parent_dir.is_dir():
            return {"success": False, "detail": f"path 不是有效目錄: {parent_dir}"}

        target_dir = parent_dir / skill_name
        try:
            if target_dir.exists():
                if not target_dir.is_dir():
                    return {"success": False, "detail": f"目標路徑不是目錄: {target_dir}"}
                if any(target_dir.iterdir()):
                    if not force:
                        return {"success": False, "detail": f"目標目錄非空: {target_dir}"}
                    _safe_rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            skill_file = target_dir / "SKILL.md"
            if skill_file.exists() and not force:
                return {"success": False, "detail": f"檔案已存在: {skill_file}"}
            if skill_type == "teamskills":
                content = (
                    "---\n"
                    f"name: {skill_name}\n"
                    'description: "TODO: describe this team skill."\n'
                    "kind: team-skill\n"
                    "roles:\n"
                    "  - id: role_01\n"
                    "    purpose: role_01_purpose\n"
                    "  - id: role_02\n"
                    "    purpose: role_02_purpose\n"
                    "---\n\n"
                    f"# {skill_name}\n\n"
                    "## Instructions\n\n"
                    "TODO: add step-by-step guidance.\n"
                )
            else:
                content = (
                    "---\n"
                    f"name: {skill_name}\n"
                    'description: "TODO: describe this skill."\n'
                    "---\n\n"
                    f"# {skill_name}\n\n"
                    "## Instructions\n\n"
                    "TODO: add step-by-step guidance.\n"
                )

            skill_file.write_text(content, encoding="utf-8")
            self._refresh_agent_data_indexes()
            return {"success": True, "path": str(target_dir)}
        except Exception as exc:
            logger.error("Team Skills Hub init 失敗: %s", exc)
            return {"success": False, "detail": str(exc)[:500]}

    async def handle_skills_team_skills_hub_validate(self, params: dict) -> dict:
        """校驗 TeamSkills 目錄結構與 SKILL.md 內容。"""
        path_raw = str(params.get("path") or "").strip()
        if not path_raw:
            return {"success": False, "detail": "缺少引數: path"}
        skill_root = Path(path_raw).expanduser().resolve()
        if not skill_root.exists() or not skill_root.is_dir():
            return {"success": False, "detail": f"path 不是有效目錄: {skill_root}"}
        skill_md = self._try_find_skill_file(skill_root)
        if skill_md is None:
            return {"success": False, "detail": f"目錄中未找到 SKILL.md: {skill_root}"}
        meta = self._parse_skill_md(skill_md)
        if meta is None:
            return {"success": False, "detail": f"無法解析 SKILL.md: {skill_md}"}

        skill_type = str(
            params.get("skill_type") or params.get("plugin_type") or params.get("type") or ""
        ).strip().lower()
        if skill_type not in {"teamskills", "skill"}:
            skill_type = "teamskills" if str(meta.get("kind", "")).strip() == "team-skill" else "skill"

        errors: list[str] = []
        if skill_type == "teamskills":
            roles = meta.get("roles", [])
            if not isinstance(roles, list) or not roles:
                errors.append("frontmatter `roles` must be a non-empty list")
            else:
                role_ids: list[str] = []
                for i, role in enumerate(roles):
                    if not isinstance(role, dict):
                        errors.append(f"roles[{i}] must be an object")
                        continue
                    if "id" not in role:
                        errors.append(f"roles[{i}] missing required field `id`")
                        continue
                    role_id = role.get("id")
                    if not isinstance(role_id, str) or not role_id.strip():
                        errors.append(f"roles[{i}] `id` must be a non-empty string")
                    else:
                        role_ids.append(role_id.strip())

                if len(role_ids) < 2:
                    errors.append(
                        "frontmatter `roles` must list at least 2 entries "
                        "with valid `id` (team-skill multi-role contract)."
                    )
                elif len(role_ids) != len(set(role_ids)):
                    errors.append("frontmatter `roles` must not repeat the same `id`")

        if errors:
            return {
                "success": False,
                "detail": "TeamSkills roles 校驗失敗" if skill_type == "teamskills" else "校驗失敗",
                "errors": errors,
            }

        return {
            "success": True,
            "path": str(skill_root),
            "skill_file": str(skill_md),
            "skill_type": skill_type,
            "name": str(meta.get("name", "")).strip(),
            "warnings": [],
        }

    async def handle_skills_team_skills_hub_pack(self, params: dict) -> dict:
        """將 TeamSkills 目錄打包為 zip。"""
        path_raw = str(params.get("path") or "").strip()
        if not path_raw:
            return {"success": False, "detail": "缺少引數: path"}
        skill_root = Path(path_raw).expanduser().resolve()
        if not skill_root.exists() or not skill_root.is_dir():
            return {"success": False, "detail": f"path 不是有效目錄: {skill_root}"}
        skill_md = self._try_find_skill_file(skill_root)
        if skill_md is None:
            return {"success": False, "detail": f"目錄中未找到 SKILL.md: {skill_root}"}

        output_raw = str(params.get("output") or "out").strip() or "out"
        output_path = Path(output_raw).expanduser()
        if output_path.is_absolute():
            out_dir = output_path.resolve()
        else:
            out_dir = (skill_root / output_path).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        zip_path = out_dir / f"{skill_root.name}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for child in skill_root.rglob("*"):
                    if not child.is_file():
                        continue
                    rel = child.relative_to(skill_root).as_posix()
                    zf.write(child, arcname=rel)
            return {"success": True, "path": str(zip_path)}
        except Exception as exc:
            logger.error("Team Skills Hub pack 失敗: %s", exc)
            return {"success": False, "detail": str(exc)[:500]}

    async def handle_skills_team_skills_hub_info(self, params: dict) -> dict:
        """查詢 Team Skills Hub 技能版本詳情（/api/v1/artifacts/{asset_id}）。"""
        asset_id = str(params.get("asset_id", "")).strip()
        if not asset_id:
            return {"success": False, "detail": "缺少引數: asset_id"}
        version = str(params.get("version", "")).strip()
        if not version:
            return {"success": False, "detail": "缺少引數: version"}
        base_url = self._get_team_skills_hub_base_url(str(params.get("market_url") or "").strip() or None)
        try:
            detail = await self._team_skills_hub_http_get_data(
                f"/api/v1/artifacts/{asset_id}",
                params={"version": version},
                timeout=_TEAM_SKILLS_HUB_MARKET_TIMEOUT,
                base_url=base_url,
            )
            if not isinstance(detail, dict):
                return {"success": False, "detail": "marketplace 返回資料格式錯誤"}
            return {"success": True, "asset_id": asset_id, "version": version, "data": detail}
        except Exception as exc:
            logger.error("Team Skills Hub 詳情查詢失敗: %s", exc)
            return {"success": False, "detail": str(exc)[:500]}

    async def handle_skills_team_skills_hub_search(self, params: dict) -> dict:
        """從 Team Skills Hub 搜尋技能（/api/v1/plugins）。"""
        query = str(params.get("q", "")).strip()

        page_size_raw = params.get("page_size", params.get("limit", 20))
        try:
            page_size = max(1, min(int(page_size_raw), 100))
        except Exception:
            return {"success": False, "detail": "引數 page_size 必須是整數"}

        page = params.get("page", 1)
        try:
            page = max(1, int(page))
        except Exception:
            return {"success": False, "detail": "引數 page 必須是整數"}

        skill_type = str(params.get("skill_type") or params.get("plugin_type") or "").strip()
        author = str(params.get("author", "")).strip()
        search_asset_id = str(params.get("search_asset_id", "")).strip()
        search_asset_type = str(params.get("search_asset_type", "")).strip()
        search_publisher_id = str(params.get("search_publisher_id", "")).strip()
        order_by = str(params.get("order_by", "install_count")).strip() or "install_count"
        desc_raw = params.get("desc", True)
        desc = str(desc_raw).strip().lower() in {"1", "true", "yes", "on"}
        base_url = self._get_team_skills_hub_base_url(str(params.get("market_url") or "").strip() or None)

        try:
            query_params: dict[str, Any] = {
                "page": page,
                "page_size": page_size,
                "order_by": order_by,
                "desc": str(desc).lower(),
            }
            if query:
                query_params["search_keyword"] = query
            if skill_type:
                query_params["plugin_type"] = skill_type
            if author:
                query_params["publisher_name"] = author
            if search_asset_id:
                query_params["asset_id"] = search_asset_id
            if search_asset_type:
                query_params["asset_type"] = search_asset_type
            if search_publisher_id:
                query_params["publisher_id"] = search_publisher_id
            data = await self._team_skills_hub_http_get_data(
                "/api/v1/plugins",
                params=query_params,
                timeout=_TEAM_SKILLS_HUB_MARKET_TIMEOUT,
                base_url=base_url,
            )
            items = data.get("items", []) if isinstance(data, dict) else []
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                asset_id = str(item.get("asset_id", "")).strip()
                name = str(item.get("name", "")).strip() or asset_id
                normalized.append(
                    {
                        "asset_id": asset_id,
                        "name": name,
                        "display_name": str(item.get("display_name", "")).strip() or name,
                        "summary": str(item.get("short_desc", "")).strip(),
                        "version": str(item.get("latest_version", "")).strip(),
                        "updated_at": int(item.get("update_time") or 0),
                    }
                )
            return {
                "success": True,
                "query": query,
                "count": len(normalized),
                "skills": normalized,
            }
        except Exception as exc:
            logger.error("Team Skills Hub 搜尋失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.teamskillshub.errors.searchFailed",
            }

    async def handle_skills_team_skills_hub_install(self, params: dict) -> dict:
        """從 Team Skills Hub 安裝技能（/api/v1/artifacts/{asset_id}）。"""
        asset_id = str(params.get("asset_id", "")).strip()
        if not asset_id:
            return {"success": False, "detail": "缺少引數: asset_id"}

        force = bool(params.get("force", False))
        version = params.get("version")
        version_str = str(version).strip() if version is not None else ""

        try:
            base_url = self._get_team_skills_hub_base_url(str(params.get("market_url") or "").strip() or None)
            artifact_data = await self._team_skills_hub_http_get_data(
                f"/api/v1/artifacts/{asset_id}",
                params={"version": version_str} if version_str else None,
                timeout=_TEAM_SKILLS_HUB_MARKET_TIMEOUT,
                base_url=base_url,
            )
            if not isinstance(artifact_data, dict):
                return {"success": False, "detail": "marketplace 返回資料格式錯誤"}

            download_url = str(artifact_data.get("download_url", "")).strip()
            if not download_url:
                return {"success": False, "detail": "marketplace 未返回 download_url"}
            self._assert_team_skills_hub_download_url_allowed(download_url)
            checksum_sha256 = str(artifact_data.get("checksum_sha256", "")).strip()

            artifact_bytes = await self._download_zip_and_verify(download_url, checksum_sha256=checksum_sha256)

            with tempfile.TemporaryDirectory(prefix="jiuwenclaw_team_skills_hub_") as tmpdir:
                tmp_path = Path(tmpdir)
                # 與 ClawHub 一致：從記憶體解壓，避免在臨時目錄寫入 skill.zip（扁平包時 copytree 曾誤拷入安裝目錄）。
                self._safe_extract_zip_bytes_to_dir(artifact_bytes, tmp_path)

                skill_dir = self._locate_skill_dir(tmp_path)
                if skill_dir is None:
                    return {"success": False, "detail": "下載內容不完整，未找到 SKILL.md"}

                md = self._try_find_skill_file(skill_dir)
                meta = self._parse_skill_md(md) if md else None
                if meta is None:
                    return {"success": False, "detail": "無法解析下載的技能檔案"}

                skill_name = str(meta.get("name", "")).strip() or asset_id
                output_raw = str(params.get("output", "")).strip()
                use_custom_output = bool(output_raw)
                install_root = Path(output_raw).expanduser().resolve() if use_custom_output else self._skills_dir
                install_root.mkdir(parents=True, exist_ok=True)
                dest = install_root / skill_name
                if dest.exists():
                    if not force:
                        return {"success": False, "detail": f"技能 {skill_name} 已安裝"}
                    _safe_rmtree(dest)

                shutil.copytree(skill_dir, dest)
                if use_custom_output:
                    return {
                        "success": True,
                        "skill": {
                            "name": skill_name,
                            "source": "teamskillshub",
                            "asset_id": asset_id,
                            "path": str(dest),
                        },
                    }
                for mirror_root in self._get_mirror_skills_dirs():
                    mirror_dest = mirror_root / skill_name
                    if mirror_dest.exists():
                        if not force:
                            continue
                        _safe_rmtree(mirror_dest)
                    mirror_root.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, mirror_dest)

                installed_at = datetime.now(timezone.utc).isoformat()
                self._add_local_skill(
                    {
                        "name": skill_name,
                        "origin": f"teamskillshub:{asset_id}",
                        "source": "teamskillshub",
                        "installed_at": installed_at,
                    }
                )
                self._add_installed_plugin(
                    {
                        "name": skill_name,
                        "marketplace": "teamskillshub",
                        "version": str(meta.get("version", "")).strip()
                        or str(artifact_data.get("version", "")).strip(),
                        "commit": "",
                        "source": "teamskillshub",
                        "installed_at": installed_at,
                    }
                )
                self._refresh_agent_data_indexes()
                return {
                    "success": True,
                    "skill": {
                        "name": skill_name,
                        "source": "teamskillshub",
                        "asset_id": asset_id,
                        "path": str(dest),
                    },
                }
        except Exception as exc:
            logger.error("Team Skills Hub 安裝失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.teamskillshub.errors.installFailed",
            }

    async def handle_skills_team_skills_hub_publish(self, params: dict) -> dict:
        """釋出 TeamSkills（對齊 jiuwen-teamskills 的 /api/v1/plugins 協議）。"""
        auth = self._resolve_teamskills_hub_auth(params)
        if auth.get("error"):
            return {"success": False, "detail": str(auth["error"])}

        plugin_version = str(params.get("version") or "").strip()
        if not plugin_version:
            return {"success": False, "detail": "缺少引數: version"}

        plugin_id = str(params.get("skill_id") or "").strip() or None
        version_desc = str(params.get("version_desc") or "").strip()
        force = bool(params.get("force", False))
        base_url = self._get_team_skills_hub_base_url(str(params.get("market_url") or "").strip() or None)

        path_raw = str(params.get("path") or "").strip()
        file_raw = str(params.get("file") or "").strip()
        if not path_raw and not file_raw:
            return {"success": False, "detail": "缺少引數: path 或 file"}

        try:
            with tempfile.TemporaryDirectory(prefix="jiuwenclaw_teamskills_publish_") as tmpdir:
                zip_path = self._prepare_teamskills_publish_zip(
                    path_raw=path_raw,
                    file_raw=file_raw,
                    plugin_version=plugin_version,
                    tmpdir=Path(tmpdir),
                )
                checksum_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
                response_data = await self._teamskills_hub_publish_request(
                    base_url=base_url,
                    zip_path=zip_path,
                    checksum_sha256=checksum_sha256,
                    plugin_id=plugin_id,
                    plugin_version=plugin_version,
                    version_desc=version_desc,
                    force=force,
                    token=auth.get("token"),
                    system_token=auth.get("system_token"),
                )
                skill_id = str(
                    response_data.get("asset_id")
                    or response_data.get("plugin_id")
                    or plugin_id
                    or ""
                ).strip()
                name = str(response_data.get("name") or "").strip()
                version = str(response_data.get("version") or plugin_version).strip() or plugin_version
                return {"success": True, "skill_id": skill_id, "name": name, "version": version}
        except Exception as exc:
            logger.error("Team Skills Hub 釋出失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.teamskillshub.errors.publishFailed",
            }

    async def handle_skills_team_skills_hub_delete(self, params: dict) -> dict:
        """刪除 TeamSkills（對齊 jiuwen-teamskills 的 DELETE /api/v1/plugins/...）。"""
        skill_id = str(params.get("skill_id") or "").strip()
        if not skill_id:
            return {"success": False, "detail": "缺少引數: skill_id"}

        auth = self._resolve_teamskills_hub_auth(params)
        if auth.get("error"):
            return {"success": False, "detail": str(auth["error"])}

        version = str(params.get("version") or "all").strip() or "all"
        base_url = self._get_team_skills_hub_base_url(str(params.get("market_url") or "").strip() or None)
        try:
            await self._teamskills_hub_delete_request(
                base_url=base_url,
                skill_id=skill_id,
                version=version,
                token=auth.get("token"),
                system_token=auth.get("system_token"),
            )
            return {"success": True, "skill_id": skill_id, "version": version}
        except Exception as exc:
            logger.error("Team Skills Hub 刪除失敗: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.teamskillshub.errors.deleteFailed",
            }

    async def _skillnet_install_background(
        self,
        install_id: str,
        skill_url: str,
        force: bool,
        mirror_url: str | None = None,
    ) -> None:
        try:
            result = await asyncio.to_thread(self._skillnet_install_files_sync, skill_url, force, mirror_url)
        except Exception as exc:
            logger.error("SkillNet 後臺安裝異常: %s", exc)
            raw = str(exc).strip()
            self._skillnet_install_jobs[install_id] = {
                "status": "failed",
                "detail": raw or "安裝失敗，請重試。",
                **(
                    {}
                    if raw
                    else {
                        "detail_key": "skills.skillNet.errors.installFailedFallback",
                    }
                ),
            }
            return

        if not result.get("ok"):
            job_entry: dict[str, Any] = {
                "status": "failed",
                "detail": result.get("detail", "安裝失敗，請重試。"),
            }
            if result.get("detail_key"):
                job_entry["detail_key"] = result["detail_key"]
            if result.get("detail_params") is not None:
                job_entry["detail_params"] = result["detail_params"]
            self._skillnet_install_jobs[install_id] = job_entry
            return

        skill_name = result["skill_name"]
        meta = result["meta"]
        skill_url_stored = result["skill_url"]
        try:
            self._add_local_skill(
                {
                    "name": skill_name,
                    "origin": skill_url_stored,
                    "source": "skillnet",
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._add_installed_plugin(
                {
                    "name": skill_name,
                    "marketplace": "skillnet",
                    "version": meta.get("version", ""),
                    "commit": "",
                    "source": "skillnet",
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._refresh_agent_data_indexes()
        except Exception as exc:
            logger.error("SkillNet 寫入狀態失敗: %s", exc)
            self._skillnet_install_jobs[install_id] = {
                "status": "failed",
                "detail": "安裝完成但儲存配置失敗，請重新整理頁面重試。",
                "detail_key": "skills.skillNet.errors.saveConfigFailed",
            }
            return

        hook = self._skillnet_install_complete_hook
        if hook is not None:
            try:
                await hook()
            except Exception as exc:
                logger.error("SkillNet 安裝完成後 hook 失敗: %s", exc)
                self._skillnet_install_jobs[install_id] = {
                    "status": "failed",
                    "detail": "技能已安裝，請手動重新整理頁面生效。",
                    "detail_key": "skills.skillNet.errors.reloadRequired",
                }
                return

        self._skillnet_install_jobs[install_id] = {
            "status": "done",
            "skill": {"name": skill_name, "source": "skillnet"},
        }

    def _skillnet_install_files_sync(
        self, skill_url: str, force: bool, mirror_url: str | None = None
    ) -> dict[str, Any]:
        """在工作執行緒中下載並複製到 skills 目錄；返回 ok / skill_name / meta / skill_url."""
        try:
            with tempfile.TemporaryDirectory(prefix="jiuwenclaw_skillnet_") as tmpdir:
                tmp_path = Path(tmpdir)
                download_path_str = self._skillnet_download_sync(skill_url, str(tmp_path), mirror_url)
                download_path = Path(download_path_str).resolve()
                if not download_path.exists():
                    return {
                        "ok": False,
                        "detail": "下載失敗，請重試。",
                        "detail_key": "skills.skillNet.errors.downloadFailed",
                    }

                # 庫在部分檔案下載失敗時仍會返回路徑，只有找到 SKILL.md 才視為下載完整，才繼續後續邏輯
                skill_dir = self._locate_skill_dir(download_path)
                if skill_dir is None:
                    return {
                        "ok": False,
                        "detail": "下載未完成或內容不完整，未找到 SKILL.md，請重試。",
                        "detail_key": "skills.skillNet.errors.skillMdNotFound",
                    }

                md = self._try_find_skill_file(skill_dir)
                meta = self._parse_skill_md(md) if md else None
                if meta is None:
                    return {
                        "ok": False,
                        "detail": "無法解析下載的技能檔案",
                        "detail_key": "skills.skillNet.errors.parseSkillFailed",
                    }

                raw_skill_name = meta.get("name", skill_dir.name)
                try:
                    skill_name = _safe_path_name(raw_skill_name, "skill")
                except ValueError as exc:
                    _log_rejected_name("skills.skillnet.install", "skill", raw_skill_name, exc)
                    return {"ok": False, "detail": str(exc)}
                dest = _safe_child_path(self._skills_dir, skill_name, "skill")
                if dest.exists():
                    if not force:
                        return {
                            "ok": False,
                            "detail": "該技能已安裝。",
                            "detail_key": "skills.skillNet.errors.skillAlreadyInstalled",
                        }
                    _safe_rmtree(dest)

                shutil.copytree(skill_dir, dest)
                for mirror_root in self._get_mirror_skills_dirs():
                    mirror_dest = _safe_child_path(mirror_root, skill_name, "skill")
                    if mirror_dest.exists():
                        if not force:
                            continue
                        _safe_rmtree(mirror_dest)
                    mirror_root.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, mirror_dest)
                _safe_rmtree(skill_dir)
                return {
                    "ok": True,
                    "skill_name": skill_name,
                    "meta": meta,
                    "skill_url": skill_url,
                }
        except SkillNetEmptyDownloadError as exc:
            logger.error("SkillNet 下載失敗: %s", exc)
            out: dict[str, Any] = {
                "ok": False,
                "detail_key": exc.detail_key,
                "detail": "",
            }
            out["detail_params"] = exc.detail_params
            return out
        except Exception as exc:
            logger.error("SkillNet 下載失敗: %s", exc)
            raw = str(exc).strip()
            detail = raw or "安裝失敗，請重試。"
            extra: dict[str, Any] = {}
            if not raw:
                extra["detail_key"] = "skills.skillNet.errors.installFailedFallback"
            return {"ok": False, "detail": detail, **extra}

    async def handle_skills_uninstall(self, params: dict) -> dict:
        """解除安裝已安裝的 skill.

        params:
            name: skill 名稱
        """
        name = params.get("name", "")
        if not name:
            return {"success": False, "detail": "缺少引數: name"}
        try:
            name = _safe_path_name(name, "skill")
        except ValueError as exc:
            _log_rejected_name("skills.uninstall", "skill", name, exc)
            return {"success": False, "detail": str(exc)}

        # 使用 _resolve_local_skill_dir 正確解析技能目錄（處理 name 與資料夾名稱不一致的情況）
        dest = self._resolve_local_skill_dir(name)
        if dest is None:
            return {"success": False, "detail": f"未找到 skill: {name}"}

        # 檢查是否為真正的內建技能（原始碼目錄中的，不允許刪除）
        builtin_dir = get_builtin_skills_dir()
        if builtin_dir.exists():
            # 檢查 builtin 技能目錄中是否有該技能
            builtin_skill_path = None
            direct_builtin = _safe_child_path(builtin_dir, name, "skill")
            if direct_builtin.exists() and direct_builtin.is_dir():
                builtin_skill_path = direct_builtin
            else:
                # 遍歷 builtin 目錄，透過解析 SKILL.md 查詢匹配的技能
                for child in builtin_dir.iterdir():
                    if not child.is_dir() or child.name.startswith("_"):
                        continue
                    md = self._try_find_skill_file(child)
                    if md is None:
                        continue
                    meta = self._parse_skill_md(md)
                    if meta and meta.get("name") == name:
                        builtin_skill_path = child
                        break

            if builtin_skill_path:
                if dest.resolve() == builtin_skill_path.resolve():
                    return {"success": False, "detail": "內建技能不允許刪除"}

        _safe_rmtree(dest)

        # 處理 mirror 根目錄中的技能
        for mirror_root in self._get_mirror_skills_dirs():
            mirror_dest = _safe_child_path(mirror_root, dest.name, "skill")
            if mirror_dest.exists() and mirror_dest.is_dir():
                _safe_rmtree(mirror_dest)

        self._remove_installed_plugin(name)
        self._remove_local_skill(name)
        self._refresh_agent_data_indexes()
        return {"success": True}

    async def handle_skills_import_local(self, params: dict) -> dict:
        """從本地路徑或遠端歸檔 URL 匯入 skill."""
        raw_path = params.get("path", "")
        force = bool(params.get("force", False))
        checksum_sha256 = str(params.get("checksum_sha256", "") or "").strip()
        logger.info(
            "[SkillManager] import_local called: path=%r force=%s remote=%s",
            raw_path,
            force,
            self._is_http_download_target(str(raw_path).strip()),
        )
        if not raw_path:
            return {"success": False, "detail": "缺少引數: path"}

        remote_url = str(raw_path).strip()
        if self._is_http_download_target(remote_url):
            try:
                return await self._import_skill_from_remote_archive(
                    download_url=remote_url,
                    force=force,
                    checksum_sha256=checksum_sha256,
                )
            except Exception as exc:
                logger.error("remote archive import failed: %s", exc)
                return {"success": False, "detail": str(exc)[:500]}

        return self._import_local_from_path(Path(raw_path), force=force, origin=str(raw_path))

    def _import_local_from_path(self, src: Path, *, force: bool, origin: str) -> dict[str, Any]:
        logger.info(
            "[SkillManager] import_local_from_path start: src=%s origin=%s force=%s",
            src,
            origin,
            force,
        )
        if not src.exists():
            return {"success": False, "detail": f"路徑不存在: {origin}"}

        if src.is_file():
            meta = self._parse_skill_md(src)
            if meta is None:
                return {"success": False, "detail": "無法解析 skill 檔案"}
            raw_skill_name = meta.get("name", src.stem)
            try:
                skill_name = _safe_path_name(raw_skill_name, "skill")
            except ValueError as exc:
                _log_rejected_name("skills.import_local", "skill", raw_skill_name, exc)
                return {"success": False, "detail": str(exc)}
            dest = _safe_child_path(self._skills_dir, skill_name, "skill")
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在"}
                _safe_rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
        elif src.is_dir():
            md = self._try_find_skill_file(src)
            if md is None:
                return {"success": False, "detail": f"目錄中未找到 SKILL.md: {origin}"}
            meta = self._parse_skill_md(md) or {}
            raw_skill_name = meta.get("name", src.name)
            try:
                skill_name = _safe_path_name(raw_skill_name, "skill")
            except ValueError as exc:
                _log_rejected_name("skills.import_local", "skill", raw_skill_name, exc)
                return {"success": False, "detail": str(exc)}
            dest = _safe_child_path(self._skills_dir, skill_name, "skill")
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在"}
                _safe_rmtree(dest)
            shutil.copytree(src, dest)
        else:
            return {"success": False, "detail": f"不支援的路徑型別: {origin}"}

        self._add_local_skill({"name": skill_name, "origin": origin, "source": "local"})
        self._refresh_agent_data_indexes()
        logger.info(
            "[SkillManager] import_local_from_path done: skill_name=%s origin=%s dest=%s",
            skill_name,
            origin,
            self._skills_dir / skill_name,
        )
        return {"success": True, "skill": {"name": skill_name}}

    async def _import_skill_from_remote_archive(
        self,
        *,
        download_url: str,
        force: bool,
        checksum_sha256: str = "",
    ) -> dict[str, Any]:
        """Download archive by URL, extract by type, then reuse local import flow."""
        self._assert_import_local_download_url_allowed(download_url)
        timeout = max(30.0, _IMPORT_LOCAL_REMOTE_TIMEOUT)
        logger.info(
            "[SkillManager] remote import start: url=%s force=%s timeout=%s",
            download_url,
            force,
            timeout,
        )

        def _download_with_requests() -> bytes:
            with requests.Session() as session:
                session.mount("https://", _ImportLocalTLSAdapter())
                logger.info("[SkillManager] remote import downloading: url=%s", download_url)
                with session.get(
                    download_url.strip(),
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                    verify=False,
                ) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            chunks.append(chunk)
                    body = b"".join(chunks)
            logger.info(
                "[SkillManager] remote import downloaded: url=%s bytes=%s",
                download_url,
                len(body),
            )
            if not body:
                raise RuntimeError("下載內容為空")

            expected = checksum_sha256.strip().lower()
            if expected:
                digest = hashlib.sha256(body).hexdigest().lower()
                if digest != expected:
                    raise RuntimeError("下載檔案校驗失敗（SHA256 不匹配）")
            return body

        artifact_bytes = _download_with_requests()

        with tempfile.TemporaryDirectory(prefix="jiuwenclaw_import_local_") as tmpdir:
            tmp_path = Path(tmpdir)
            logger.info("[SkillManager] remote import extracting: url=%s tmpdir=%s", download_url, tmp_path)
            self._extract_archive_bytes_to_dir(artifact_bytes, tmp_path)
            skill_dir = self._locate_skill_dir(tmp_path)
            if skill_dir is None:
                return {"success": False, "detail": "下載內容不完整，未找到 SKILL.md"}
            logger.info("[SkillManager] remote import extracted: url=%s skill_dir=%s", download_url, skill_dir)
            return self._import_local_from_path(skill_dir, force=force, origin=download_url)


    async def handle_skills_marketplace_add(self, params: dict) -> dict:
        """新增 marketplace 源.

        params:
            name: marketplace 名稱
            url: git 倉庫 URL
        """
        name = params.get("name", "")
        url = params.get("url", "")
        if not name or not url:
            return {"success": False, "detail": "缺少引數: name 和 url"}
        try:
            name = _safe_path_name(name, "marketplace")
        except ValueError as exc:
            _log_rejected_name("skills.marketplace.add", "marketplace", name, exc)
            return {"success": False, "detail": str(exc)}

        # 檢查是否已存在
        for m in self._get_marketplaces():
            if m.get("name") == name:
                return {"success": False, "detail": f"marketplace 已存在: {name}"}

        # 新增源預設禁用，避免未經確認就觸發遠端同步。
        self._add_marketplace({"name": name, "url": url, "enabled": False})
        return {"success": True}

    async def handle_skills_marketplace_remove(self, params: dict) -> dict:
        """刪除 marketplace 源.

        params:
            name: marketplace 名稱
            remove_cache: 是否刪除本地倉庫快取（可選，預設 True）
        """
        name = params.get("name", "")
        remove_cache = params.get("remove_cache", True)
        if not name:
            return {"success": False, "detail": "缺少引數: name"}
        try:
            name = _safe_path_name(name, "marketplace")
        except ValueError as exc:
            _log_rejected_name("skills.marketplace.remove", "marketplace", name, exc)
            return {"success": False, "detail": str(exc)}

        removed = self._remove_marketplace(name)
        if not removed:
            return {"success": False, "detail": f"marketplace 不存在: {name}"}

        cache_removed = False
        if bool(remove_cache):
            repo_dir = _safe_child_path(self._marketplace_dir, name, "marketplace")
            if repo_dir.exists() and repo_dir.is_dir():
                try:
                    _safe_rmtree(repo_dir)
                    cache_removed = True
                except Exception as exc:
                    logger.warning("刪除 marketplace 快取失敗: %s", exc)

        return {
            "success": True,
            "name": name,
            "cache_removed": cache_removed,
        }

    async def handle_skills_marketplace_toggle(self, params: dict) -> dict:
        """啟用或禁用 marketplace 源.

        params:
            name: marketplace 名稱
            enabled: 目標狀態
        """
        name = params.get("name", "")
        enabled = params.get("enabled")
        if not name:
            return {"success": False, "detail": "缺少引數: name"}
        if not isinstance(enabled, bool):
            return {"success": False, "detail": "缺少引數: enabled (bool)"}
        try:
            name = _safe_path_name(name, "marketplace")
        except ValueError as exc:
            _log_rejected_name("skills.marketplace.toggle", "marketplace", name, exc)
            return {"success": False, "detail": str(exc)}

        marketplace = next(
            (m for m in self._get_marketplaces() if m.get("name") == name),
            None,
        )
        if marketplace is None:
            return {"success": False, "detail": f"marketplace 不存在: {name}"}

        if enabled:
            repo_dir = _safe_child_path(self._marketplace_dir, name, "marketplace")
            url = marketplace.get("url", "")
            if not url:
                return {"success": False, "detail": f"marketplace {name} 缺少 url"}

            detail = "已啟用"
            if repo_dir.exists():
                commit = await self._git_pull(repo_dir)
                if commit is None:
                    return {"success": False, "name": name, "enabled": False, "detail": "git pull 失敗"}
                detail = "已啟用並執行 git pull"
            else:
                commit = await self._git_clone(url, repo_dir)
                if commit is None:
                    return {"success": False, "name": name, "enabled": False, "detail": "git clone 失敗"}
                detail = "已啟用並執行 git clone"

            self._set_marketplace_enabled(name, True)
            self._set_marketplace_last_updated(name)
            return {"success": True, "name": name, "enabled": True, "detail": detail}

        # 禁用：刪除本地快取目錄，不解除安裝已安裝 skill。
        repo_dir = _safe_child_path(self._marketplace_dir, name, "marketplace")
        cache_removed = False
        if repo_dir.exists() and repo_dir.is_dir():
            cache_removed = _safe_rmtree(repo_dir)
            if not cache_removed:
                return {"success": False, "name": name, "enabled": True, "detail": "刪除本地快取失敗"}

        self._set_marketplace_enabled(name, False)
        self._set_marketplace_last_updated(name)
        return {
            "success": True,
            "name": name,
            "enabled": False,
            "cache_removed": cache_removed,
            "detail": "已禁用並刪除本地快取" if cache_removed else "已禁用（無本地快取）",
        }

    # -----------------------------------------------------------------------
    # SKILL.md 解析
    # -----------------------------------------------------------------------

    @staticmethod
    def _coerce_str_list(val: Any) -> list[str]:
        """frontmatter 裡 tags/allowed_tools 可能是逗號分隔字串，統一為 list[str]."""
        if val is None:
            return []
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return []
            if "," in s:
                return [p.strip() for p in s.split(",") if p.strip()]
            return [s]
        return [str(val)]

    @staticmethod
    def _convert_yaml_date(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: SkillManager._convert_yaml_date(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [SkillManager._convert_yaml_date(item) for item in obj]
        if isinstance(obj, date) and not isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    @staticmethod
    def _parse_skill_md(path: Path) -> dict | None:
        """解析 SKILL.md，提取 YAML frontmatter 和正文.

        支援兩種格式:
        1. 有 frontmatter（--- 分隔的 YAML 頭 + 正文）
        2. 無 frontmatter（整個檔案作為 body，name 從檔名推斷）
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("無法讀取檔案: %s", path)
            return None

        meta: dict[str, Any] = {}
        body = text

        # 嘗試解析 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            body = fm_match.group(2).strip()
            # 優先完整 YAML（支援 description: >- 多行、巢狀等），與 Team Skills Hub register_skill 一致
            try:
                loaded = yaml.safe_load(fm_text)
                if isinstance(loaded, dict):
                    loaded = SkillManager._convert_yaml_date(loaded)
                    meta = {str(k): v for k, v in loaded.items()}
                else:
                    meta = {}
            except Exception:
                meta = {}
            if not meta:
                # 回退：逐行 key: value（舊邏輯，無 PyYAML 語義）
                for line in fm_text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)", line)
                    if m:
                        key = m.group(1)
                        val = m.group(2).strip()
                        if val.startswith("[") and val.endswith("]"):
                            inner = val[1:-1]
                            val = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
                        elif val.startswith(("'", '"')) and val.endswith(("'", '"')):
                            val = val[1:-1]
                        meta[key] = val

        # 如果沒有 name，從檔名推斷
        if "name" not in meta:
            meta["name"] = path.stem

        # 預設欄位
        meta.setdefault("description", "")
        meta.setdefault("version", "")
        meta.setdefault("author", "")
        meta["tags"] = SkillManager._coerce_str_list(meta.get("tags"))
        meta["allowed_tools"] = SkillManager._coerce_str_list(meta.get("allowed_tools"))

        meta["body"] = body
        meta["path"] = str(path)

        return meta

    @staticmethod
    def _try_find_skill_file(directory: Path) -> Path | None:
        """在目錄中查詢 skill 檔案.

        優先查詢 SKILL.md，其次查詢任意 .md 檔案.
        """
        skill_md = directory / "SKILL.md"
        if skill_md.is_file():
            return skill_md

        # 相容：查詢任意 .md 檔案
        md_files = list(directory.glob("*.md"))
        if md_files:
            return md_files[0]

        return None

    # -----------------------------------------------------------------------
    # 內建技能判斷
    # -----------------------------------------------------------------------

    def _is_builtin_skill(self, skill_name: str, installed_plugins: list[dict], skill_path: Path | None = None) -> bool:
        """判斷技能是否為內建技能.

        內建技能的判斷標準：
        1. 不在 local_skills 中（使用者本地匯入）
        2. 不在 installed_plugins 中（marketplace安裝）
        3. 實際路徑在原始碼內建路徑下（透過 skill_path 引數判斷）

        注意：如果 skill_path 不為 None，則直接判斷該路徑是否在 builtin_dir 下，
        這比僅透過 skill_name 判斷更準確，避免名稱衝突導致的誤判。
        """
        try:
            # 檢查是否在 local_skills 中（使用者本地匯入或SkillNet下載）
            for local_skill in self._state.get("local_skills", []):
                if local_skill.get("name") == skill_name:
                    return False

            # 檢查是否在 installed_plugins 中記錄（marketplace安裝的）
            for plugin in installed_plugins:
                if plugin.get("name") == skill_name:
                    return False

            # 如果提供了 skill_path，直接判斷該路徑是否在 builtin_dir 下
            if skill_path is not None:
                builtin_dir = get_builtin_skills_dir()
                if builtin_dir.exists():
                    return skill_path.resolve().parent == builtin_dir.resolve()
                return False

            # 沒有提供 skill_path 時，回退到透過 skill_name 判斷（相容舊程式碼）
            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtin_skill_path = _safe_child_path(builtin_dir, skill_name, "skill")
                return builtin_skill_path.exists() and builtin_skill_path.is_dir()
            return False
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # 目錄掃描
    # -----------------------------------------------------------------------

    def _scan_local_skills(self) -> list[dict]:
        """掃描 agent/skills/ 下的本地 skill（跳過 _marketplace）."""
        results: list[dict] = []
        if not self._skills_dir.exists():
            return results

        for child in self._skills_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta is None:
                continue

            # 判斷 source 型別
            installed = self._get_installed_plugins()
            source = "project"
            for p in installed:
                if p.get("name") == meta.get("name"):
                    source = p.get("source", "project")
                    if source == "project" and p.get("marketplace"):
                        source = p.get("marketplace", "project")
                    break
            # 檢查是否透過 import_local / SkillNet 等寫入 local_skills（含 origin 供前端對照 skill_url）
            for ls in self._state.get("local_skills", []):
                if ls.get("name") == meta.get("name"):
                    source = ls.get("source", "local") if isinstance(ls, dict) else "local"
                    if isinstance(ls, dict):
                        origin = ls.get("origin")
                        if isinstance(origin, str) and origin.strip():
                            meta["origin"] = origin.strip()
                    break

            meta["source"] = source
            # 判斷是否為內建技能（傳入 child 路徑，透過實際路徑判斷）
            meta["is_builtin"] = self._is_builtin_skill(meta.get("name", ""), self._get_installed_plugins(), child)
            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtin_skill_path = builtin_dir / child.name
                meta["is_builtin_source"] = builtin_skill_path.exists() and builtin_skill_path.is_dir()
            else:
                meta["is_builtin_source"] = False
            meta["has_evolutions"] = (child / _EVOLUTION_FILENAME).is_file()
            # 不在列表中返回 body
            meta.pop("body", None)
            results.append(meta)

        return results

    def _scan_builtin_skills(self) -> list[dict]:
        """掃描內建技能目錄中尚未安裝到使用者目錄的技能.

        返回的技能列表僅包含那些存在於內建目錄但尚未在使用者目錄中的技能。
        """
        results: list[dict] = []
        builtin_dir = get_builtin_skills_dir()
        user_skills_dir = get_agent_skills_dir()

        if not builtin_dir.exists() or not builtin_dir.is_dir():
            return results

        for child in builtin_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue

            # 檢查該技能是否已經在使用者目錄中安裝
            user_skill_path = user_skills_dir / child.name
            if user_skill_path.exists() and user_skill_path.is_dir():
                continue  # 已安裝，跳過

            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta is None:
                continue

            # 設定內建技能的標記
            meta["source"] = "builtin"
            meta["is_builtin"] = True
            meta["is_builtin_source"] = True  # 這是內建技能來源
            meta["has_evolutions"] = False
            # 不在列表中返回 body
            meta.pop("body", None)
            results.append(meta)

        return results

    def _resolve_skill_source(self, skill_name: str) -> str:
        """解析 skill 來源（local / project / marketplace 名稱）."""
        if not skill_name:
            return "project"

        for plugin in self._get_installed_plugins():
            if plugin.get("name") == skill_name:
                source = plugin.get("source")
                marketplace = plugin.get("marketplace")
                if source == "project" and isinstance(marketplace, str) and marketplace:
                    return marketplace
                if isinstance(source, str) and source:
                    return source
                if isinstance(marketplace, str) and marketplace:
                    return marketplace
                return "project"

        for local_skill in self._state.get("local_skills", []):
            if local_skill.get("name") == skill_name:
                return "local"

        return "project"

    def _resolve_local_skill_dir(self, skill_name: str) -> Path | None:
        """根據 skill name 定位本地技能目錄（僅 agent/skills 下）."""
        try:
            direct = _safe_child_path(self._skills_dir, skill_name, "skill")
        except ValueError:
            return None
        if direct.is_dir():
            return direct

        if not self._skills_dir.exists():
            return None

        for child in self._skills_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta and meta.get("name") == skill_name:
                return child
        return None

    def _get_skill_evolution_path(self, skill_name: str) -> Path | None:
        skill_dir = self._resolve_local_skill_dir(skill_name)
        if skill_dir is None:
            return None
        return skill_dir / _EVOLUTION_FILENAME

    def _scan_marketplace_skills(self) -> list[dict]:
        """掃描 _marketplace/ 下已 clone 的倉庫中未安裝的 skill.

        掃描路徑：_marketplace/{marketplace_name}/skills/{plugin_name}
        """
        results: list[dict] = []
        if not self._marketplace_dir.exists():
            return results

        installed_names = {p.get("name") for p in self._get_installed_plugins()}

        enabled_marketplaces = {
            m.get("name") for m in self._get_marketplaces() if bool(m.get("enabled", True)) and m.get("name")
        }

        for repo_dir in self._marketplace_dir.iterdir():
            if not repo_dir.is_dir():
                continue
            marketplace_name = repo_dir.name
            if marketplace_name not in enabled_marketplaces:
                continue

            # 檢查 skills 子目錄是否存在
            skills_dir = repo_dir / "skills"
            # 單skill相容
            is_skills_dir = True
            if not skills_dir.exists() or not skills_dir.is_dir():
                # 如果沒有 skills 子目錄，嘗試直接掃描 repo_dir（相容舊結構）
                skills_dir = repo_dir
                is_skills_dir = False

            for plugin_dir in skills_dir.iterdir():
                if not is_skills_dir and plugin_dir != repo_dir:
                    continue
                if not plugin_dir.is_dir():
                    continue
                # 跳過 git 後設資料和以 _ 開頭的目錄
                if plugin_dir.name.startswith((".", "_")):
                    continue
                md = self._try_find_skill_file(plugin_dir)
                if md is None:
                    continue
                meta = self._parse_skill_md(md)
                if meta is None:
                    continue

                # 跳過已安裝的
                if meta.get("name") in installed_names:
                    continue

                # source 直接返回 marketplace 名稱，便於前端安裝時自動拼接 spec
                meta["source"] = marketplace_name
                meta["marketplace"] = marketplace_name
                meta["is_builtin"] = False
                meta["has_evolutions"] = False
                meta.pop("body", None)
                results.append(meta)

        return results

    def _get_mirror_skills_dirs(self) -> list[Path]:
        """返回需要映象同步的 skills 目錄（不包含當前執行目錄）.

        注意：開發模式下不返回原始碼目錄作為映象目標，避免使用者下載的
        skill被複制到原始碼目錄，重啟後被誤判為內建skill。
        """
        mirrors: list[Path] = []
        if is_package_installation():
            return []
        try:
            source_repo_root = Path(__file__).resolve().parents[2]
            source_resources_skills_dir = source_repo_root / "jiuwenclaw" / "resources" / "agent" / "skills"
            # 開發模式下不將原始碼目錄作為映象目標
            # 這樣使用者下載的skill只儲存在使用者目錄，不會汙染原始碼目錄
            if (
                source_resources_skills_dir.exists()
                and source_resources_skills_dir.resolve() != self._skills_dir.resolve()
                and source_resources_skills_dir.resolve() != get_builtin_skills_dir().resolve()
            ):
                mirrors.append(source_resources_skills_dir)
        except Exception:
            return []
        return mirrors

    @staticmethod
    def _normalize_lang_suffix(name: str) -> str:
        """將 xxxx_zh.MD / xxxx_en.MD 規範為 xxxx.MD（去除 _zh/_en 字尾）。"""
        stem, suffix = name.rpartition(".")[0], name.rpartition(".")[2]
        suffix_lower = suffix.lower()
        if suffix_lower in ("md", "mdx"):
            stem_lower = stem.lower()
            if stem_lower.endswith("_zh"):
                stem = stem[:-3]
            elif stem_lower.endswith("_en"):
                stem = stem[:-3]
        return f"{stem}.{suffix}" if stem else name

    @staticmethod
    def _generate_agent_data_for_workspace(workspace_root: Path) -> None:
        """Generate agent/jiuwenclaw_workspace/agent-data.json from agent tree."""
        agent_root = workspace_root.resolve()
        output_path = (agent_root / "agent-data.json").resolve()
        root_folder_key = "__root__"

        if not agent_root.exists() or not agent_root.is_dir():
            return

        folder_data: dict[str, list[dict[str, str | bool]]] = {}
        seen_paths: dict[str, set[str]] = {}
        for entry in sorted(agent_root.rglob("*")):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            # Skip files in hidden directories (e.g., .agent_history)
            if any(part.startswith(".") for part in entry.relative_to(agent_root).parts):
                continue
            relative_folder_path = entry.parent.relative_to(agent_root.parent).as_posix()
            folder_key = root_folder_key if relative_folder_path == "." else relative_folder_path

            display_name = SkillManager._normalize_lang_suffix(entry.name)
            display_path = (
                f"agent/{relative_folder_path}/{display_name}".replace("/.", "/").replace("//", "/")
                if relative_folder_path != "."
                else f"agent/{display_name}"
            )

            seen = seen_paths.setdefault(folder_key, set())
            if display_path in seen:
                continue
            seen.add(display_path)

            folder_data.setdefault(folder_key, []).append(
                {
                    "name": display_name,
                    "path": display_path,
                    "isMarkdown": entry.suffix.lower() in {".md", ".mdx"},
                }
            )

        sorted_folder_data = {
            folder_key: sorted(files, key=lambda item: item["path"])
            for folder_key, files in sorted(folder_data.items(), key=lambda item: item[0])
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(sorted_folder_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _refresh_agent_data_indexes(self) -> None:
        """Refresh agent-data.json for runtime and mirror workspaces."""
        workspace_roots: set[Path] = {self._agent_root.resolve()}
        for mirror_root in self._get_mirror_skills_dirs():
            try:
                # mirror_root = .../agent/skills → agent 根目錄為其 parent
                workspace_roots.add(mirror_root.parent.resolve())
            except Exception:
                continue
        for workspace_root in workspace_roots:
            try:
                self._generate_agent_data_for_workspace(workspace_root)
            except Exception as exc:
                logger.warning("重建 agent-data.json 失敗: agent_root=%s error=%s", workspace_root, exc)

    @staticmethod
    def _locate_skill_dir(path: Path) -> Path | None:
        """定位包含 SKILL.md 的目錄（優先當前目錄，再向下遞迴）；檔名大小寫不敏感."""
        if path.is_file() and path.name.lower() == "skill.md":
            return path.parent
        if path.is_dir():
            direct = path / "SKILL.md"
            if direct.is_file():
                return path
            for md in path.rglob("SKILL.md"):
                if md.is_file():
                    return md.parent
            # 相容小寫 skill.md（如 Linux 下倉庫命名）
            for md in path.rglob("*.md"):
                if md.is_file() and md.name.lower() == "skill.md":
                    return md.parent
        return None

    @staticmethod
    def _get_team_skills_hub_base_url(override_url: str | None = None) -> str:
        raw = (override_url or os.getenv("TEAM_SKILLS_HUB_BASE_URL") or _TEAM_SKILLS_HUB_BASE_URL_DEFAULT).strip()
        return raw.rstrip("/")

    @staticmethod
    def _resolve_teamskills_hub_auth(params: dict[str, Any]) -> dict[str, str]:
        token = str(params.get("token") or "").strip()
        system_token = str(params.get("system_token") or "").strip()
        has_user = bool(token)
        has_system = bool(system_token)
        if has_user == has_system:
            return {"error": "請且僅請提供一種鑑權：token 或 system_token"}
        if has_system:
            return {"system_token": system_token}
        return {"token": token}

    def _prepare_teamskills_publish_zip(
        self,
        *,
        path_raw: str,
        file_raw: str,
        plugin_version: str,
        tmpdir: Path,
    ) -> Path:
        """對齊 jiuwen-teamskills：上傳前規範化 zip，確保包含合法 plugin.yaml."""
        if file_raw:
            src_zip = Path(file_raw).expanduser().resolve()
            if not src_zip.is_file():
                raise RuntimeError(f"zip 檔案不存在: {src_zip}")
            if src_zip.suffix.lower() != ".zip":
                raise RuntimeError("file 必須是 .zip 檔案")
            stage_dir = tmpdir / "zip_stage"
            stage_dir.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(src_zip, "r") as zf:
                    zf.extractall(stage_dir)
            except zipfile.BadZipFile as exc:
                raise RuntimeError(f"zip 檔案損壞或格式非法: {src_zip}") from exc
            return self._build_teamskills_publish_zip_from_root(stage_dir, plugin_version, tmpdir)

        root = Path(path_raw).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"path 不是有效目錄: {root}")
        return self._build_teamskills_publish_zip_from_root(root, plugin_version, tmpdir)

    def _build_teamskills_publish_zip_from_root(
        self,
        root: Path,
        plugin_version: str,
        tmpdir: Path,
    ) -> Path:
        skill_dir = self._locate_skill_dir(root)
        if skill_dir is None:
            raise RuntimeError("釋出目錄中未找到 SKILL.md")
        skill_md = skill_dir / "SKILL.md"
        meta = self._parse_skill_md(skill_md)
        if not meta:
            raise RuntimeError("SKILL.md 解析失敗")

        skill_name = str(meta.get("name") or "").strip()
        if not skill_name:
            raise RuntimeError("SKILL.md frontmatter 缺少 name")
        description = str(meta.get("description") or "").strip() or skill_name
        display_name = str(meta.get("display_name") or "").strip() or skill_name
        author = str(meta.get("author") or "").strip() or "unknown"
        tags = meta.get("tags")
        if isinstance(tags, list):
            normalized_tags = [str(t).strip() for t in tags if str(t).strip()]
        elif isinstance(tags, str) and tags.strip():
            normalized_tags = [tags.strip()]
        else:
            normalized_tags = []
        if not normalized_tags:
            normalized_tags = ["teamskills"]

        # 與 jiuwen-teamskills 相容：market publish 仍使用 runtime.type=skill
        plugin_yaml_payload = {
            "name": skill_name,
            "version": plugin_version,
            "display_name": display_name,
            "description": description,
            "runtime": {"type": "skill"},
            "metadata": {
                "author": author,
                "tags": normalized_tags,
            },
        }

        zip_path = tmpdir / "teamskills_publish_normalized.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f"{skill_name}/plugin.yaml",
                yaml.safe_dump(plugin_yaml_payload, sort_keys=False, allow_unicode=True),
            )
            readme = root / "README.md"
            if readme.is_file():
                zf.write(readme, arcname=f"{skill_name}/README.md")
            for child in skill_dir.rglob("*"):
                if not child.is_file():
                    continue
                rel = child.relative_to(skill_dir).as_posix()
                zf.write(child, arcname=f"{skill_name}/{skill_name}/{rel}")
        return zip_path

    @staticmethod
    def _normalize_teamskills_hub_http_error(resp: httpx.Response) -> str:
        detail = (resp.text or "").strip()[:300]
        if not detail:
            return f"Team Skills Hub API 錯誤 HTTP {resp.status_code}"
        return f"Team Skills Hub API 錯誤 HTTP {resp.status_code}: {detail}"

    async def _teamskills_hub_publish_request(
        self,
        *,
        base_url: str,
        zip_path: Path,
        checksum_sha256: str,
        plugin_id: str | None,
        plugin_version: str,
        version_desc: str,
        force: bool,
        token: str | None,
        system_token: str | None,
    ) -> dict[str, Any]:
        req_url = f"{base_url}/api/v1/plugins"
        headers: dict[str, str] = {"X-Checksum-SHA256": checksum_sha256}
        if system_token:
            headers["X-System-Token"] = system_token
        else:
            headers["Authorization"] = f"Bearer {token}"

        data: dict[str, str] = {
            "force": "true" if force else "false",
            "version_desc": version_desc,
            "plugin_version": plugin_version,
        }
        if plugin_id:
            data["plugin_id"] = plugin_id
        files = {"file": (zip_path.name, zip_path.read_bytes(), "application/zip")}

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            resp = await client.post(req_url, data=data, files=files, headers=headers)
        if not resp.is_success:
            raise RuntimeError(self._normalize_teamskills_hub_http_error(resp))
        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Team Skills Hub API 響應格式錯誤")
        code = payload.get("code", 200)
        if int(code) != 200:
            message = str(payload.get("message") or "").strip() or "Team Skills Hub API 返回失敗"
            raise RuntimeError(message)
        data_payload = payload.get("data")
        if isinstance(data_payload, dict):
            return data_payload
        if data_payload is None:
            return {}
        raise RuntimeError("Team Skills Hub API 響應 data 格式錯誤")

    async def _teamskills_hub_delete_request(
        self,
        *,
        base_url: str,
        skill_id: str,
        version: str,
        token: str | None,
        system_token: str | None,
    ) -> None:
        req_url = f"{base_url}/api/v1/plugins/{quote(skill_id, safe='')}/versions/{quote(version, safe='')}"
        headers: dict[str, str] = {}
        if system_token:
            headers["X-System-Token"] = system_token
        else:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            resp = await client.delete(req_url, headers=headers)
        if not resp.is_success:
            raise RuntimeError(self._normalize_teamskills_hub_http_error(resp))

    @staticmethod
    def _get_team_skills_hub_allowed_download_hosts() -> list[str]:
        raw = (os.getenv("TEAM_SKILLS_HUB_ALLOWED_DOWNLOAD_HOSTS") or "").strip()
        if not raw:
            return list(_TEAM_SKILLS_HUB_DEFAULT_ALLOWED_DOWNLOAD_HOSTS)
        hosts: list[str] = []
        for token in raw.split(","):
            host = token.strip().lower()
            if not host:
                continue
            hosts.append(host)
        return hosts or list(_TEAM_SKILLS_HUB_DEFAULT_ALLOWED_DOWNLOAD_HOSTS)

    @staticmethod
    def _get_import_local_allowed_download_hosts() -> list[str]:
        raw = (os.getenv("IMPORT_LOCAL_ALLOWED_DOWNLOAD_HOSTS") or "").strip()
        if not raw:
            return list(_IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS)
        hosts: list[str] = []
        for token in raw.split(","):
            host = token.strip().lower()
            if not host:
                continue
            hosts.append(host)
        return hosts or list(_IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS)

    @staticmethod
    def _assert_team_skills_hub_download_url_allowed(download_url: str) -> None:
        parsed = urlparse(download_url)
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError("Team Skills Hub download_url 必須使用 HTTP 或 HTTPS")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise RuntimeError("Team Skills Hub download_url 缺少主機名")
        for rule in SkillManager._get_team_skills_hub_allowed_download_hosts():
            # 支援 .example.com 字尾匹配與 * 單段通配（如 a.*.c.com）。
            if rule.startswith("."):
                if host.endswith(rule):
                    return
                continue
            if SkillManager._team_skills_hub_host_matches_rule(host, rule):
                return
        raise RuntimeError(f"Team Skills Hub download_url host 不在白名單: {host}")

    @staticmethod
    def _assert_import_local_download_url_allowed(download_url: str) -> None:
        parsed = urlparse(download_url)
        if parsed.scheme != "https":
            raise RuntimeError("遠端匯入 URL 必須使用 HTTPS")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise RuntimeError("遠端匯入 URL 缺少主機名")
        for rule in SkillManager._get_import_local_allowed_download_hosts():
            if rule.startswith("."):
                if host.endswith(rule):
                    return
                continue
            if SkillManager._team_skills_hub_host_matches_rule(host, rule):
                return
        raise RuntimeError(f"遠端匯入 URL host 不在白名單: {host}")

    @staticmethod
    def _team_skills_hub_host_matches_rule(host: str, rule: str) -> bool:
        host_parts = host.split(".")
        rule_parts = rule.split(".")
        if len(host_parts) != len(rule_parts):
            return False
        for host_part, rule_part in zip(host_parts, rule_parts):
            if rule_part == "*":
                continue
            if host_part != rule_part:
                return False
        return True

    @staticmethod
    def _is_http_download_target(value: str) -> bool:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _team_skills_hub_http_get_data(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = _TEAM_SKILLS_HUB_MARKET_TIMEOUT,
        base_url: str | None = None,
    ) -> Any:
        base_url = (base_url or self._get_team_skills_hub_base_url()).rstrip("/")
        rel_path = path if path.startswith("/") else f"/{path}"
        req_url = f"{base_url}{rel_path}"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                resp = await client.get(req_url, params=params)
        except Exception as exc:
            raise RuntimeError(f"無法連線 Team Skills Hub: {exc}") from exc

        if not resp.is_success:
            detail = (resp.text or "").strip()[:300]
            raise RuntimeError(f"Team Skills Hub API 錯誤 HTTP {resp.status_code}: {detail}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Team Skills Hub API 響應不是合法 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Team Skills Hub API 響應格式錯誤")

        code = payload.get("code", 200)
        if int(code) != 200:
            message = str(payload.get("message", "")).strip() or "Team Skills Hub API 返回失敗"
            raise RuntimeError(message)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Team Skills Hub API 響應 data 格式錯誤")
        return data

    @staticmethod
    def _safe_extract_zip_members_into(zf: zipfile.ZipFile, dest_root: Path) -> None:
        """將已開啟的 ZIP 成員解壓到 dest_root（須為 resolve() 後的目錄），拒絕 Zip Slip。"""
        for info in zf.infolist():
            raw = (info.filename or "").replace("\\", "/")
            if not raw or raw.startswith("/"):
                continue
            if "\0" in raw:
                raise RuntimeError("ZIP 包含非法檔名")
            is_dir = raw.endswith("/") or info.is_dir()
            rel_str = raw.rstrip("/")
            if not rel_str:
                continue
            rel = PurePosixPath(rel_str)
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError("ZIP 包含非法路徑")
            dest_path = dest_root.joinpath(*rel.parts)
            try:
                dest_path = dest_path.resolve()
                dest_path.relative_to(dest_root)
            except ValueError as exc:
                raise RuntimeError("ZIP 路徑越界") from exc
            if is_dir:
                dest_path.mkdir(parents=True, exist_ok=True)
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src:
                dest_path.write_bytes(src.read())

    @staticmethod
    def _safe_extract_zip_bytes_to_dir(data: bytes, dest_dir: Path) -> None:
        """將 ZIP 位元組解壓到 dest_dir（不落盤 staging zip，與 ClawHub extractall 語義一致）。"""
        dest_root = dest_dir.resolve()
        dest_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            SkillManager._safe_extract_zip_members_into(zf, dest_root)

    @staticmethod
    def _safe_extract_zip_to_dir(zip_path: Path, dest_dir: Path) -> None:
        """將 ZIP 檔案解壓到 dest_dir，拒絕 Zip Slip（..、絕對路徑、寫出目標目錄外）。"""
        dest_root = dest_dir.resolve()
        dest_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            SkillManager._safe_extract_zip_members_into(zf, dest_root)

    @staticmethod
    def _safe_extract_tar_to_dir(tar_path: Path, dest_dir: Path) -> None:
        """Extract TAR/TAR.GZ/TGZ safely into dest_dir."""
        dest_root = dest_dir.resolve()
        dest_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:*") as tf:
            for member in tf.getmembers():
                raw = (member.name or "").replace("\\", "/")
                if not raw or raw.startswith("/"):
                    continue
                if "\0" in raw:
                    raise RuntimeError("歸檔包含非法檔名")
                rel = PurePosixPath(raw.rstrip("/"))
                if not rel.parts:
                    continue
                if rel.is_absolute() or ".." in rel.parts:
                    raise RuntimeError("歸檔包含非法路徑")
                if member.islnk() or member.issym():
                    raise RuntimeError("歸檔包含連結檔案，已拒絕匯入")
                dest_path = dest_root.joinpath(*rel.parts)
                try:
                    dest_path = dest_path.resolve()
                    dest_path.relative_to(dest_root)
                except ValueError as exc:
                    raise RuntimeError("歸檔路徑越界") from exc
                if member.isdir():
                    dest_path.mkdir(parents=True, exist_ok=True)
                    continue
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with extracted:
                    dest_path.write_bytes(extracted.read())

    @staticmethod
    def _detect_archive_format(body: bytes) -> str:
        if len(body) >= 4 and body.startswith(b"PK"):
            return "zip"
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:*"):
                return "tar"
        except tarfile.TarError:
            pass
        return ""

    def _extract_archive_bytes_to_dir(self, body: bytes, dest_dir: Path) -> None:
        archive_format = self._detect_archive_format(body)
        logger.info(
            "[SkillManager] extract archive: format=%s bytes=%s dest_dir=%s",
            archive_format or "unknown",
            len(body),
            dest_dir,
        )
        if archive_format == "zip":
            archive_path = dest_dir / "artifact.zip"
            archive_path.write_bytes(body)
            self._safe_extract_zip_to_dir(archive_path, dest_dir)
            return
        if archive_format == "tar":
            archive_path = dest_dir / "artifact.tar"
            archive_path.write_bytes(body)
            self._safe_extract_tar_to_dir(archive_path, dest_dir)
            return
        raise RuntimeError("下載內容不是受支援的歸檔格式，目前僅支援 zip/tar/tar.gz/tgz")

    async def _download_remote_archive_and_verify(
        self,
        download_url: str,
        *,
        checksum_sha256: str = "",
        timeout: float | None = None,
    ) -> bytes:
        timeout = max(30.0, timeout or _IMPORT_LOCAL_REMOTE_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            body = resp.content or b""

        if not body:
            raise RuntimeError("下載內容為空")

        expected = checksum_sha256.strip().lower()
        if expected:
            digest = hashlib.sha256(body).hexdigest().lower()
            if digest != expected:
                raise RuntimeError("下載檔案校驗失敗（SHA256 不匹配）")

        archive_format = self._detect_archive_format(body)
        if archive_format == "zip":
            try:
                with zipfile.ZipFile(io.BytesIO(body), "r") as zf:
                    if zf.testzip() is not None:
                        raise RuntimeError("下載 ZIP 檔案已損壞")
            except zipfile.BadZipFile as exc:
                raise RuntimeError("下載內容不是有效 ZIP 檔案") from exc
            return body
        if archive_format == "tar":
            try:
                with tarfile.open(fileobj=io.BytesIO(body), mode="r:*"):
                    pass
            except tarfile.TarError as exc:
                raise RuntimeError("下載內容不是有效 TAR 歸檔") from exc
            return body
        raise RuntimeError("下載內容不是受支援的歸檔格式，目前僅支援 zip/tar/tar.gz/tgz")

    async def _download_zip_and_verify(
        self,
        download_url: str,
        *,
        checksum_sha256: str = "",
        timeout: float | None = None,
    ) -> bytes:
        timeout = max(30.0, timeout or _TEAM_SKILLS_HUB_MARKET_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            body = resp.content or b""

        if not body:
            raise RuntimeError("下載內容為空")
        if len(body) < 4 or not body.startswith(b"PK"):
            raise RuntimeError("下載內容不是 ZIP 檔案")

        expected = checksum_sha256.strip().lower()
        if expected:
            digest = hashlib.sha256(body).hexdigest().lower()
            if digest != expected:
                raise RuntimeError("下載檔案校驗失敗（SHA256 不匹配）")

        try:
            with zipfile.ZipFile(io.BytesIO(body), "r") as zf:
                if zf.testzip() is not None:
                    raise RuntimeError("下載 ZIP 檔案已損壞")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("下載內容不是有效 ZIP 檔案") from exc
        return body

    @staticmethod
    def _get_github_token() -> str:
        return (os.getenv("GITHUB_TOKEN") or "").strip()

    @staticmethod
    def _skillnet_eval_llm_params() -> dict[str, str | None]:
        """與主對話一致的 API Key / Base URL / 模型名（config.yaml react 段）."""
        try:
            from jiuwenclaw.common.config import get_config
        except Exception:
            return {
                "api_key": (os.getenv("API_KEY") or "").strip() or None,
                "base_url": (os.getenv("API_BASE") or "").strip() or None,
                "model": (os.getenv("MODEL_NAME") or "gpt-4o").strip(),
            }

        cfg = get_config() or {}
        react = cfg.get("react") or {}
        mcc = react.get("model_client_config") or {}
        api_key = (mcc.get("api_key") or os.getenv("API_KEY") or "").strip()
        base_url = (mcc.get("api_base") or os.getenv("API_BASE") or "").strip()
        model = (react.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o").strip()
        if base_url.endswith("/chat/completions"):
            base_url = base_url.rsplit("/chat/completions", 1)[0]
        return {
            "api_key": api_key or None,
            "base_url": base_url or None,
            "model": model or "gpt-4o",
        }

    @staticmethod
    def _skillnet_evaluate_sync(skill_url: str) -> dict[str, Any]:
        """同步 evaluate，供 asyncio.to_thread 呼叫."""
        try:
            from skillnet_ai import SkillNetClient
            from skillnet_ai.client import SkillNetError
        except Exception as exc:
            return {
                "ok": False,
                "detail": "未安裝 skillnet-ai，請先安裝依賴: pip install skillnet-ai",
                "detail_key": "skills.skillNet.errors.skillnetAiMissing",
            }

        llm = SkillManager._skillnet_eval_llm_params()
        if not llm.get("api_key"):
            return {
                "ok": False,
                "detail": "",
                "detail_key": "skills.skillNet.errors.evaluateNoApiKey",
            }

        kwargs: dict[str, Any] = {
            "api_key": llm["api_key"],
            "base_url": llm["base_url"],
            "github_token": SkillManager._get_github_token() or None,
        }
        try:
            with _skillnet_network_context():
                client = SkillNetClient(**kwargs)
                result = client.evaluate(target=skill_url, model=str(llm["model"]))
        except SkillNetError as exc:
            return {"ok": False, "detail": str(exc).strip() or "評估失敗。"}
        except Exception as exc:
            logger.exception("SkillNet evaluate 異常")
            return {"ok": False, "detail": str(exc).strip() or "評估失敗。"}

        if not isinstance(result, dict):
            return {"ok": True, "evaluation": result}
        return {"ok": True, "evaluation": result}

    @staticmethod
    def _skillnet_search_sync(search_kwargs: dict[str, Any]) -> list[Any]:
        """同步呼叫 skillnet-ai search，供 asyncio.to_thread 使用."""
        try:
            from skillnet_ai.searcher import SkillNetSearcher
        except Exception as exc:
            raise RuntimeError("未安裝 skillnet-ai，請先安裝依賴: pip install skillnet-ai") from exc

        with _skillnet_network_context():
            searcher = SkillNetSearcher()
            _configure_skillnet_requests_session(searcher.session)
            results = searcher.search(**search_kwargs)
        if results is None:
            return []
        if isinstance(results, list):
            return results
        return list(results)

    @staticmethod
    def _github_skillnet_install_error_context(skill_url: str) -> str:
        """下載失敗時拉 GitHub Contents 與 rate_limit，把官方 message 等拼給前端."""
        try:
            from skillnet_ai.downloader import SkillDownloader
        except ImportError:
            return ""

        dl = SkillDownloader(api_token=SkillManager._get_github_token())
        _configure_skillnet_requests_session(dl.session)
        parsed = dl._parse_github_url(skill_url)
        if not parsed:
            return ""

        owner, repo, ref, dir_path, _ = parsed
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}?ref={ref}"
        try:
            with _skillnet_network_context():
                r = dl.session.get(api, timeout=_SKILLNET_DOWNLOAD_TIMEOUT)
        except Exception as exc:
            logger.debug("SkillNet 安裝錯誤上下文: GitHub Contents 請求失敗: %s", exc)
            return ""

        parts: list[str] = []
        if r.status_code != 200:
            try:
                body = r.json()
                msg = body.get("message")
                if isinstance(msg, str) and msg.strip():
                    parts.append(msg.strip()[:800])
                else:
                    raw = (r.text or "").strip()[:500]
                    if raw:
                        parts.append(f"HTTP {r.status_code}: {raw}")
            except Exception as exc:
                logger.debug("SkillNet 安裝錯誤上下文: 解析 GitHub 錯誤 JSON 失敗: %s", exc)
                raw = (r.text or "").strip()[:500]
                if raw:
                    parts.append(f"HTTP {r.status_code}: {raw}")

            if r.status_code == 403 or any("rate limit" in p.lower() for p in parts):
                try:
                    with _skillnet_network_context():
                        rl = dl.session.get("https://api.github.com/rate_limit", timeout=12)
                    if rl.status_code == 200:
                        core = rl.json().get("resources", {}).get("core") or {}
                        rem, lim = core.get("remaining"), core.get("limit")
                        if rem is not None and lim is not None:
                            parts.append(
                                f"GitHub 核心 API 剩餘 {rem}/{lim}，"
                                "可在配置頁「第三方服務」填寫 github_token（GITHUB_TOKEN）提高額度"
                            )
                except Exception as exc:
                    logger.debug(
                        "SkillNet 安裝錯誤上下文: GitHub rate_limit 請求失敗: %s",
                        exc,
                    )

        return " | ".join(parts) if parts else ""

    @staticmethod
    def _skillnet_download_sync(skill_url: str, target_dir: str, mirror_url: str | None = None) -> str:
        """同步呼叫 skillnet-ai download；失敗時附帶 GitHub API 返回說明（如前端的限流文案）。"""
        try:
            from skillnet_ai.downloader import SkillDownloader, GitHubAPIError
        except Exception as exc:
            raise RuntimeError("未安裝 skillnet-ai，請先安裝依賴: pip install skillnet-ai") from exc

        token = SkillManager._get_github_token()
        dl_kwargs: dict[str, Any] = {
            "api_token": token,
            "timeout": _SKILLNET_DOWNLOAD_TIMEOUT,
            "max_retries": _SKILLNET_MAX_RETRIES,
        }
        if mirror_url:
            dl_kwargs["mirror_url"] = mirror_url
        with _skillnet_network_context():
            downloader = SkillDownloader(**dl_kwargs)
            _configure_skillnet_requests_session(downloader.session)
            try:
                local_path = downloader.download(folder_url=skill_url, target_dir=target_dir)
            except GitHubAPIError:
                raise
            except Exception as exc:
                ctx = SkillManager._github_skillnet_install_error_context(skill_url)
                if ctx:
                    raise RuntimeError(f"{exc} | {ctx}") from exc
                raise
        if not local_path:
            # skillnet-ai 在多種情況下會無異常地返回 None：URL 無效、目錄下列表為空、
            # 或 Contents API 成功但拉 raw 檔案全部失敗（超時/網路）等，庫未區分原因。
            ctx = SkillManager._github_skillnet_install_error_context(skill_url)
            raise SkillNetEmptyDownloadError(github_context=ctx)
        return str(local_path)

    async def _git_clone(self, url: str, dest: Path) -> str | None:
        """淺克隆 git 倉庫，返回 commit hash 或 None."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                url,
                str(dest),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error("git clone 失敗: %s", stderr.decode(errors="replace"))
                return None
            return await self._git_get_commit(dest)
        except Exception as exc:
            logger.error("git clone 異常: %s", exc)
            return None

    async def _git_pull(self, repo_path: Path) -> str | None:
        """拉取最新程式碼，返回 commit hash 或 None."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "pull",
                "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("git pull 失敗: %s", stderr.decode(errors="replace"))
                return None
            return await self._git_get_commit(repo_path)
        except Exception as exc:
            logger.warning("git pull 異常: %s", exc)
            return None

    async def _git_get_commit(self, repo_path: Path) -> str | None:
        """獲取當前 HEAD commit hash."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "rev-parse",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            return stdout.decode().strip()
        except Exception:
            return None

    async def _sync_marketplace_repos(self) -> None:
        """同步所有已配置 marketplace 到本地目錄（存在則 pull，不存在則 clone）."""
        marketplaces = [m for m in self._get_marketplaces() if bool(m.get("enabled", True))]
        if not marketplaces:
            return

        self._marketplace_dir.mkdir(parents=True, exist_ok=True)

        for marketplace in marketplaces:
            name = marketplace.get("name", "")
            url = marketplace.get("url", "")
            if not name or not url:
                continue
            try:
                repo_dir = _safe_child_path(self._marketplace_dir, name, "marketplace")
            except ValueError as exc:
                _log_rejected_name("skills.marketplace.sync", "marketplace", name, exc)
                continue
            try:
                if repo_dir.exists():
                    await self._git_pull(repo_dir)
                else:
                    await self._git_clone(url, repo_dir)
            except Exception as exc:
                logger.warning(
                    "同步 marketplace 失敗: name=%s url=%s error=%s",
                    name,
                    url,
                    exc,
                )

    # -----------------------------------------------------------------------
    # 狀態持久化
    # -----------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        """載入 skills_state.json，失敗時返回預設空狀態."""
        try:
            if self._state_file.exists():
                state = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._normalize_state(state)
                return state
        except Exception:
            logger.warning("載入 skills_state.json 失敗，使用預設空狀態")
        default_state = {"marketplaces": [], "installed_plugins": [], "local_skills": []}
        self._normalize_state(default_state)
        return default_state

    def _save_state(self) -> None:
        """持久化狀態到 skills_state.json."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.error("儲存 skills_state.json 失敗")

    def _get_marketplaces(self) -> list[dict]:
        marketplaces = self._state.get("marketplaces", [])
        normalized = self.normalize_marketplaces(marketplaces)
        # 僅當結構發生變化時寫回，避免每次讀取都觸盤。
        if normalized != marketplaces:
            self._state["marketplaces"] = normalized
            self._save_state()
        return normalized

    def _add_marketplace(self, marketplace: dict) -> None:
        self._state.setdefault("marketplaces", []).append(marketplace)
        self._state["marketplaces"] = self.normalize_marketplaces(self._state.get("marketplaces", []))
        self._save_state()

    def _remove_marketplace(self, name: str) -> bool:
        marketplaces = self._state.get("marketplaces", [])
        kept = [m for m in marketplaces if m.get("name") != name]
        if len(kept) == len(marketplaces):
            return False
        self._state["marketplaces"] = self.normalize_marketplaces(kept)
        self._save_state()
        return True

    def _set_marketplace_enabled(self, name: str, enabled: bool) -> bool:
        marketplaces = self.normalize_marketplaces(self._state.get("marketplaces", []))
        updated = False
        for marketplace in marketplaces:
            if marketplace.get("name") == name:
                marketplace["enabled"] = bool(enabled)
                updated = True
                break
        if updated:
            self._state["marketplaces"] = marketplaces
            self._save_state()
        return updated

    def _set_marketplace_last_updated(self, name: str) -> bool:
        marketplaces = self.normalize_marketplaces(self._state.get("marketplaces", []))
        updated = False
        for marketplace in marketplaces:
            if marketplace.get("name") == name:
                marketplace["last_updated"] = datetime.now(timezone.utc).isoformat()
                updated = True
                break
        if updated:
            self._state["marketplaces"] = marketplaces
            self._save_state()
        return updated

    @staticmethod
    def normalize_marketplaces(raw_marketplaces: Any) -> list[dict]:
        normalized: list[dict] = []
        if not isinstance(raw_marketplaces, list):
            return normalized
        for item in raw_marketplaces:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            url = item.get("url", "")
            if not name or not url:
                continue
            normalized.append(
                {
                    **item,
                    "name": name,
                    "url": url,
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return normalized

    def _normalize_state(self, state: dict[str, Any]) -> None:
        state.setdefault("marketplaces", [])
        state.setdefault("installed_plugins", [])
        state.setdefault("local_skills", [])
        state["marketplaces"] = self.normalize_marketplaces(state.get("marketplaces"))

    def _get_installed_plugins(self) -> list[dict]:
        return self._state.get("installed_plugins", [])

    # -----------------------------------------------------------------------
    # 供 AgentServer 內部其它元件複用的輕量公開查詢介面
    # -----------------------------------------------------------------------

    def get_installed_plugins(self) -> list[dict]:
        """返回已安裝外掛記錄的複製。"""
        return list(self._get_installed_plugins())

    def get_local_skills(self) -> list[dict]:
        """返回本地技能安裝記錄的複製。"""
        return list(self._state.get("local_skills", []))

    def get_skill_meta(self, skill_name: str) -> dict[str, Any] | None:
        """返回本地 skill 的解析後設資料，附帶目錄與 skill 檔案路徑。"""
        skill_dir = self._resolve_local_skill_dir(skill_name)
        if skill_dir is None:
            return None
        skill_file = self._try_find_skill_file(skill_dir)
        if skill_file is None:
            return None
        meta = self._parse_skill_md(skill_file)
        if meta is None:
            return None
        meta["skill_dir"] = str(skill_dir)
        meta["skill_file"] = str(skill_file)
        return meta

    def is_builtin_skill(self, skill_name: str) -> bool:
        """判斷當前執行目錄中的 skill 是否為真正的內建技能。"""
        if not skill_name:
            return False
        try:
            dest = _safe_child_path(self._skills_dir, skill_name, "skill")
        except ValueError:
            return False
        builtin_dir = get_builtin_skills_dir()
        if not builtin_dir.exists():
            return False
        try:
            builtin_skill_path = _safe_child_path(builtin_dir, skill_name, "skill")
        except ValueError:
            return False
        if not builtin_skill_path.exists() or not builtin_skill_path.is_dir():
            return False
        return dest.exists() and dest.is_dir() and dest.resolve() == builtin_skill_path.resolve()

    def _add_installed_plugin(self, plugin: dict) -> None:
        plugins = self._state.setdefault("installed_plugins", [])
        # 更新已有記錄
        for i, p in enumerate(plugins):
            if p.get("name") == plugin.get("name"):
                plugins[i] = plugin
                self._save_state()
                return
        plugins.append(plugin)
        self._save_state()

    def _remove_installed_plugin(self, name: str) -> None:
        plugins = self._state.get("installed_plugins", [])
        self._state["installed_plugins"] = [p for p in plugins if p.get("name") != name]
        self._save_state()

    def _add_local_skill(self, skill: dict) -> None:
        local = self._state.setdefault("local_skills", [])
        # 更新已有記錄
        for i, s in enumerate(local):
            if s.get("name") == skill.get("name"):
                local[i] = skill
                self._save_state()
                return
        local.append(skill)
        self._save_state()

    def _remove_local_skill(self, name: str) -> None:
        local = self._state.get("local_skills", [])
        self._state["local_skills"] = [s for s in local if s.get("name") != name]
        self._save_state()

    # -----------------------------------------------------------------------
    # ClawHub 相關方法
    # -----------------------------------------------------------------------

    def _get_clawhub_token(self) -> str:
        """獲取 ClawHub CLI token."""
        return (self._state.get("clawhub", {}).get("token") or "").strip()

    def _set_clawhub_token(self, token: str) -> None:
        """設定 ClawHub CLI token（掩碼處理）。"""
        self._state.setdefault("clawhub", {})["token"] = token.strip() if token.strip() else ""
        self._save_state()

    @staticmethod
    def _mask_clawhub_token(token: str) -> str:
        """掩碼處理 ClawHub token。"""
        if not token:
            return ""
        if len(token) <= 8:
            return "*" * len(token)
        return token[:4] + "*" * (len(token) - 8) + token[-4:]
