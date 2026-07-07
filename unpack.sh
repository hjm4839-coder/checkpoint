#!/usr/bin/env bash
# checkpoint 知识库解包脚本（新电脑运行）
set -euo pipefail

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "用法: ./unpack.sh <checkpoint-migrate-xxx.tar.gz>"
    exit 1
fi

SETTINGS="$HOME/.claude/settings.json"
# 优先读安装时存的 OBSIDIAN_VAULT
if [ -f "$SETTINGS" ] && command -v python3 &>/dev/null; then
    VAULT=$(python3 -c "
import json,os
try:
    d=json.load(open(os.path.expanduser('$SETTINGS')))
    v=d.get('env',{}).get('OBSIDIAN_VAULT','')
    print(v)
except: pass
" 2>/dev/null)
fi

if [ -z "${VAULT:-}" ]; then
    echo "知识库解包到哪个目录？"
    read -r -p "Obsidian vault 路径 [默认: $HOME/obsidian/知识库]: " VAULT
    VAULT="${VAULT:-$HOME/obsidian/知识库}"
fi
VAULT="${VAULT/#~/$HOME}"

echo "[unpack] 知识库 → $VAULT"
mkdir -p "$VAULT"
tar -xzf "$ARCHIVE" -C "$VAULT" Claude方案/ && echo "[unpack]   Claude方案/ ✓"

# 首页移到 vault 根
if [ -f "$VAULT/Claude方案/_知识库首页.md" ]; then
    mv "$VAULT/Claude方案/_知识库首页.md" "$VAULT/_知识库首页.md" 2>/dev/null || true
    echo "[unpack]   _知识库首页.md → vault 根 ✓"
fi

echo "[unpack] transcript → $HOME/.claude/"
mkdir -p "$HOME/.claude"
tar -xzf "$ARCHIVE" -C "$HOME" .claude/projects/ && echo "[unpack]   transcript ✓"

echo
echo "[unpack] 完成。接下来："
echo "  git clone https://github.com/hjm4839-coder/checkpoint.git ~/obsidian"
echo "  cd ~/obsidian && ./install.sh"
