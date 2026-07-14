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
NOTE_DIR = PLANS_DIR / "会话断点"        # 会话断点按月份保存：YYYY-MM/<主题>.md
EXPERIENCE_DIR = PLANS_DIR / "AI开发参考" # 按同类设计主题归类的跨项目复用经验
PROJECT_SUMMARY_NAME = "项目总结.md"      # 每个项目目录内的滚动项目摘要
PROJECT_SUMMARY_MAX_CHARS = 18000

# 强信号：明确指向”已形成方案/决策”的短语，命中 1 个即足以判定。
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

REDACTION_MARKER = "[REDACTED]"
_AUTH_BEARER_RE = re.compile(r"(Authorization\s*:\s*Bearer\s+)([A-Za-z0-9._~+/=-]+)", re.IGNORECASE)
_NAMESPACE_RE = re.compile(r"(X-Namespace\s*[:=]\s*)([A-Za-z0-9._~+/=-]+)", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|auth[_ -]?token|client[_ -]?secret|mysql[_ -]?password|db[_ -]?password|password|passwd|secret|token)\s*[:=]\s*)([\"']?)([A-Za-z0-9_./+=:@~%-]{8,})(\2)",
    re.IGNORECASE,
)
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{12,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def redact_sensitive_text(text):
    if not isinstance(text, str) or not text:
        return text

    def redact_assignment(match):
        quote = match.group(2) or ""
        return f"{match.group(1)}{quote}{REDACTION_MARKER}{quote}"

    text = _AUTH_BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTION_MARKER}", text)
    text = _NAMESPACE_RE.sub(lambda m: f"{m.group(1)}{REDACTION_MARKER}", text)
    text = _SECRET_ASSIGNMENT_RE.sub(redact_assignment, text)
    text = _SK_KEY_RE.sub(REDACTION_MARKER, text)
    text = _JWT_RE.sub(REDACTION_MARKER, text)
    return text


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
                        user_messages.append(redact_sensitive_text(content.strip()))
                if entry_type == "assistant":
                    assistant_count += 1
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            safe_text = redact_sensitive_text(text)
                            all_assistant_parts.append(safe_text)
                            if len(safe_text) > 50:
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
                                            if len(rel.parts) > 1 and rel.parts[0] not in ("会话索引", "会话断点", "AI开发参考"):
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

    except (json.JSONDecodeError, OSError) as e:
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
    # env 没有就读 settings.json 顶层 model（代理/托管场景常用）
    if not model:
        try:
            sp = os.path.expanduser("~/.claude/settings.json")
            d = json.load(open(sp, encoding="utf-8"))
            model = d.get("model", "") if isinstance(d, dict) else ""
        except Exception:
            pass
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
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
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
    SKIP = {"", "/", "\\", ".", "..", "~", "home", "user", "users", "projects", "src", "code", "dev",
            "desktop", "documents", "downloads", "tmp", "var", "private", "opt", "etc", "usr",
            "library", "applications", "claude", "claude方案", "知识库", "hooks", "skills", "memory",
            "worktrees", "checkpoint-convention", "readme-update-rule", "settings"}
    SKIP.update(part.casefold() for part in Path.home().parts if part not in {"/", "\\"})
    tags, keywords = [], []
    seen = set()
    for f in sorted(files):
        parts = Path(f).parts
        for index, p in enumerate(parts):
            p_clean = p.strip().lower()
            previous = parts[index - 1].strip().lower() if index else ""
            if previous == "worktrees":
                continue
            base = p_clean.split(".")[0]  # 去扩展名
            if not base or base in SKIP or base.startswith(".") or base.startswith("-"):
                continue
            if not _is_valid_metadata_value(base):
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
        if stem and stem.casefold() not in SKIP and stem.casefold() not in seen and _is_valid_metadata_value(stem):
            seen.add(stem.casefold())
            if len(keywords) < 3:
                keywords.append(stem)
    return tags[:5], keywords[:3]


_TOPIC_FILLER_RE = re.compile(
    r"^(请|帮我|麻烦你|给我|我想|我要|能不能|可以|请问|继续|先|再)\s*"
)


def _compact_topic_title(text: str, max_chars: int = 24) -> str:
    """把原始提问压成适合文件名/H1 的短主题。"""
    text = str(text or "")
    text = re.sub(r"https?://\S+", "网页链接", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[─-▟]", "", text)
    text = re.sub(r"[`*_#>\[\]（）(){}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？；;：:\"'“”‘’")
    text = _TOPIC_FILLER_RE.sub("", text).strip()
    for phrase in (
        "还记得之前做的", "还记得之前的", "还记得", "刚刚做的", "刚刚给",
        "上次讨论", "之前做的", "目前", "当前", "实际效果是否", "是否如此",
        "进行", "一下", "的方法", "怎么", "如何", "给我", "上传", "没有",
    ):
        text = text.replace(phrase, "")
    text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？；;：:\"'“”‘’")
    if len(text) > max_chars:
        for sep in ("，", ",", "。", ".", "；", ";", "？", "?", "！", "!"):
            if sep in text:
                first = text.split(sep, 1)[0].strip()
                if 4 <= len(first) <= max_chars:
                    return first
        text = text[:max_chars].rstrip()
    return text


def _looks_like_raw_prompt(title: str) -> bool:
    title = str(title or "").strip()
    if not title or title == "未命名会话":
        return True
    if len(title) > 30:
        return True
    if re.search(r"https?://|www\.", title, re.IGNORECASE):
        return True
    if re.search(r"\d{1,3}(?:\.\d{1,3}){3}", title) and len(title) > 16:
        return True
    if title.endswith(("吗", "吗？", "?", "？")):
        return True
    if "实际效果" in title and any(k in title for k in ("测试", "检测", "验证", "是否如此")):
        return True
    if "按照" in title or ("进行" in title and len(title) > 10):
        return True
    if "文件夹" in title and any(k in title for k in ("分类", "删除", "汇总")):
        return True
    raw_prefixes = (
        "对比一下", "刚刚", "还记得", "上次", "我要", "我想", "请", "帮我",
        "检测", "测试", "整理", "删除", "移除", "如果有一个",
    )
    return any(title.startswith(p) for p in raw_prefixes) and len(title) > 8


def _infer_topic_by_rules(text: str) -> str:
    hay = (text or "").lower()
    if not hay:
        return ""
    if "会话断点" in hay and ("标题" in hay or "命名" in hay):
        return "会话断点标题优化"
    if "知识库" in hay and any(k in hay for k in ("实际效果", "验证", "检测", "测试")):
        return "知识库实际效果验证"
    if "实际效果" in hay and any(k in hay for k in ("验证", "检测", "测试", "是否如此")):
        return "实际效果验证"
    if "AI开发参考" in hay and any(k in hay for k in ("同类设计", "归类", "规则")):
        return "AI开发参考同类归类"
    if "网站平台汇总" in hay and any(k in hay for k in ("文件夹", "分类", "汇总")):
        return "网站平台汇总分类"
    if "汇总文件夹" in hay and any(k in hay for k in ("文件夹", "分类", "删除")):
        return "网站平台汇总分类"
    if "整理知识库" in hay or ("知识库" in hay and "现有项目" in hay):
        return "知识库项目整理"
    if "空白文件" in hay:
        return "知识库空白文件清理"
    if "知识库" in hay and "功能" in hay:
        return "知识库功能梳理"
    if "github" in hay and any(k in hay for k in ("上传", "同步")):
        return "知识库优化GitHub同步"
    if "课程设计报告" in hay or ("报告" in hay and any(k in hay for k in ("docx", "模板", "截图"))):
        return "课程设计报告生成"
    if "潮流物品" in hay or "潮流物品交易平台" in hay:
        return "潮流物品交易平台部署"
    if "美妆" in hay and "服务器地址" in hay:
        return "美妆平台服务器地址查询"
    if "美妆" in hay and ("销售平台" in hay or "美妆平台" in hay):
        if any(k in hay for k in ("部署", "云服务器", "做一个", "124.220.67.208")):
            return "美妆销售平台部署"
    if "书店平台" in hay:
        if any(k in hay for k in ("部署", "8082", "服务器", "之前")):
            return "书店平台上下文恢复"
    if "销售平台" in hay and "二手平台" in hay and "token" in hay:
        return "销售二手平台Token对比"
    if "销售平台" in hay and "美妆" in hay and "token" in hay:
        return "销售美妆平台Token对比"
    if "ubuntu" in hay and "netplan" in hay:
        return "Ubuntu Netplan配置"
    if "ubuntu" in hay and "网卡" in hay:
        return "Ubuntu网卡切换脚本"
    if "本地服务" in hay and "连通性" in hay:
        return "本地服务连通性测试"
    if "会话索引" in hay and "导出" in hay:
        return "会话索引导出方法"
    if "verify" in hay and ("用法" in hay or "usage" in hay):
        return "Verify用法询问"
    if "taobao.com" in hay or "淘宝" in hay:
        return "淘宝页面参考分析"
    if "obs功能" in hay or ("obs" in hay and "功能" in hay):
        return "OBS功能说明"
    if "http://" in hay or "https://" in hay or "www." in hay:
        return "网页链接分析"
    return ""


def _topic_from_written_files(files) -> str:
    generic = {"项目总结", "README", "readme", "checkpoint"}
    candidates = []
    for f in sorted(files or []):
        p = Path(str(f))
        stem = p.stem.strip()
        if not stem or stem in generic:
            continue
        full = str(p)
        if p.name == "checkpoint.py":
            candidates.append("会话断点机制优化")
            continue
        if PLANS_DIR_STR in full:
            candidates.append(stem)
    for c in candidates:
        if c not in generic:
            return _compact_topic_title(c)
    return ""


def _basic_topic_title(text: str, max_chars: int = 24) -> str:
    text = str(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[─-▟]", "", text)
    text = re.sub(r"[`*_#>\[\]（）(){}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？；;：:\"'“”‘’")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def normalize_session_topic(topic: str, user_prompts=None, written_files=None, max_chars: int = 24) -> str:
    """统一生成会话断点标题：优先保留简洁主题，原始提问再按规则兜底。"""
    user_prompts = user_prompts or []
    written_files = written_files or []
    if not _looks_like_raw_prompt(str(topic or "")):
        basic = _basic_topic_title(topic, max_chars=max_chars)
        if basic:
            return basic
    source_text = "\n".join([str(topic or ""), *[str(p) for p in user_prompts], *[str(f) for f in written_files]])
    rule_topic = _infer_topic_by_rules(source_text)
    if rule_topic:
        return rule_topic[:max_chars]
    cleaned = _compact_topic_title(topic, max_chars=max_chars)
    file_topic = _topic_from_written_files(written_files)
    if _looks_like_raw_prompt(topic) and file_topic:
        return file_topic[:max_chars]
    if cleaned:
        return cleaned[:max_chars]
    if file_topic:
        return file_topic[:max_chars]
    return "未命名会话"



_FORBIDDEN_FILENAME_RE = re.compile(r'[/\\:*?"<>|\r\n\t]')
# Unicode Box Drawing 块 (U+2500–U+257F) + Block Elements (U+2580–U+259F)
_BOXDRAW_RE = re.compile("[─-▟]")


def sanitize_filename(name: str) -> str:
    """把主题转成合法文件名：去禁止字符/制表符、折叠空白、截断 80 字符。"""
    name = _BOXDRAW_RE.sub("", name or "")
    name = _FORBIDDEN_FILENAME_RE.sub("_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > 80:
        name = name[:80].rstrip()
    return name or "未命名"


def checkpoint_date_dir(base_dir: Path = NOTE_DIR, when: datetime = None) -> Path:
    dt = when or datetime.now(timezone.utc)
    return base_dir / dt.strftime("%Y-%m")


def available_checkpoint_path(note_dir: Path, topic: str, session_id: str) -> Path:
    """为同月同主题分配不覆盖已有笔记的路径。"""
    stem = sanitize_filename(topic)
    candidate = note_dir / f"{stem}.md"
    if not candidate.exists():
        return candidate
    short_id = (session_id or "unknown")[:8]
    candidate = note_dir / f"{stem}-{short_id}.md"
    suffix = 2
    while candidate.exists():
        candidate = note_dir / f"{stem}-{short_id}-{suffix}.md"
        suffix += 1
    return candidate


def note_link_target(path: Path) -> str:
    try:
        rel = path.relative_to(VAULT_ROOT).with_suffix("")
        return rel.as_posix()
    except Exception:
        return path.stem


def note_wikilink(path: Path, alias: str = None) -> str:
    target = note_link_target(path)
    return f"[[{target}|{alias}]]" if alias else f"[[{target}]]"


def iter_checkpoint_notes(base_dir: Path = NOTE_DIR):
    return sorted(base_dir.rglob("*.md"))


def find_note_by_session(index_dir: Path, session_id: str):
    """在 NOTE_DIR 中按 frontmatter 的 session_id 递归查找已存在的断点笔记。"""
    for p in iter_checkpoint_notes(index_dir):
        try:
            text = p.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        m = re.search(r'^session_id:\s*"([^"]+)"', text, re.MULTILINE)
        if m and m.group(1) == session_id:
            return p
    return None


def redact_markdown_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return False
    redacted = redact_sensitive_text(text)
    if redacted == text:
        return False
    path.write_text(redacted, encoding="utf-8")
    return True


def redact_written_plan_files(ctx: dict):
    for f in sorted(ctx.get("written_files", [])):
        try:
            p = Path(f)
            p.relative_to(PLANS_DIR)
        except Exception:
            continue
        if p.suffix.lower() == ".md":
            redact_markdown_file(p)


def _metadata_noise_values():
    values = {
        "/", "\\", ".", "..", "~",
        "home", "user", "users", "tmp", "var", "private", "opt", "etc", "usr",
        "desktop", "documents", "downloads", "library", "applications", "obsidian",
        "claude", "claude方案", "知识库", "hooks", "skills", "memory", "worktrees",
    }
    values.update(part.casefold() for part in Path.home().parts if part not in {"/", "\\"})
    return values


_METADATA_NOISE_VALUES = _metadata_noise_values()


def _is_valid_metadata_value(value) -> bool:
    text = str(value or "").strip().strip("\"'")
    if not text or text.casefold() in _METADATA_NOISE_VALUES:
        return False
    if text in {"/", "\\", ".", ".."}:
        return False
    return True


def _metadata_values(*values, limit: int = None):
    result = []
    seen = set()
    for value in values:
        if value is None:
            continue
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            text = str(item or "").strip().strip("\"'")
            if not _is_valid_metadata_value(text):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if limit and len(result) >= limit:
                return result
    return result


def build_aliases(topic=None, project=None, tags=None, keywords=None, existing=None):
    return _metadata_values(topic, project, existing or [], keywords or [], tags or [], limit=12)


def build_keywords(*values):
    return _metadata_values(*values, limit=8)


def build_checkpoint_keywords(keywords=None, tags=None):
    values = _metadata_values(keywords or [], limit=3)
    if values:
        return values
    tag_terms = [str(tag).rsplit("/", 1)[-1] for tag in (tags or [])]
    return _metadata_values(tag_terms, limit=3)


def _parse_frontmatter_list(text: str, key: str):
    match = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not match:
        return []
    inline = match.group(1).strip()
    if inline:
        try:
            value = json.loads(inline)
            if isinstance(value, list):
                return _metadata_values(value)
        except (json.JSONDecodeError, TypeError):
            return _metadata_values([t.strip() for t in re.split(r"[,，、]", inline.strip("[]")) if t.strip()])
    values = []
    for line in text[match.end():].splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if item:
            values.append(item.group(1).strip().strip("\"'"))
            continue
        break
    return _metadata_values(values)


def read_frontmatter_list(path: Path, key: str):
    """读取笔记 frontmatter 里某个数组字段。无则返回 []。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    return _parse_frontmatter_list(text, key)



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
    keywords = build_checkpoint_keywords(ctx.get("keywords", []), ctx.get("tags", []))
    aliases = build_aliases(topic, None, ctx.get("tags", []), keywords, ctx.get("aliases", []))
    content = redact_sensitive_text(f"""---
date: "{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
session_id: "{session_id}"
status: "{status}"
projects: {json.dumps(sorted(ctx['projects']), ensure_ascii=False)}
category: {json.dumps(ctx.get('category', []), ensure_ascii=False)}
tags: {json.dumps(ctx.get('tags', []), ensure_ascii=False)}
keywords: {json.dumps(keywords, ensure_ascii=False)}
aliases: {json.dumps(aliases, ensure_ascii=False)}
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
""")
    return content


def find_related_notes(tags: list, current_stem: str) -> list:
    """找 tag 重叠 ≥2 的已有笔记，返回 stem 列表（最多 5 个）。"""
    related = []
    for n in iter_checkpoint_notes(NOTE_DIR):
        if n.stem == current_stem or note_link_target(n) == current_stem:
            continue
        existing = read_frontmatter_list(n, "tags")
        overlap = sum(1 for t in tags if t in existing)
        if overlap >= 2:
            related.append(note_link_target(n))
    return related[:5]


def _read_text_limited(path: Path, max_chars: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""
    text = redact_sensitive_text(text)
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + "\n\n...（中间内容已截断）...\n\n" + tail


def _extract_h1(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", text or "", re.MULTILINE)
    if m:
        return m.group(1).strip()
    return fallback


def _project_doc_paths(project: str) -> list:
    """返回某个项目目录下可作为总结材料的归档文档。"""
    project_dir = PLANS_DIR / project
    if not project_dir.is_dir():
        return []
    docs = []
    for p in sorted(project_dir.rglob("*.md")):
        if p.name == PROJECT_SUMMARY_NAME:
            continue
        docs.append(p)
    return docs


def _collect_project_material(project: str, ctx: dict, session_note_path: Path) -> str:
    """收集项目归档、旧总结、当前断点，限制体积后交给 LLM。"""
    project_dir = PLANS_DIR / project
    parts = [f"项目名：{project}"]
    summary_path = project_dir / PROJECT_SUMMARY_NAME
    if summary_path.exists():
        parts.append("\n## 旧项目总结（用于增量更新）\n" + _read_text_limited(summary_path, 5000))

    if session_note_path and session_note_path.exists():
        parts.append("\n## 本次会话断点\n" + _read_text_limited(session_note_path, 4000))

    written_in_project = []
    for f in sorted(ctx.get("written_files", [])):
        try:
            p = Path(f)
            rel = p.relative_to(project_dir)
            if len(rel.parts) >= 1:
                written_in_project.append(p)
        except Exception:
            pass
    if written_in_project:
        parts.append("\n## 本次直接产出文件\n" + "\n".join(f"- {p.name}" for p in written_in_project))

    docs = _project_doc_paths(project)
    if docs:
        doc_parts = []
        for p in docs[:12]:
            text = _read_text_limited(p, 3500)
            title = _extract_h1(text, p.stem)
            doc_parts.append(f"\n### {title}（{p.name}）\n{text}")
        parts.append("\n## 项目归档文档\n" + "\n".join(doc_parts))

    material = "\n".join(parts)
    material = redact_sensitive_text(material)
    if len(material) > PROJECT_SUMMARY_MAX_CHARS:
        material = material[:PROJECT_SUMMARY_MAX_CHARS] + "\n\n...（项目材料超出长度，已截断）..."
    return material


def _frontmatter(title_tags: list, project: str, kind: str, keywords: list = None, aliases: list = None) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tags = ["claude/方案", "知识库/自动总结", kind] + [t for t in title_tags if t]
    unique_tags = _metadata_values(tags)
    unique_keywords = build_keywords(keywords or [], title_tags)
    unique_aliases = build_aliases(project, kind, unique_tags, unique_keywords, aliases or ["项目总结", "AI开发参考", "经验摘要"])
    return f"""---
date: {today}
project: {project}
tags: {json.dumps(unique_tags, ensure_ascii=False)}
keywords: {json.dumps(unique_keywords, ensure_ascii=False)}
aliases: {json.dumps(unique_aliases, ensure_ascii=False)}
---
"""


def _fallback_project_summary(project: str, ctx: dict, session_note_path: Path) -> str:
    files = []
    for f in sorted(ctx.get("written_files", [])):
        try:
            p = Path(f)
            if p.is_relative_to(PLANS_DIR / project):
                files.append(f"- [[{p.stem}]]")
        except Exception:
            pass
    prompts = "\n".join(f"- {p}" for p in ctx.get("user_prompts", [])[:5]) or "- （未提取到用户提问）"
    outputs = "\n".join(files) if files else "- （本次未新增项目归档）"
    session_link = note_wikilink(session_note_path) if session_note_path else "（无）"
    return f"""# {project} 项目总结

## 项目定位

（LLM 不可用，以下为规则兜底生成的滚动摘要。）

## 最近会话脉络

{prompts}

## 归档产出

{outputs}

## 当前状态

- 最近断点：{session_link}
- 会话状态：{ctx.get('status', 'unknown')}

## 后续恢复入口

优先阅读本文件，再阅读最近断点和归档产出，避免恢复完整长 transcript。
"""


def _fallback_experience(project: str, ctx: dict, session_note_path: Path, theme: str = "通用项目经验") -> str:
    session_link = note_wikilink(session_note_path) if session_note_path else "（无）"
    return f"""# {theme}

## 关键技术节点

- 项目结束或阶段结束后，用项目总结承接上下文，下一次只读取摘要和 1-2 篇关键归档，避免恢复完整长会话。
- 同类设计经验归入同一个主题文件，新项目只补充差异、反例和新增坑点。

## 创作思路

- 先判断项目类型、目标用户、交付物形态和验收方式，再决定设计表达与信息组织。
- 不写项目流水账，只沉淀可迁移的方法和判断标准。

## 实施思路

1. 新会话读取 `项目总结.md`。
2. 读取 `Claude方案/AI开发参考/{theme}.md`。
3. 只补读与当前任务最相关的 1-2 篇归档。
4. 阶段结束写方案/修复记录。
5. Stop Hook 自动刷新项目总结和同类设计经验。

## 踩坑点

- 不要把新项目接在旧项目长会话后继续开发，否则 `cache_read_input_tokens` 会被旧上下文放大。
- 不要只验证“接口存在”，要验证“前端入口可见、按钮可触发、用户能完成闭环”。
- 不要为每个项目重复新建经验文件；同类设计要合并到同一个主题文件。

## 来源

- 最近断点：{session_link}
- 本次项目：[[{project}]]
"""


def classify_experience_theme(project: str, ctx: dict) -> str:
    """把项目映射到AI开发参考主题；同类设计合并到一个文件。"""
    text = "\n".join([project] + list(ctx.get("user_prompts", []))).lower()
    rules = [
        ("平台项目通用技术节点与实施思路", ["平台", "电商", "商城", "交易", "销售", "美妆", "书店", "潮流", "二手", "b2c", "c2c", "javaweb"]),
        ("知识库自动总结与经验复用", ["obsidian", "知识库", "checkpoint", "断点", "项目总结", "AI开发参考", "hook", "hooks", "stop hook"]),
        ("课程报告与文档生成经验", ["报告", "课程设计", "docx", "模板", "截图", "word", "文档"]),
        ("前端UI设计与交互经验", ["前端", "ui", "界面", "视觉", "交互", "vue", "react", "css", "截图", "官网", "落地页", "网页", "页面设计", "品牌页"]),
        ("部署运维与数据隔离经验", ["部署", "docker", "nginx", "tomcat", "mysql", "服务器", "端口", "数据隔离", "adminer", "compose"]),
    ]
    for theme, keywords in rules:
        if any(k in text for k in keywords):
            return theme
    return "通用项目经验"


def _collect_theme_experience_material(theme: str, project: str, ctx: dict, session_note_path: Path) -> str:
    parts = [f"同类设计主题：{theme}", f"本次项目：{project}"]
    theme_path = EXPERIENCE_DIR / f"{sanitize_filename(theme)}.md"
    if theme_path.exists():
        parts.append("\n## 旧同类设计经验（用于增量更新、合并去重）\n" + _read_text_limited(theme_path, 7000))
    parts.append("\n## 本次项目材料\n" + _collect_project_material(project, ctx, session_note_path))
    material = "\n".join(parts)
    material = redact_sensitive_text(material)
    if len(material) > PROJECT_SUMMARY_MAX_CHARS:
        material = material[:PROJECT_SUMMARY_MAX_CHARS] + "\n\n...（同类经验材料超出长度，已截断）..."
    return material


def synthesize_reusable_experience(project: str, ctx: dict, session_note_path: Path, theme: str) -> str:
    material = _collect_theme_experience_material(theme, project, ctx, session_note_path)
    instruction = f"""你是工程经验沉淀助手。请基于下面材料，更新同类设计主题“{theme}”的AI开发参考文件。

要求：
- 只输出 Markdown 正文，不要输出 YAML frontmatter，不要代码围栏。
- 同类设计归类为一个文件：保留旧经验中的成熟结论，合并去重，只补充本次项目带来的差异、反例、新增技术节点和新增坑点。
- 不要复述项目流水账；不要按项目逐篇罗列；输出应像未来同类项目的首读指南。
- 必须包含这些二级标题：覆盖范围、关键技术节点、创作思路、实施思路、踩坑点、验收清单、下次执行顺序、可检索关键词、相关笔记。
- “关键技术节点”写技术/架构/配置/工具/数据模型等硬节点。
- “创作思路”写如何定方向、用户场景、风格、信息组织、取舍原则。
- “实施思路”写可复用的步骤和落地路径。
- “踩坑点”写真实容易出错的地方、触发条件、规避方法。
- 如果材料不足，用“待补充”标注，不要编造。

材料：
{material}
"""
    text = _llm_post({"max_tokens": 2600, "messages": [{"role": "user", "content": instruction}]})
    return text.strip() if text else _fallback_experience(project, ctx, session_note_path, theme)


def synthesize_project_summary(project: str, ctx: dict, session_note_path: Path) -> str:
    material = _collect_project_material(project, ctx, session_note_path)
    instruction = f"""你是 Obsidian 知识库整理助手。请基于下面材料，为项目“{project}”生成一份可供新 Claude Code 会话快速接手的滚动项目总结。

要求：
- 只输出 Markdown 正文，不要输出 YAML frontmatter，不要代码围栏。
- 控制在 1200-2000 字，重信息密度，避免寒暄。
- 目标是替代恢复完整 transcript，减少长上下文 token 消耗。
- 必须包含这些二级标题：项目定位、当前状态、关键架构与决策、已验证闭环、部署与运行要点、重要经验、后续恢复入口、相关笔记。
- “后续恢复入口”要明确说明下一次新会话优先读哪些笔记。
- 如果材料不足，用“待补充”标注，不要编造。

材料：
{material}
"""
    text = _llm_post({"max_tokens": 2400, "messages": [{"role": "user", "content": instruction}]})
    return text.strip() if text else _fallback_project_summary(project, ctx, session_note_path)




def update_project_knowledge(ctx: dict, session_note_path: Path):
    """为本次涉及的项目刷新滚动总结，并沉淀跨项目AI开发参考。"""
    projects = sorted(ctx.get("projects", []))
    if not projects:
        return []
    os.makedirs(EXPERIENCE_DIR, exist_ok=True)
    written = []
    for project in projects:
        project_dir = PLANS_DIR / project
        if not project_dir.is_dir():
            continue
        ctx_with_status = dict(ctx)
        ctx_with_status["status"] = ctx.get("status", "completed")

        summary_body = synthesize_project_summary(project, ctx_with_status, session_note_path)
        summary_path = project_dir / PROJECT_SUMMARY_NAME
        summary_keywords = build_keywords(
            read_frontmatter_list(summary_path, "keywords"),
            ctx_with_status.get("keywords", []),
        )
        summary_aliases = _metadata_values(
            read_frontmatter_list(summary_path, "aliases"),
            ctx_with_status.get("aliases", []),
        )
        summary_frontmatter = _frontmatter(
            ["项目总结"],
            project,
            "项目总结",
            keywords=summary_keywords,
            aliases=summary_aliases,
        )
        summary_content = redact_sensitive_text(
            summary_frontmatter + "\n" + summary_body.strip() + "\n"
        )
        summary_path.write_text(summary_content, encoding="utf-8")
        written.append(summary_path)

        theme = classify_experience_theme(project, ctx_with_status)
        experience_body = synthesize_reusable_experience(project, ctx_with_status, session_note_path, theme)
        exp_path = EXPERIENCE_DIR / f"{sanitize_filename(theme)}.md"
        experience_keywords = build_keywords(
            read_frontmatter_list(exp_path, "keywords"),
            ctx_with_status.get("keywords", []),
            [project, theme],
        )
        experience_aliases = _metadata_values(
            read_frontmatter_list(exp_path, "aliases"),
            ctx_with_status.get("aliases", []),
        )
        experience_frontmatter = _frontmatter(
            ["AI开发参考", theme],
            theme,
            "AI开发参考",
            keywords=experience_keywords,
            aliases=experience_aliases,
        )
        experience_content = redact_sensitive_text(
            experience_frontmatter + "\n" + experience_body.strip() + "\n"
        )
        exp_path.write_text(experience_content, encoding="utf-8")
        written.append(exp_path)
    return written


def _parse_cli():
    """解析命令行/stdin 输入，返回统一 dict。"""
    result = {"transcript": "", "session": "unknown", "cwd": os.getcwd(), "force": False,
              "lite": False, "lite_topic": None, "lite_category": [], "lite_tags": [], "lite_keywords": []}
    result["force"] = "--force" in sys.argv
    for flag, key, is_list in (("--topic", "lite_topic", False), ("--category", "lite_category", True),
                                ("--tags", "lite_tags", True), ("--keywords", "lite_keywords", True)):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                val = sys.argv[idx + 1]
                result[key] = [t.strip() for t in val.split(",") if t.strip()] if is_list else val
    result["lite"] = result["lite_topic"] is not None
    if "--transcript" in sys.argv:
        idx = sys.argv.index("--transcript")
        if idx + 1 < len(sys.argv):
            result["transcript"] = sys.argv[idx + 1]
        if "--session-id" in sys.argv:
            sid_idx = sys.argv.index("--session-id")
            if sid_idx + 1 < len(sys.argv):
                result["session"] = sys.argv[sid_idx + 1]
        print(f"[obsidian-hook] Manual mode: transcript={result['transcript']}, session={result['session']}")
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
        result["transcript"] = event.get("transcript_path", "")
        result["session"] = event.get("session_id", "unknown")
        result["cwd"] = event.get("cwd", result["cwd"])
    return result


def _read_frontmatter_all(path):
    """一趟读取笔记的 category/tags/keywords/aliases frontmatter 字段。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return [], [], [], []
    return (
        _parse_frontmatter_list(text, "category"),
        _parse_frontmatter_list(text, "tags"),
        _parse_frontmatter_list(text, "keywords"),
        _parse_frontmatter_list(text, "aliases"),
    )


def main():
    cli = _parse_cli()
    transcript_path, session_id, cwd = cli["transcript"], cli["session"], cli["cwd"]
    force, lite_mode = cli["force"], cli["lite"]
    lite_topic, lite_category, lite_tags, lite_keywords = cli["lite_topic"], cli["lite_category"], cli["lite_tags"], cli["lite_keywords"]

    if not transcript_path:
        print("[obsidian-hook] No transcript path available, skipping")
        sys.exit(0)
    if not VAULT_ROOT.is_dir():
        print(f"[obsidian-hook] Vault not accessible: {VAULT_ROOT}, skipping")
        sys.exit(0)
    ctx = extract_session_context(transcript_path)
    redact_written_plan_files(ctx)
    status = determine_session_status(ctx)
    ctx["status"] = status
    os.makedirs(NOTE_DIR, exist_ok=True)
    note_dir = checkpoint_date_dir(NOTE_DIR)
    os.makedirs(note_dir, exist_ok=True)
    os.makedirs(EXPERIENCE_DIR, exist_ok=True)
    existing_note = find_note_by_session(NOTE_DIR, session_id)
    if lite_mode:
        # Lite 模式：元数据由对话模型生成，直接覆盖，不调 LLM。
        ctx["topic"] = normalize_session_topic(
            lite_topic or ctx["topic"] or "未命名会话",
            ctx["user_prompts"],
            ctx["all_writes"],
        )
        ctx["category"] = lite_category or []
        ctx["tags"] = [t for t in (lite_tags or []) if t]
        ctx["keywords"] = lite_keywords or []
        if existing_note:
            existing_note.unlink()
        session_note_path = available_checkpoint_path(note_dir, ctx["topic"], session_id)
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
        # 已有元数据逐字段保留，只综合补齐缺失字段。
        existing_category, existing_tags, existing_keywords, existing_aliases = _read_frontmatter_all(session_note_path)
        ctx["aliases"] = existing_aliases
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
            existing_note.unlink()
        synth = synthesize_topic_and_tags(ctx["user_prompts"], ctx["all_writes"])
        if synth["topic"]:
            ctx["topic"] = synth["topic"]
        ctx["topic"] = normalize_session_topic(
            ctx["topic"],
            ctx["user_prompts"],
            ctx["all_writes"],
        )
        ctx["category"] = synth["category"]
        ctx["tags"] = synth["tags"]
        ctx["keywords"] = synth["keywords"]
        session_note_path = available_checkpoint_path(note_dir, ctx["topic"], session_id)
    ctx["keywords"] = build_checkpoint_keywords(ctx.get("keywords", []), ctx.get("tags", []))
    related = find_related_notes(ctx["tags"], note_link_target(session_note_path))
    note_content = generate_session_note(session_id, ctx, status, related)
    session_note_path.write_text(note_content, encoding="utf-8")
    print(f"[obsidian-hook] Session checkpoint written: {session_note_path}")
    project_notes = update_project_knowledge(ctx, session_note_path)
    if project_notes:
        print("[obsidian-hook] Project knowledge updated: " + ", ".join(str(p) for p in project_notes))
    if status == "interrupted":
        msg = f"⚠️ 会话可能未完成。下次启动 Claude Code 时会自动检测断点，或手动执行: claude --resume {session_id}"
        print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "Stop", "permissionDecision": "allow"}}, ensure_ascii=False))
    elif status == "incomplete_archive":
        msg = f"📋 检测到方案讨论但未写入 Obsidian 知识库。建议恢复会话补写方案: claude --resume {session_id}"
        print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "Stop", "permissionDecision": "allow"}}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
