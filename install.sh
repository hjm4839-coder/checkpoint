#!/usr/bin/env bash
# checkpoint 机制安装脚本
# 把 Stop hook 注册到用户级 ~/.claude/settings.json（任意目录启动 claude 都生效）。
# 幂等：重复运行不会重复注册。不会动你已有的 env / theme / 其他 hook。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_PATH="$SCRIPT_DIR/.claude/hooks/checkpoint.py"
SETTINGS="$HOME/.claude/settings.json"

echo "[checkpoint] 仓库目录: $SCRIPT_DIR"
echo "[checkpoint] hook 脚本: $HOOK_PATH"

if [ ! -f "$HOOK_PATH" ]; then
  echo "[checkpoint] ✗ 找不到 hook 脚本: $HOOK_PATH" >&2
  exit 1
fi

mkdir -p "$HOME/.claude"

python3 - "$SETTINGS" "$HOOK_PATH" <<'PY'
import json, sys, os, shutil
settings_path = os.path.expanduser(sys.argv[1])
hook_path = sys.argv[2]

try:
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

if os.path.exists(settings_path):
    bak = settings_path + ".bak"
    shutil.copy2(settings_path, bak)
    print(f"[checkpoint] 已备份原配置: {bak}")

hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])

# 幂等：移除已指向 checkpoint.py 的旧条目，再添加新的
stop[:] = [
    e for e in stop
    if not any("checkpoint.py" in h.get("command", "") for h in e.get("hooks", []))
]
stop.append({"hooks": [{"type": "command", "command": f"python3 {hook_path}"}]})

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"[checkpoint] ✓ Stop hook 已注册到 {settings_path}")
print(f"[checkpoint]   命令: python3 {hook_path}")
PY

cat <<EOF

[checkpoint] 安装完成。

接下来：
  1. API 凭证：你 Claude Code 已配的 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 会自动复用，无需额外配置。
  2. Obsidian vault：默认 ~/obsidian/知识库。若在别处，在 ~/.claude/settings.json 的 env 里加：
       "OBSIDIAN_VAULT": "/你的/vault/路径"
  3. 新开一个 claude 会话即生效（当前会话不重载 hook）。

卸载：把 ~/.claude/settings.json 里 hooks.Stop 中指向 checkpoint.py 的条目删掉即可（或恢复 .bak）。
EOF
