#!/usr/bin/env python3
"""
Claude Code Stop Hook: 会话断点写入 Obsidian。
"""

import json
import sys
import os
import re
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Windows 默认 stdout/stderr 是 GBK(cp936)，输出 ✓/⚠️/📋/中文会 UnicodeEncodeError。强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT", "~/obsidian/知识库")).expanduser().resolve()
PLANS_DIR = VAULT_ROOT / "Claude方案"
PLANS_DIR_STR = str(PLANS_DIR)
INDEX_DIR = PLANS_DIR / "会话索引"      # 每日索引 YYYY-MM-DD.md
NOTE_DIR = PLANS_DIR / "会话断点"        # 单条会话断点 <主题>.md（与会话索引分开）
ALLOWED_TAGS = ["产品", "功能开发", "日常问答"]

# 强信号：明确指向“已形成方案/决策”的短语，命中 1 个即足以判定。
STRONG_PLAN_PATTERNS = [
    "方案如下", "方案是", "设计方案", "方案设计",
    "推荐方案", "最优方案", "备选方案", "技术方案",
    "架构设计", "架构如下", "系统架构",
    "实现计划", "实施步骤", "实现思路",
    "关键决策", "技术选型", "技术决策",
    "here is the plan", "here's the plan",
    "architecture design", "design decision",
    "implementation plan", "proposed solution",
    "这个方案", "按这个方案", "方案确认", "方案定了",
    "就这么设计", "最终方案", "敲定",
]
# 弱信号：日常讨论也常出现的词，需 ≥2 个不同词同时命中才算。
WEAK_PLAN_PATTERNS = [
    "背景", "核心思路", "取舍",
    "按这个来", "确定用", "定了",
]

CONCLUSION_MARKERS = [
    "全部完成", "以上就是", "总结一下", "没有问题的话",
    "有什么问题随时", "随时问我", "任务完成", "已全部",
    "没有其他问题", "overview",
]

STATUS_MAP = {
    "completed":          {"label": "正常结束", "emoji": "✅"},
    "interrupted":        {"label": "会话中断", "emoji": "⚠️"},
    "incomplete_archive": {"label": "方案未归档", "emoji": "📋"},
}


