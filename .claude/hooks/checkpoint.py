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
        "topic": "", "category": [], "tags": [], "keywords": [],
        "user_prompts": [], "written_files": set(), "all_writes": set(),
        "projects": set(), "last_was_conclusion": False,
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
            # Claude Code 注入到 transcript 的系统消息（含各类 XML 标签）
            for tag in (
                "<command-name>", "<command-message>", "<command-args>",
                "<local-command-stdout>", "<local-command-caveat>",
                "<task-notification>", "<task-id>", "<system-reminder>",
                "<tool-use-id>", "<output-file>",
            ):
                if tag in msg:
                    return False
            if "Base directory for this skill" in msg:
                return False
            return len(msg) >= 4

        def is_good_topic_candidate(msg: str) -> bool:
            """排除含表格/代码/管道符的提问，选适合做主题的自然语言。"""
            if "─" in msg or "│" in msg or "└" in msg or "┌" in msg or "├" in msg:
                return False
            if "```" in msg or "|" in msg:
                return False
            return len(msg) <= 300

        real_prompts = [m for m in user_messages if is_real_prompt(m)]
        # 话题从自然语言提问中取（排除表格/代码块）
        topic_candidates = [m for m in real_prompts if is_good_topic_candidate(m)]
        if topic_candidates:
            result["topic"] = max(topic_candidates, key=len)[:200].replace("\n", " ").replace("\r", " ").strip()
        elif real_prompts:
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


def _extract_response_text(data: dict):
    """从多种 API 响应格式中提取文本返回（Anthropic / OpenAI / 网关等），失败返回 None。"""
    if not isinstance(data, dict):
        return None
    # Anthropic Messages 格式（含思考型模型如 deepseek-v4-pro）
    content = data.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                return block["text"].strip()
        # 没有 text 块：思考型模型只产 thinking（指令太长吞了 token 预算）——不回退到 thinking，
        # 它的内容是推理过程不是答案，强行用作标签反而污染。返回 None，由调用方兜底。
    # OpenAI Chat Completions 格式
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {})
        if isinstance(msg, dict):
            t = msg.get("content", "")
            if t:
                return str(t).strip()
    # 其他常见简化格式（部分网关/代理返回）
    for key in ("text", "output", "response", "result", "answer"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


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
        text = _extract_response_text(data)
        return text if text else None
    except Exception as e:
        print(f"[obsidian-hook] LLM call failed: {e}", file=sys.stderr)
        return None


def synthesize_topic_and_tags(user_prompts, written_files=None):
    """一次 LLM 调用，返回 {'topic': str|None, 'category': list, 'tags': list, 'keywords': list}。

    category = 1-2 个大类（宽泛领域），tags = 2-4 个小类（具体技术/场景）。
    """
    written_files = written_files or []
    if not user_prompts and not written_files:
        return {"topic": None, "category": [], "tags": [], "keywords": []}
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
        + "。请综合判断真实主题，输出四行：\n"
        "第1行：用不超过20个汉字概括主题，不要句末标点/引号/解释。\n"
        "第2行：1-2 个大类标签（逗号分隔），表示宽泛领域/学科（如 技术开发、运维管理、"
        "产品设计、知识管理、日常对话、前端、后端、基础设施，可自由发挥）。\n"
        "第3行：2-4 个小类标签（逗号分隔），表示具体技术/模块/场景，"
        "可用 / 表示层级（如 前端/Vue、obsidian/配置、shell/Netplan）。\n"
        "第4行：1-3 个补充关键词（逗号分隔），用于精确搜索。\n\n"
        + prompts_text
        + files_text
    )
    text = _llm_post({"max_tokens": 500, "messages": [{"role": "user", "content": instruction}]})
    if not text:
        # LLM 完全失败 → 从文件路径兜底
        tags, keywords = _fallback_tags_from_files(written_files or [])
        return {"topic": None, "category": [], "tags": tags, "keywords": keywords}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    topic = lines[0].strip("\"'“”‘’。.：: ")[:60] if lines else None
    category = []
    if len(lines) >= 2:
        for t in re.split(r"[,，、;；\s]+", lines[1]):
            t = t.strip().strip("#").strip("\"'“”‘’。.：: ")
            if t and t not in category:
                category.append(t)
        category = category[:2]
    tags = []
    if len(lines) >= 3:
        for t in re.split(r"[,，、;；\s]+", lines[2]):
            t = t.strip().strip("#").strip("\"'“”‘’。.：: ")
            if t and t not in tags:
                tags.append(t)
        tags = tags[:4]
    keywords = []
    if len(lines) >= 4:
        for t in re.split(r"[,，、;；\s]+", lines[3]):
            t = t.strip().strip("#").strip("\"'“”‘’。.：: ")
            if t and t not in keywords:
                keywords.append(t)
        keywords = keywords[:3]
    # LLM 失败时从文件路径兜底
    if not tags and written_files:
        tags, keywords = _fallback_tags_from_files(written_files)
    return {"topic": topic, "category": category, "tags": tags, "keywords": keywords}



