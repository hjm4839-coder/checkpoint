#!/usr/bin/env bash
# checkpoint 机制安装脚本
# 把 Stop hook 注册到用户级 ~/.claude/settings.json（任意目录启动 claude 都生效）。
# 幂等：重复运行不会重复注册。不会动你已有的 env / theme / 其他 hook。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_PATH="$SCRIPT_DIR/.claude/hooks/checkpoint.py"
SETTINGS="$HOME/.claude/settings.json"
DEFAULT_VAULT="$HOME/obsidian/知识库"

echo "[checkpoint] 仓库目录: $SCRIPT_DIR"
echo "[checkpoint] hook 脚本: $HOOK_PATH"

if [ ! -f "$HOOK_PATH" ]; then
  echo "[checkpoint] ✗ 找不到 hook 脚本: $HOOK_PATH" >&2
  exit 1
fi

# 询问 Obsidian vault 路径（回车用默认）
echo
echo "断点笔记会写到你的 Obsidian vault 下的 Claude方案/ 目录。"
read -r -p "你的 Obsidian vault 路径 [默认: $DEFAULT_VAULT]: " VAULT
VAULT="${VAULT:-$DEFAULT_VAULT}"

mkdir -p "$HOME/.claude"

python3 - "$SETTINGS" "$HOOK_PATH" "$VAULT" <<'PY'
import json, sys, os, shutil
settings_path = os.path.expanduser(sys.argv[1])
hook_path = sys.argv[2]
vault = os.path.expanduser(sys.argv[3])

try:
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

if os.path.exists(settings_path):
    bak = settings_path + ".bak"
    shutil.copy2(settings_path, bak)
    print(f"[checkpoint] 已备份原配置: {bak}")

# 注册 Stop hook（幂等：去重旧条目）
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])
stop[:] = [
    e for e in stop
    if not any("checkpoint.py" in h.get("command", "") for h in e.get("hooks", []))
]
stop.append({"hooks": [{"type": "command", "command": f"python3 {hook_path}"}]})

# 写入 OBSIDIAN_VAULT（覆盖旧值，保留其他 env 不动）
env = data.setdefault("env", {})
env["OBSIDIAN_VAULT"] = vault

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"[checkpoint] ✓ Stop hook 已注册: python3 {hook_path}")
print(f"[checkpoint] ✓ OBSIDIAN_VAULT = {vault}")
PY

# 装 skill 到用户级（任意目录可用 /checkpoint）
SKILL_SRC="$SCRIPT_DIR/.claude/skills/checkpoint"
SKILL_DST="$HOME/.claude/skills/checkpoint"
mkdir -p "$HOME/.claude/skills"
rm -rf "$SKILL_DST"
cp -r "$SKILL_SRC" "$SKILL_DST"
# SKILL.md 里的 hook 路径替换成本机实际路径
sed -i.bak "s|~/obsidian/.claude/hooks/checkpoint.py|$HOOK_PATH|g" "$SKILL_DST/SKILL.md"
rm -f "$SKILL_DST/SKILL.md.bak"
echo "[checkpoint] ✓ /checkpoint skill 已装到 $SKILL_DST"

# 若用户还没用户级 CLAUDE.md，创建带归档约定的模板
USER_CLAUDE="$HOME/.claude/CLAUDE.md"
if [ ! -f "$USER_CLAUDE" ]; then
    mkdir -p "$HOME/.claude"
    cat > "$USER_CLAUDE" <<'CLMD'
# 全局指令

## 方案归档

方案敲定后直接 Write 到 `$OBSIDIAN_VAULT/Claude方案/<项目名>/<方案标题>.md`。
`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`，可通过环境变量覆盖。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [claude/方案, ...]
---
# 标题
## 背景  ## 方案  ## 关键决策  ## 实施步骤  ## 相关笔记
```

归档后会话断点会自动变 ✅，并链接到方案文件。
CLMD
    echo "[checkpoint] ✓ 已创建 $USER_CLAUDE（全局归档指令）"
else
    echo "[checkpoint]   $USER_CLAUDE 已存在，跳过（如需归档指令请手动合并）"
fi

cat <<EOF

[checkpoint] 安装完成。

  - API 凭证：Claude Code 已配的 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 自动复用，无需额外配置。
  - vault 路径已写入 settings.json 的 env.OBSIDIAN_VAULT，想改重跑本脚本即可。
  - 新开一个 claude 会话即生效（当前会话不重载 hook）。

卸载：删 ~/.claude/settings.json 里 hooks.Stop 中指向 checkpoint.py 的条目（或恢复 .bak）。
EOF