def extract_session_context(transcript_path: str) -> dict:
    result = {
        "topic": "", "tags": [], "keywords": [], "user_prompts": [], "written_files": set(),
        "all_writes": set(), "projects": set(), "last_was_conclusion": False,
        "has_substantive_work": False, "verbal_plan_detected": False,
        "verbal_plan_snippets": [], "used_plan_mode": False,
    }
    if not transcript_path or not os.path.exists(transcript_path):
        return result

    user_messages = []
    assistant_count = 0
    all_assistant_parts = []

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_type = entry.get("type", "")
                if entry_type == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        user_messages.append(content.strip())
                if entry_type == "assistant":
                    assistant_count += 1
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            all_assistant_parts.append(text)
                            if len(text) > 50:
                                result["has_substantive_work"] = True
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            if tool_name:
                                result["has_substantive_work"] = True
                            if tool_name in ("EnterPlanMode", "ExitPlanMode"):
                                result["used_plan_mode"] = True
                            if tool_name in ("Write", "Edit"):
                                tool_input = block.get("input", {})
                                file_path = tool_input.get("file_path", "")
                                if file_path:
                                    try:
                                        abs_path = str(Path(file_path).expanduser().resolve())
                                    except Exception:
                                        abs_path = file_path
                                    result["all_writes"].add(abs_path)
                                    # 只把 Claude方案/<项目名>/ 下的文件算作产出，
                                    # 排除 会话索引/ 簿记文件和直接放在 Claude方案/ 根的文件。
                                    if PLANS_DIR_STR in abs_path:
                                        try:
                                            rel = Path(abs_path).relative_to(PLANS_DIR)
                                            if len(rel.parts) > 1 and rel.parts[0] != "会话索引":
                                                result["written_files"].add(abs_path)
                                        except Exception:
                                            result["written_files"].add(abs_path)

        def is_real_prompt(msg: str) -> bool:
            # 跳过 slash 命令、skill 注入内容和过短的指令式消息。
            if msg.startswith("/"):
                return False
            if "<command-name>" in msg or "<command-message>" in msg:
                return False
            if "Base directory for this skill" in msg:
                return False
            return len(msg) >= 4

        real_prompts = [m for m in user_messages if is_real_prompt(m)]
        # 话题取最长的一条真实用户消息（信息量最大的近似）。
        if real_prompts:
            result["topic"] = max(real_prompts, key=len)[:200].replace("\n", " ").replace("\r", " ").strip()
        elif user_messages:
            result["topic"] = user_messages[0][:200].replace("\n", " ").replace("\r", " ").strip()
        result["user_prompts"] = [m[:200] for m in real_prompts]

        all_assistant_text = "".join(all_assistant_parts)
        all_text_lower = all_assistant_text.lower()
        covered_ranges = []

        def collect_hits(patterns):
            hits = []
            for pattern in patterns:
                idx = all_text_lower.find(pattern)
                if idx < 0:
                    continue
                if any(s <= idx <= e for s, e in covered_ranges):
                    continue
                covered_ranges.append((idx, idx + len(pattern)))
                start = max(0, idx - 40)
                end = min(len(all_assistant_text), idx + len(pattern) + 40)
                snippet = all_assistant_text[start:end].replace("\n", " ").strip()
                hits.append(f"...{snippet}...")
            return hits

        strong_hits = collect_hits(STRONG_PLAN_PATTERNS)
        weak_hits = collect_hits(WEAK_PLAN_PATTERNS)
        # 强信号命中 1 个即判定；弱信号需 ≥2 个不同词同时命中。
        if strong_hits or len(weak_hits) >= 2:
            result["verbal_plan_detected"] = True
            result["verbal_plan_snippets"] = (strong_hits + weak_hits)[:3]

        for f in result["written_files"]:
            try:
                rel = Path(f).relative_to(PLANS_DIR)
                if len(rel.parts) > 1:
                    result["projects"].add(rel.parts[0])
            except Exception:
                pass

        tail_text = all_assistant_text[-2000:].lower()
        result["last_was_conclusion"] = any(m in tail_text for m in CONCLUSION_MARKERS)

    except Exception as e:
        print(f"[obsidian-hook] Warning: transcript parsing error: {e}", file=sys.stderr)

    return result


def determine_session_status(ctx: dict) -> str:
    has_writes = len(ctx["written_files"]) > 0
    has_substance = ctx["has_substantive_work"]
    has_conclusion = ctx["last_was_conclusion"]
    plan_discussed = ctx["verbal_plan_detected"] or ctx["used_plan_mode"]
    # 改了代码（非 Claude方案/ 的 Write/Edit）但没归档：可能没干完
    has_code_edits = len(ctx["all_writes"] - ctx["written_files"]) > 0
    # 讨论了方案但没归档 → 提醒补写
    if plan_discussed and not has_writes:
        return "incomplete_archive"
    # 有归档 / 有收尾语 / 纯问答(无方案讨论且无代码改动) / 无实质工作 → 正常结束
    if has_writes or has_conclusion or not has_substance or (not plan_discussed and not has_code_edits):
        return "completed"
    return "interrupted"


