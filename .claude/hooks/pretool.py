#!/usr/bin/env python3
"""PreToolUse hook: Write/Edit 到 Claude方案/ 时提醒已有相关文档，避免重复。"""
import json, sys, os
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT", "~/obsidian/知识库")).expanduser().resolve()
PLANS_DIR = VAULT_ROOT / "Claude方案"


def main():
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        sys.exit(0)
    if not raw:
        sys.exit(0)
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    tool = event.get("tool_name", "") or event.get("tool", "")
    inp = event.get("tool_input", {})
    if tool not in ("Write", "Edit"):
        sys.exit(0)
    fp = inp.get("file_path", "")
    if not fp or "Claude方案" not in str(fp):
        sys.exit(0)
    fp = os.path.expanduser(str(fp))
    parent = os.path.dirname(fp)
    try:
        existing = [f for f in os.listdir(parent) if f.endswith(".md") and os.path.basename(fp) != f]
    except Exception:
        sys.exit(0)
    if not existing:
        sys.exit(0)
    out = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"注意：'{os.path.basename(parent)}' 目录已有 {len(existing)} 份文档（"
                + ", ".join(f"[[{os.path.splitext(f)[0]}]]" for f in existing[:5])
                + (" …" if len(existing) > 5 else "")
                + "），请先确认是否已有相关结论可引用，避免重新推导。"
            ),
        }
    }, ensure_ascii=False)
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
