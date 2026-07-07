#!/usr/bin/env bash
# checkpoint 知识库解包脚本（新电脑运行）
set -euo pipefail

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "用法: ./unpack.sh <checkpoint-migrate-xxx.tar.gz>"
    exit 1
fi

VAULT="${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}"

echo "[unpack] 知识库 → $VAULT"
mkdir -p "$VAULT"
tar -xzf "$ARCHIVE" -C "$VAULT" Claude方案/ 2>/dev/null && echo "[unpack]   Claude方案/ ✓"
echo "[unpack] transcript → $HOME/.claude/"
mkdir -p "$HOME/.claude"
tar -xzf "$ARCHIVE" -C "$HOME" .claude/projects/ 2>/dev/null && echo "[unpack]   .claude/projects/ ✓"

echo
echo "[unpack] 完成。clone 仓库 → install："
echo "  git clone https://github.com/hjm4839-coder/checkpoint.git ~/obsidian"
echo "  cd ~/obsidian && ./install.sh"