def _llm_post(body_dict: dict):
    """发 Messages API 请求，返回响应文本或 None。

    Provider 无关：兼容真 Anthropic（ANTHROPIC_API_KEY + x-api-key）
    和网关代理（ANTHROPIC_AUTH_TOKEN + Bearer）。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    token = api_key or auth_token
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    model = (
        os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
    )
    if not token or not model:
        return None
    body_dict["model"] = model
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # 真 Anthropic 用 x-api-key；网关代理用 Authorization: Bearer。
    if api_key:
        headers["x-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {auth_token}"
    req = urllib.request.Request(
        base + "/v1/messages",
        data=json.dumps(body_dict).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        ssl_ctx = ssl.create_default_context()
        # 默认上下文找不到根证书时，依次尝试 Unix 路径和 Windows 的 certifi。
        if not ssl_ctx.get_ca_certs():
            loaded = False
            for cert_path in (
                "/etc/ssl/cert.pem",
                "/etc/ssl/certs/ca-certificates.crt",
                "/usr/local/share/cacert.pem",
            ):
                if os.path.exists(cert_path):
                    ssl_ctx.load_verify_locations(cert_path)
                    loaded = True
                    break
            if not loaded:
                try:
                    import certifi
                    ssl_ctx.load_verify_locations(certifi.where())
                except ImportError:
                    pass
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"].strip()
    except Exception as e:
        print(f"[obsidian-hook] LLM call failed: {e}", file=sys.stderr)
        return None


def synthesize_topic_and_tags(user_prompts, written_files=None):
    """一次 LLM 调用，返回 {'topic': str|None, 'tags': list, 'keywords': list}。

    同时参考用户提问和实际写/改的文件；两者不一致时以实际产出为准。
    tags 限定在 ALLOWED_TAGS（产品/功能开发/日常问答）里挑 1-2 个；
    keywords 是按内容额外给的 1-3 个自由关键词。
    """
    written_files = written_files or []
    if not user_prompts and not written_files:
        return {"topic": None, "tags": [], "keywords": []}
    prompts_text = "\n".join(f"{i+1}. {p}" for i, p in enumerate(user_prompts[:10]))
    home = os.path.expanduser("~")
    files_text = ""
    if written_files:
        files_text = "\n\n本次会话实际写/改的文件（这是真实产出，命名时优先以此为准）：\n" + "\n".join(
            f"- {f.replace(home, '~')}" for f in sorted(written_files)[:15]
        )
    instruction = (
        "下面是一次 Claude Code 会话中用户的连续提问"
        + ("及实际写/改的文件" if written_files else "")
        + "。请综合判断会话的真实主题（提问和产出不一致时，以实际产出为准），输出三行：\n"
        "第1行：用不超过20个汉字概括这次会话的主题，不要句末标点、引号或解释。\n"
        "第2行：从这三个固定分类里挑 1-2 个最贴合的，逗号分隔："
        "产品、功能开发、日常问答。"
        "（写功能/改代码→功能开发；讨论产品方向/需求→产品；纯问答/闲聊→日常问答）\n"
        "第3行：按会话具体内容给 1-3 个关键词标签，逗号分隔，"
        "描述实际涉及的技术/模块/场景（如 obsidian、hooks、登录、pts工时），不要 # 号。\n\n"
        + prompts_text
        + files_text
    )
    text = _llm_post({"max_tokens": 150, "messages": [{"role": "user", "content": instruction}]})
    if not text:
        return {"topic": None, "tags": [], "keywords": []}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    topic = lines[0].strip("\"'“”‘’。.：: ")[:60] if lines else None
    tags = []
    if len(lines) >= 2:
        for t in re.split(r"[,，、;；\s]+", lines[1]):
            t = t.strip().strip("#").strip("\"'“”‘’。.：: ")
            if t in ALLOWED_TAGS and t not in tags:
                tags.append(t)
        tags = tags[:2]
    if not tags:
        tags = ["日常问答"]
    keywords = []
    if len(lines) >= 3:
        for t in re.split(r"[,，、;；\s]+", lines[2]):
            t = t.strip().strip("#").strip("\"'“”‘’。.：: ")
            if t and t not in keywords and t not in ALLOWED_TAGS:
                keywords.append(t)
        keywords = keywords[:3]
    return {"topic": topic, "tags": tags, "keywords": keywords}



_FORBIDDEN_FILENAME_RE = re.compile(r'[/\\:*?"<>|\r\n\t]')


def sanitize_filename(name: str) -> str:
    """把主题转成合法文件名：去禁止字符、折叠空白。"""
    name = _FORBIDDEN_FILENAME_RE.sub("_", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name or "未命名"


def find_note_by_session(index_dir: Path, session_id: str):
    """在 NOTE_DIR 中按 frontmatter 的 session_id 查找已存在的断点笔记。"""
    for p in sorted(index_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        m = re.search(r'^session_id:\s*"([^"]+)"', text, re.MULTILINE)
        if m and m.group(1) == session_id:
            return p
    return None


def read_frontmatter_list(path: Path, key: str):
    """读取笔记 frontmatter 里某个 JSON 数组字段（如 tags/keywords）。无则返回 []。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    m = re.search(rf'^{key}:\s*(\[.*\])', text, re.MULTILINE)
    if not m:
        return []
    try:
        v = json.loads(m.group(1))
        return [str(t) for t in v] if isinstance(v, list) else []
    except Exception:
        return []