def _fallback_tags_from_files(files):
    """LLM 失败时，从写/改的文件路径中提取标签和关键词。"""
    SKIP = {"", "~", "home", "user", "users", "projects", "src", "code", "dev",
            "desktop", "documents", "downloads", "tmp", "var", "opt", "etc", "usr",
            "library", "applications", "claude", "hooks", "skills", "memory",
            "checkpoint-convention", "readme-update-rule", "settings", "ouyangkai"}
    tags, keywords = [], []
    seen = set()
    for f in sorted(files):
        for p in Path(f).parts:
            p_clean = p.strip().lower()
            base = p_clean.split(".")[0]  # 去扩展名
            if not base or base in SKIP or base.startswith("."):
                continue
            if base not in seen:
                seen.add(base)
                if len(tags) < 5:
                    tags.append(base)
                elif len(keywords) < 3:
                    keywords.append(base)
    # 文件名作关键词（取最后有意义的）
    for f in sorted(files):
        stem = Path(f).stem.strip()
        if stem and stem not in seen and stem not in SKIP:
            seen.add(stem)
            if len(keywords) < 3:
                keywords.append(stem)
    return tags[:5], keywords[:3]



_FORBIDDEN_FILENAME_RE = re.compile(r'[/\\:*?"<>|\r\n\t]')
_BOXDRAW_RE = re.compile(r"[─-╿]")


def sanitize_filename(name: str) -> str:
    """把主题转成合法文件名：去禁止字符/制表符、折叠空白、截断 80 字符。"""
    name = _BOXDRAW_RE.sub("", name or "")
    name = _FORBIDDEN_FILENAME_RE.sub("_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > 80:
        name = name[:80].rstrip()
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



def generate_session_note(session_id: str, ctx: dict, status: str, related: list = None) -> str:
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
category: {json.dumps(ctx.get('category', []), ensure_ascii=False)}
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

{"## 相关会话\n\n" + "\n".join(f"- [[{r}]]" for r in related) + "\n\n---\n" if related else ""}
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


def find_related_notes(tags: list, current_stem: str) -> list:
    """找 tag 重叠 ≥2 的已有笔记，返回 stem 列表（最多 5 个）。"""
    related = []
    for n in sorted(NOTE_DIR.glob("*.md")):
        if n.stem == current_stem:
            continue
        existing = read_frontmatter_list(n, "tags")
        overlap = sum(1 for t in tags if t in existing)
        if overlap >= 2:
            related.append(n.stem)
    return related[:5]


def _count_tags_in_dir(d: Path, tag_counts: dict):
    """统计一个目录下所有 .md 的 tags（递归一层）。"""
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        tm = re.search(r'^tags:\s*(\[.*\])', text, re.MULTILINE)
        if tm:
            try:
                for t in json.loads(tm.group(1)):
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except Exception:
                pass


def update_dashboard():
    """更新知识库首页：概览/状态/大类/小类/待恢复列表。"""
    notes = list(NOTE_DIR.glob("*.md"))
    total = len(notes)
    status_counts = {"completed": 0, "interrupted": 0, "incomplete_archive": 0}
    tag_counts = {}
    cat_counts = {}
    pending_entries = []

    for n in notes:
        try:
            text = n.read_text(encoding="utf-8")
        except Exception:
            continue
        st = re.search(r'^status:\s*"([^"]+)"', text, re.MULTILINE)
        status = st.group(1) if st else ""
        if status in status_counts:
            status_counts[status] += 1
        # 分类
        for c in read_frontmatter_list(n, "category"):
            cat_counts[c] = cat_counts.get(c, 0) + 1
        tm = re.search(r'^tags:\s*(\[.*\])', text, re.MULTILINE)
        if tm:
            try:
                for t in json.loads(tm.group(1)):
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except Exception:
                pass
        # 待恢复
        if status in ("interrupted", "incomplete_archive"):
            d = re.search(r'^date:\s*"([^"]+)"', text, re.MULTILINE)
            h1 = re.search(r'^# (.+)', text, re.MULTILINE)
            display = h1.group(1) if h1 else n.stem
            emoji = STATUS_MAP.get(status, {}).get("emoji", "❓")
            pending_entries.append(f"- {emoji} [[{n.stem}|{display}]] — {d.group(1) if d else '?'}")

    # 归档文档标签
    doc_count = 0
    for sub in sorted(PLANS_DIR.glob("*/")):
        if sub.name in ("会话索引", "会话断点"):
            continue
        md_files = list(sub.glob("*.md"))
        doc_count += len(md_files)
        _count_tags_in_dir(sub, tag_counts)

    completed = status_counts["completed"]
    interrupted = status_counts["interrupted"]
    incomplete = status_counts["incomplete_archive"]
    pending = interrupted + incomplete
    rate = round(completed / total * 100) if total else 0
    hot_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]
    top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:6]

    dash = f"""# 知识库首页

> `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}` 自动刷新

---

## 概览

| 🗂 断点 | 📄 知识文档 | ⚠️ 待恢复 | ✅ 完成率 |
|---|---|---|---|
| **{total}** | **{doc_count}** | **{pending}** | **{rate}%** |

## 状态

`✅ 已完成 {completed}` `⚠️ 中断 {interrupted}` `📋 未归档 {incomplete}`

## 大类

{" ".join(f'`{t}` ({c})' for t, c in top_cats) if top_cats else '（暂无）'}

## 小类

{" ".join(f'`{t}`' for t, c in hot_tags) if hot_tags else '（暂无）'}

---

## 待恢复

{chr(10).join(pending_entries) if pending_entries else '✅ 全部完成，无待恢复'}

---

> 💡 `Claude方案/会话断点/` · `Claude方案/会话索引/` · Bases: `会话断点.base`
"""
    (PLANS_DIR / "知识库首页.md").write_text(dash, encoding="utf-8")


