#!/usr/bin/env bash
# checkpoint 机制安装脚本
# 把 Stop hook 注册到用户级 ~/.claude/settings.json（任意目录启动 claude 都生效）。
# 幂等：重复运行不会重复注册。不会动你已有的 env / theme / 其他 hook。
set -euo pipefail

LITE=false
if [ "${1:-}" = "--lite" ]; then
    LITE=true
    echo "[checkpoint] 安装模式: Lite（仅手动 /checkpoint，不额外调 API）"
else
    echo "[checkpoint] 安装模式: Full（自动 Stop hook + 手动 /checkpoint）"
fi

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

if [ "$LITE" = false ]; then
  HOOK_FLAG="false"
else
  HOOK_FLAG="true"
fi

python3 - "$SETTINGS" "$HOOK_PATH" "$VAULT" "$HOOK_FLAG" <<'PY'
import json, sys, os, shutil
settings_path = os.path.expanduser(sys.argv[1])
hook_path = sys.argv[2]
vault = os.path.expanduser(sys.argv[3])
lite = sys.argv[4] == "true" if len(sys.argv) > 4 else False

try:
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

if os.path.exists(settings_path):
    bak = settings_path + ".bak"
    shutil.copy2(settings_path, bak)
    print(f"[checkpoint] 已备份原配置: {bak}")

# 写入 OBSIDIAN_VAULT（覆盖旧值，保留其他 env）
env = data.setdefault("env", {})
env["OBSIDIAN_VAULT"] = vault

# 非 Lite 模式才注册 Stop hook
if not lite:
    hooks = data.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    stop[:] = [
        e for e in stop
        if not any("checkpoint.py" in h.get("command", "") for h in e.get("hooks", []))
    ]
    stop.append({"hooks": [{"type": "command", "command": f"python3 {hook_path}"}]})
    print(f"[checkpoint] ✓ Stop hook 已注册: python3 {hook_path}")
else:
    print("[checkpoint]   Lite 模式，跳过 Stop hook（仅手动 /checkpoint）")

# PreToolUse hook：写 Claude方案/ 文件时提醒已有文档（两模式都装）
pretool_path = os.path.join(os.path.dirname(hook_path), "pretool.py")
if os.path.exists(pretool_path):
    hooks = data.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    pre[:] = [e for e in pre if not any("pretool.py" in h.get("command","") for h in e.get("hooks",[]))]
    pre.append({"hooks": [{"type": "command", "command": f"python3 {pretool_path}"}]})
    print(f"[checkpoint] ✓ PreToolUse hook 已注册")

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"[checkpoint] ✓ OBSIDIAN_VAULT = {vault}")
PY

# 装 skill 到用户级（任意目录可用 /checkpoint）
if [ "$LITE" = true ]; then
    SKILL_SRC="$SCRIPT_DIR/.claude/skills/checkpoint-lite"
else
    SKILL_SRC="$SCRIPT_DIR/.claude/skills/checkpoint"
fi
SKILL_DST="$HOME/.claude/skills/checkpoint"
mkdir -p "$HOME/.claude/skills"
rm -rf "$SKILL_DST"
cp -r "$SKILL_SRC" "$SKILL_DST"
# SKILL.md 里的 hook 路径替换成本机实际路径
sed -i.bak "s|~/obsidian/.claude/hooks/checkpoint.py|$HOOK_PATH|g" "$SKILL_DST/SKILL.md"
rm -f "$SKILL_DST/SKILL.md.bak"
echo "[checkpoint] ✓ /checkpoint skill 已装到 $SKILL_DST"

# 装 synthesize skill
SYNTH_SRC="$SCRIPT_DIR/.claude/skills/synthesize"
SYNTH_DST="$HOME/.claude/skills/synthesize"
if [ -d "$SYNTH_SRC" ]; then
    rm -rf "$SYNTH_DST"
    cp -r "$SYNTH_SRC" "$SYNTH_DST"
    echo "[checkpoint] ✓ /synthesize skill 已装到 $SYNTH_DST"
fi

# 装 search skill
SEARCH_SRC="$SCRIPT_DIR/.claude/skills/search"
SEARCH_DST="$HOME/.claude/skills/search"
if [ -d "$SEARCH_SRC" ]; then
    rm -rf "$SEARCH_DST"
    cp -r "$SEARCH_SRC" "$SEARCH_DST"
    echo "[checkpoint] ✓ /search skill 已装到 $SEARCH_DST"
fi

# 若用户还没用户级 CLAUDE.md，创建带归档约定的模板
USER_CLAUDE="$HOME/.claude/CLAUDE.md"
if [ ! -f "$USER_CLAUDE" ]; then
    mkdir -p "$HOME/.claude"
    cat > "$USER_CLAUDE" <<'CLMD'
# 全局指令

## 回答前必须检索知识库

在回答任何技术、方案、配置、运维、开发类问题之前，必须先搜索知识库：

1. 从用户问题中提取 1-3 个关键词
2. `grep -l "<关键词>" $OBSIDIAN_VAULT/Claude方案/*/*.md` 找匹配文档
3. 同时 grep 标签：`grep -l 'tags:.*<关键词>' $OBSIDIAN_VAULT/Claude方案/*/*.md`
4. Read 最相关的 1-2 篇，已有结论直接引用，只补充新内容
5. 读了 2 篇没找到 → 告诉用户"知识库暂无相关记录"，然后正常回答
6. 纯闲聊/简单问答可跳过检索

知识库位置：`$OBSIDIAN_VAULT/Claude方案/`（默认 `~/obsidian/知识库/Claude方案/`）

## 方案归档

方案敲定后直接 Write 到 `$OBSIDIAN_VAULT/Claude方案/<项目名>/<方案标题>.md`。
`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`，可通过环境变量覆盖。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [claude/方案, <分类标签>, <关键词>]
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

[checkpoint] 安装完成（$([ "$LITE" = true ] && echo 'Lite' || echo 'Full') 模式）。

  - API / LLM：Full 模式自动调 LLM 起标题打标签；Lite 模式仅手动 /checkpoint，用对话模型生成元数据，不额外调 API。
  - vault 路径已写入 settings.json 的 env.OBSIDIAN_VAULT，想改重跑本脚本即可。
  $([ "$LITE" = false ] && echo '- 新开一个 claude 会话即生效（当前会话不重载 hook）。')
  $([ "$LITE" = true ] && echo '- 仅 /checkpoint 手动生成断点，无自动 Stop hook。')

卸载：删 ~/.claude/settings.json 里 hooks.Stop 中指向 checkpoint.py 的条目（或恢复 .bak）。
EOF