def generate_session_note(session_id: str, ctx: dict, status: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    short_id = session_id[:12] if len(session_id) > 12 else session_id
    label = STATUS_MAP.get(status, {}).get("label", status)
    topic = ctx["topic"] or "(未提取到话题)"
    meta_lines = [
        f"**状态**: {label}",
        f"**时间**: {timestamp}",
        f"**会话 ID**: `{session_id}`",
    ]
    if ctx["projects"]:
        meta_lines.append(f"**涉及项目**: {', '.join(sorted(ctx['projects']))}")
    meta_block = "\n".join(meta_lines)
    prompts_block = "\n".join(f"> {p}" for p in ctx["user_prompts"][:5]) if ctx["user_prompts"] else ""
    if ctx["written_files"]:
        links = [f"[[{Path(f).stem}]]" for f in sorted(ctx["written_files"])]
        yield_block = "\n".join(f"- {l}" for l in links)
    else:
        yield_block = "（本次未写入方案文件）"
    evidence_block = ""
    if status == "incomplete_archive":
        lines = []
        if ctx["used_plan_mode"]:
            lines.append("- **Plan Mode**: 使用了 EnterPlanMode / ExitPlanMode")
        if ctx["verbal_plan_snippets"]:
            lines.append("- **对话中检测到方案讨论**:")
            for snippet in ctx["verbal_plan_snippets"][:3]:
                lines.append(f"  > {snippet}")
        if lines:
            evidence_block = (
                "\n## 方案讨论证据\n\n"
                + "\n".join(lines)
                + "\n\n> 💡 **建议**：恢复此会话，要求 Claude 将方案写入 Obsidian。\n"
            )
    return f"""---
date: "{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
session_id: "{session_id}"
status: "{status}"
projects: {json.dumps(sorted(ctx['projects']), ensure_ascii=False)}
tags: {json.dumps(ctx.get('tags', []), ensure_ascii=False)}
keywords: {json.dumps(ctx.get('keywords', []), ensure_ascii=False)}
---

# {topic}

> {label} · {short_id}

{meta_block}

---

## 对话脉络

{prompts_block if prompts_block else '（未提取到对话脉络）'}

---

## 产出

{yield_block}
{evidence_block}
---

## 恢复

```bash
claude --resume {session_id}
```
"""


def remove_index_rows(index_dir: Path, old_stem: str):
    """--force 重命名笔记后，删掉每日索引里指向旧文件名的行。"""
    if not old_stem:
        return
    match_key = f"[[{old_stem}|"
    for idx in index_dir.glob("*.md"):
        try:
            lines = idx.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception:
            continue
        new_lines = [
            ln for ln in lines
            if not (match_key in ln and ln.lstrip().startswith("|"))
        ]
        if len(new_lines) != len(lines):
            idx.write_text("".join(new_lines), encoding="utf-8")


def update_daily_index(index_dir: Path, session_note_path: Path, session_id: str, ctx: dict, status: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    timestamp = datetime.now(timezone.utc).strftime("%H:%M")
    index_path = index_dir / f"{today}.md"
    emoji = STATUS_MAP.get(status, {}).get("emoji", "❓")
    topic = ctx["topic"][:60] if ctx["topic"] else "未记录话题"
    if ctx["written_files"]:
        note_names = [f"[[{Path(f).stem}]]" for f in sorted(ctx["written_files"])]
        yield_str = " · ".join(note_names)
    else:
        yield_str = "—"
    safe_topic = topic.replace("|", "\\|")
    safe_yield = yield_str.replace("|", "\\|")
    link_target = session_note_path.stem
    entry = f"| {timestamp} | {emoji} | [[{link_target}|{safe_topic}]] | {safe_yield} |\n"
    if not index_path.exists():
        header = f"""---
date: "{today}"
tags:
  - claude/会话索引
---

# 会话记录 - {today}

> 每日自动生成 · `Claude方案/会话索引/`

| 时间 | 状态 | 话题 | 产出 |
|---|---|---|---|
"""
        index_path.write_text(header, encoding="utf-8")
    # 同一 session 已有行则原地更新：按精确链接目标 [[stem| 匹配，避免子串误伤。
    match_key = f"[[{link_target}|"
    lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if match_key in line and line.lstrip().startswith("|"):
            lines[i] = entry
            index_path.write_text("".join(lines), encoding="utf-8")
            return
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(entry)


def main():
    transcript_path = ""
    session_id = "unknown"
    cwd = os.getcwd()
    force = "--force" in sys.argv
    if "--transcript" in sys.argv:
        idx = sys.argv.index("--transcript")
        if idx + 1 < len(sys.argv):
            transcript_path = sys.argv[idx + 1]
        if "--session-id" in sys.argv:
            sid_idx = sys.argv.index("--session-id")
            if sid_idx + 1 < len(sys.argv):
                session_id = sys.argv[sid_idx + 1]
        print(f"[obsidian-hook] Manual mode: transcript={transcript_path}, session={session_id}")
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            print("[obsidian-hook] No stdin input, skipping")
            sys.exit(0)
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            print("[obsidian-hook] Invalid JSON, skipping")
            sys.exit(0)
        transcript_path = event.get("transcript_path", "")
        session_id = event.get("session_id", "unknown")
        cwd = event.get("cwd", cwd)
    if not transcript_path:
        print("[obsidian-hook] No transcript path available, skipping")
        sys.exit(0)
    if not VAULT_ROOT.is_dir():
        print(f"[obsidian-hook] Vault not accessible: {VAULT_ROOT}, skipping")
        sys.exit(0)
    ctx = extract_session_context(transcript_path)
    status = determine_session_status(ctx)
    os.makedirs(INDEX_DIR, exist_ok=True)
    os.makedirs(NOTE_DIR, exist_ok=True)
    existing_note = find_note_by_session(NOTE_DIR, session_id)
    old_stem = None
    if existing_note and not force:
        # 已有笔记：沿用其文件名与 H1 标题（可能已被手动编辑成更贴切的主题）。
        session_note_path = existing_note
        try:
            for line in session_note_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    existing_title = stripped[2:].strip()
                    if existing_title:
                        ctx["topic"] = existing_title
                    break
        except Exception:
            pass
        # tags/keywords：已有则保留，没有则补一次综合（回填）。
        existing_tags = read_frontmatter_list(session_note_path, "tags")
        existing_keywords = read_frontmatter_list(session_note_path, "keywords")
        if existing_tags and existing_keywords:
            ctx["tags"] = existing_tags
            ctx["keywords"] = existing_keywords
        else:
            synth = synthesize_topic_and_tags(ctx["user_prompts"], ctx["all_writes"])
            ctx["tags"] = existing_tags or synth["tags"]
            ctx["keywords"] = existing_keywords or synth["keywords"]
    else:
        # 新笔记，或 --force 强制重新综合（删旧笔记、重新命名）。
        if existing_note and force:
            old_stem = existing_note.stem
            existing_note.unlink()
        synth = synthesize_topic_and_tags(ctx["user_prompts"], ctx["all_writes"])
        if synth["topic"]:
            ctx["topic"] = synth["topic"]
        ctx["tags"] = synth["tags"]
        ctx["keywords"] = synth["keywords"]
        fname = sanitize_filename(ctx["topic"])
        candidate = NOTE_DIR / f"{fname}.md"
        if candidate.exists():
            candidate = NOTE_DIR / f"{fname}-{session_id[:8]}.md"
        session_note_path = candidate
    note_content = generate_session_note(session_id, ctx, status)
    session_note_path.write_text(note_content, encoding="utf-8")
    print(f"[obsidian-hook] Session checkpoint written: {session_note_path}")
    update_daily_index(INDEX_DIR, session_note_path, session_id, ctx, status)
    # --force 重命名后，清掉旧文件名对应的每日索引行
    if old_stem:
        remove_index_rows(INDEX_DIR, old_stem)
    print(f"[obsidian-hook] Daily index updated")
    if status == "interrupted":
        msg = f"⚠️ 会话可能未完成。下次启动 Claude Code 时会自动检测断点，或手动执行: claude --resume {session_id}"
        print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "Stop", "permissionDecision": "allow"}}, ensure_ascii=False))
    elif status == "incomplete_archive":
        msg = f"📋 检测到方案讨论但未写入 Obsidian 知识库。建议恢复会话补写方案: claude --resume {session_id}"
        print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "Stop", "permissionDecision": "allow"}}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
