#!/usr/bin/env bash
# JiuwenClaw 打包指令碼
# 1. 編譯前端 (jiuwenclaw/channels/web/frontend)
# 2. 構建 wheel 包（包含前端 dist）

set -e
PROJECT_ROOT="$(cd "$(dirname "$(dirname "$0")")" && pwd)"

echo "[build] 專案根目錄: $PROJECT_ROOT"

# 1. 編譯前端
WEB_DIR="$PROJECT_ROOT/jiuwenclaw/channels/web/frontend"
if [[ ! -d "$WEB_DIR" ]]; then
    echo "[build] 錯誤: 前端目錄不存在: $WEB_DIR" >&2
    exit 1
fi

echo "[build] 正在編譯前端..."
cd "$WEB_DIR"
if [[ ! -d node_modules ]]; then
    echo "[build] 安裝 npm 依賴..."
    npm install
fi
npm run build
cd "$PROJECT_ROOT"

DIST_DIR="$WEB_DIR/dist"
if [[ ! -d "$DIST_DIR" ]]; then
    echo "[build] 錯誤: 前端編譯輸出不存在: $DIST_DIR" >&2
    exit 1
fi
echo "[build] 前端編譯完成: $DIST_DIR"

# 臨時移走 node_modules，避免被打包進 wheel
NODE_MODULES="$WEB_DIR/node_modules"
NODE_MODULES_BAK="$WEB_DIR/node_modules.bak"
NODE_MODULES_MOVED=false
if [[ -d "$NODE_MODULES" ]]; then
    echo "[build] 臨時移走 node_modules 以減小 wheel 體積..."
    mv "$NODE_MODULES" "$NODE_MODULES_BAK"
    NODE_MODULES_MOVED=true
fi

cleanup() {
    # 恢復 node_modules
    if [[ "$NODE_MODULES_MOVED" == "true" && -d "$NODE_MODULES_BAK" ]]; then
        mv "$NODE_MODULES_BAK" "$NODE_MODULES"
        echo "[build] 已恢復 node_modules"
    fi
}
trap cleanup EXIT

# 2. 構建 wheel
echo "[build] 正在構建 wheel 包..."
pip install -q --upgrade build wheel
python -m build --wheel --no-isolation

# 確保 dist 目錄存在
DIST_OUTPUT="$PROJECT_ROOT/dist"
if [[ ! -d "$DIST_OUTPUT" ]]; then
    mkdir -p "$DIST_OUTPUT"
    echo "[build] 建立 dist 目錄: $DIST_OUTPUT"
fi
echo "[build] 完成! wheel 包位於: $DIST_OUTPUT"
ls -la dist/*.whl 2>/dev/null || true

# 3. 構建 TUI wheel
if ! command -v bun &>/dev/null; then
    echo "[build] 跳過 TUI 構建: 未找到 bun 命令" >&2
    echo "完成bun安裝: curl -fsSL https://bun.sh/install | bash  # 針對 macOS、Linux 和 WSL" >&2
else
    echo "[build] 正在構建TUI的 wheel包..."
    cd "$PROJECT_ROOT"
    python scripts/build_python_packages.py --target all --clean --install-js-deps
    echo "[build] TUI的 wheel 包構建完成"
fi
