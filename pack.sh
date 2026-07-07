#!/usr/bin/env bash
# checkpoint 知识库打包脚本（旧电脑运行）
# 打包 Claude方案/ + transcript，生成一个 tar.gz 用于迁移到新电脑。
set -euo pipefail

VAULT="${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}"
PLANS="$VAULT/Claude方案"
PROJECTS="$HOME/.claude/projects"
OUT="checkpoint-migrate-$(date +%Y%m%d-%H%M).tar.gz"

echo "[pack] vault: $VAULT"
echo "[pack] transcript: $PROJECTS"

if [ ! -d "$PLANS" ]; then
    echo "[pack] Claude方案/ 不存在，无需打包"
    exit 1
fi

tar -czf "$OUT" \
    -C "$VAULT" Claude方案 \
    -C "$HOME" .claude/projects 2>/dev/null || true

SIZE=$(du -h "$OUT" | cut -f1)
echo "[pack] → $OUT ($SIZE)"
echo "[pack] 把 $OUT 传到新电脑，然后跑 unpack.sh"
