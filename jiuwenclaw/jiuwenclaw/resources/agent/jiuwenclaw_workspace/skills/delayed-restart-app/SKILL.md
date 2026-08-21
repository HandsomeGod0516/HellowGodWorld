---
name: delayed-restart-app
description: 安排延遲重啟本 Agent 所在的服務（jiuwenclaw app）。執行後當前 Agent 程序會被終止並重新啟動，當前會話會斷開。用於使用者要求重啟、配置更新需生效、或服務異常需過載時。使用 bash 呼叫指令碼。
---

# 重啟本 Agent 所在的服務

本 skill 會**重啟 Agent 自身所在的服務程序**（jiuwenclaw app）。執行後 Agent 程序將被終止並重新拉起，當前會話會斷開，新連線將連到新程序。

當使用者要求「重啟服務」「重啟 app」「重啟 Agent」「配置已更新需重啟」或類似需求時，使用 `bash` 執行本 skill 下的指令碼。

## 指令碼位置

本 skill 目錄下包含 `launch_delayed_restart.py`，以 detached 方式啟動 `jiuwenclaw.scripts.delayed_restart_app`。

## 執行命令

使用 `bash` 工具執行（必須使用 launcher，否則重啟時會連同指令碼一起被終止）：

```bash
python %USERPROFILE%\.jiuwenclaw\agent\skills\delayed-restart-app\launch_delayed_restart.py --pid <當前 app 的 PID> --delay 5
```

（Unix/macOS 使用：`python ~/.jiuwenclaw/agent/skills/delayed-restart-app/launch_delayed_restart.py --pid <PID> --delay 5`）

- `--pid`：必填，當前 jiuwenclaw app 程序的 PID（執行前需先獲取，如從 config 或透過 `tasklist`/`pgrep` 等命令）
- `--delay 5`：延遲 5 秒後重啟（可改為 3、10 等）

## When to Use

- 使用者明確要求「重啟 app」「重啟服務」「重啟 Agent」
- 配置已透過 config.set 等修改，使用者詢問或要求重啟以生效
- 使用者反饋服務異常，建議重啟

## 注意事項

- 重啟後當前會話會斷開，新連線將使用新程序
- 預設 5 秒延遲，便於先返回響應再重啟
