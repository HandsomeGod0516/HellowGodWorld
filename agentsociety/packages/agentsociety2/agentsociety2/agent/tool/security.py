"""Bash 命令安全检查模块。

分级安全策略：
- 系统级危险命令：完全禁止（sudo、shutdown、dd 等）
- 网络命令：禁止（curl、wget、nc 等）
- 敏感路径访问：禁止

Example:
    from agentsociety2.agent.tool.security import BashSecurityChecker

    checker = BashSecurityChecker()
    is_safe, reason = checker.check("rm -rf /tmp/test")
    if not is_safe:
        print(f"Blocked: {reason}")
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Final


#: 系统级危险命令（完全禁止）
BLOCKED_SYSTEM_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "sudo",
        "su",
        "doas",
        "shutdown",
        "reboot",
        "poweroff",
        "halt",
        "init",
        "systemctl",
        "mkfs",
        "fdisk",
        "dd",
        ":(){",
    }
)

#: 网络命令（禁止）
BLOCKED_NETWORK_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "ssh",
        "scp",
        "rsync",
        "ftp",
        "telnet",
        "nmap",
        "socat",
    }
)

#: 合并的黑名单
BLOCKED_TOKENS: Final[frozenset[str]] = (
    BLOCKED_SYSTEM_COMMANDS | BLOCKED_NETWORK_COMMANDS
)

#: 危险模式（正则表达式）
BLOCKED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r">\s*/dev/"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\|\s*(ba)?sh\s*$"),
    re.compile(r"\beval\s+"),
    re.compile(r"\bfind\s+.*-exec\s+"),
)

#: 敏感路径
BLOCKED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "~/.ssh",
        "~/.gnupg",
        "/dev/sd",
        "/dev/hd",
        "/dev/nvme",
    }
)


class BashSecurityChecker:
    """Bash 命令安全检查器（单例）。"""

    _instance: "BashSecurityChecker | None" = None

    def __new__(
        cls, extra_tokens: frozenset[str] | None = None
    ) -> "BashSecurityChecker":
        if extra_tokens is None and cls._instance is not None:
            return cls._instance
        instance = super().__new__(cls)
        instance._blocked_tokens = BLOCKED_TOKENS | (extra_tokens or frozenset())
        if extra_tokens is None:
            cls._instance = instance
        return instance

    def check(self, cmd: str, workspace: str | None = None) -> tuple[bool, str]:
        """检查命令是否安全。

        :param cmd: Bash 命令。
        :param workspace: workspace 路径（预留）。
        :return: (is_safe, reason)。
        """
        if not cmd or not cmd.strip():
            return True, ""

        try:
            tokens = shlex.split(cmd)
        except ValueError as e:
            return False, f"invalid shell syntax: {e}"

        for token in tokens:
            base = token.split("/")[-1].lower()
            if base in self._blocked_tokens:
                return False, f"blocked command: {base}"

        for pattern in BLOCKED_PATTERNS:
            match = pattern.search(cmd)
            if match:
                return False, f"blocked pattern: {match.group()}"

        cmd_lower = cmd.lower()
        for path in BLOCKED_PATHS:
            expanded = os.path.expanduser(path)
            if expanded.lower() in cmd_lower:
                return False, f"blocked path: {path}"

        return True, ""

    def is_safe(self, cmd: str, workspace: str | None = None) -> bool:
        is_safe, _ = self.check(cmd, workspace)
        return is_safe