def main():
    transcript_path = ""
    session_id = "unknown"
    cwd = os.getcwd()
    force = "--force" in sys.argv
    # Lite 模式：元数据（主题/大类/标签/关键词）由对话模型提供，脚本不再调 LLM
    lite_topic = None
    lite_category = None
    lite_tags = None
    lite_keywords = None
    for flag in ("--topic", "--category", "--tags", "--keywords"):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                val = sys.argv[idx + 1]
                if flag == "--topic":
                    lite_topic = val
                elif flag == "--category":
                    lite_category = [t.strip() for t in val.split(",") if t.strip()]
                elif flag == "--tags":
                    lite_tags = [t.strip() for t in val.split(",") if t.strip()]
                elif flag == "--keywords":
                    lite_keywords = [t.strip() for t in val.split(",") if t.strip()]
    lite_mode = lite_topic is not None
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
    if lite_mode:
        # Lite 模式：元数据由对话模型生成，直接覆盖，不调 LLM。
        ctx["topic"] = lite_topic or "未命名会话"
        ctx["category"] = lite_category or []
        ctx["tags"] = [t for t in (lite_tags or []) if t]
        ctx["keywords"] = lite_keywords or []
        if existing_note:
            old_stem = existing_note.stem
            existing_note.unlink()
        fname = sanitize_filename(ctx["topic"])
        candidate = NOTE_DIR / f"{fname}.md"
        if candidate.exists():
            candidate = NOTE_DIR / f"{fname}-{session_id[:8]}.md"
        session_note_path = candidate
    elif existing_note and not force:
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
        existing_category = read_frontmatter_list(session_note_path, "category")
        existing_tags = read_frontmatter_list(session_note_path, "tags")
        existing_keywords = read_frontmatter_list(session_note_path, "keywords")
        if existing_category and existing_tags and existing_keywords:
            ctx["category"] = existing_category
            ctx["tags"] = existing_tags
            ctx["keywords"] = existing_keywords
        else:
            synth = synthesize_topic_and_tags(ctx["user_prompts"], ctx["all_writes"])
            ctx["category"] = existing_category or synth["category"]
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
        ctx["category"] = synth["category"]
        ctx["tags"] = synth["tags"]
        ctx["keywords"] = synth["keywords"]
        fname = sanitize_filename(ctx["topic"])
        candidate = NOTE_DIR / f"{fname}.md"
        if candidate.exists():
            candidate = NOTE_DIR / f"{fname}-{session_id[:8]}.md"
        session_note_path = candidate
    related = find_related_notes(ctx["tags"], session_note_path.stem)
    note_content = generate_session_note(session_id, ctx, status, related)
    session_note_path.write_text(note_content, encoding="utf-8")
    print(f"[obsidian-hook] Session checkpoint written: {session_note_path}")
    update_daily_index(INDEX_DIR, session_note_path, session_id, ctx, status)
    update_dashboard()
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
