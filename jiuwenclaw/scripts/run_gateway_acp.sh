#!/bin/bash

# 自動獲取專案根目錄
ROOT=$(cd "$(dirname "$0")/.." && pwd)

# 設定環境變數
export PYTHONPATH="$ROOT"
export PYTHONIOENCODING=utf-8

# 進入專案目錄
cd "$ROOT"

# 啟動程式（Linux/Mac 虛擬環境路徑不同）
"$ROOT/.venv/bin/python" -m jiuwenclaw.channel.acp_channel "$@"