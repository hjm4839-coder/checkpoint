#!/usr/bin/env python3
"""
Claude Code Stop Hook: 知识库结构健康检查。
每次会话结束时静默扫描，仅当发现问题才写入报告。
"""

import re
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT", "~/obsidian/知识库")).expanduser().resolve()
PLANS_DIR = VAULT_ROOT / "Claude方案"
REPORT_PATH = PLANS_DIR / "运维" / "知识库健康检查报告.md"
NOTE_DIR = PLANS_DIR / "会话断点"
THROTTLE_MINUTES = 60  # 上次报告 < 60 分钟且问题无变化则跳过

# ── 规则定义 ──────────────────────────────────────────────────────────

CHECKPOINT_FIELDS = ["date", "session_id", "status", "projects", "category", "tags", "keywords", "aliases"]

GITLAB_SPECIAL_RECORD_PATTERNS = [
    r"复测", r"Sidecar", r"sidecar", r"OAuth", r"oauth",
    r"钉钉自动工作流", r"网站钉钉",
]


# ── 工具函数 ──────────────────────────────────────────────────────────

def _md_files(rel_path: str = "") -> list[Path]:
    root = VAULT_ROOT / rel_path if rel_path else VAULT_ROOT
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.md"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def _parse_frontmatter(text: str) -> dict | None:
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    fm = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line:
            continue
        kv = re.match(r"^(\w+):\s*(.*)", line)
        if kv:
            key = kv.group(1)
            val = kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                items = re.findall(r'"([^"]*)"', val)
                fm[key] = items
            else:
                fm[key] = val.strip('"').strip("'")
    return fm


def _collect_wikilinks(text: str) -> set[str]:
    links = set()
    for m in re.finditer(r"\[\[(.+?)\]\]", text):
        inner = m.group(1)
        target = _link_target(inner)
        if target:
            links.add(target)
    return links


def _link_target(inner: str) -> str:
    inner = inner.replace("\\|", "|")
    target = inner
    i = 0
    while i < len(target):
        if target[i] == "\\" and i + 1 < len(target):
            i += 2
            continue
        if target[i] == "|":
            target = target[:i]
            break
        if target[i] == "#":
            target = target[:i]
            break
        i += 1
    return target.strip()


def _build_file_index() -> tuple[dict[str, Path], dict[str, Path]]:
    exact, ci = {}, {}
    for f in _md_files():
        try:
            rel = f.relative_to(VAULT_ROOT)
        except ValueError:
            continue
        key = rel.as_posix().replace(".md", "")
        exact[key] = f
        ci[key.lower()] = f
        exact[f.stem] = f
        ci[f.stem.lower()] = f
    return exact, ci


# ── 单项检查 ──────────────────────────────────────────────────────────

def check_empty_files() -> list[str]:
    issues = []
    for f in _md_files():
        try:
            if f.stat().st_size == 0:
                issues.append(f"空文件: `{f.relative_to(VAULT_ROOT)}`")
        except OSError:
            pass
    return issues


def check_checkpoint_frontmatter() -> list[str]:
    issues = []
    for f in _md_files("Claude方案/会话断点"):
        if f.name.startswith("_") or f.name == "会话断点导航.md":
            continue
        text = _read_text(f)
        if not text:
            continue
        fm = _parse_frontmatter(text)
        if fm is None:
            issues.append(f"缺少 frontmatter: `{f.relative_to(VAULT_ROOT)}`")
            continue
        for field in CHECKPOINT_FIELDS:
            if field not in fm:
                issues.append(f"缺少字段 `{field}`: `{f.relative_to(VAULT_ROOT)}`")
            elif field in ("projects", "category", "tags", "keywords", "aliases"):
                if not isinstance(fm[field], list):
                    issues.append(f"`{field}` 应为数组: `{f.relative_to(VAULT_ROOT)}`")
        raw = text[: text.find("\n---\n", 4) + 4] if text.startswith("---") else text[:500]
        if re.search(r"^\w+:\s*$", raw, re.MULTILINE):
            issues.append(f"frontmatter 可能存在多行列表残留: `{f.relative_to(VAULT_ROOT)}`")
    return issues


def check_directory_conventions() -> list[str]:
    issues = []
    gitlab_root = PLANS_DIR / "AI开发参考" / "gitlab"
    if gitlab_root.is_dir():
        for f in gitlab_root.glob("*.md"):
            if f.name in ("README.md",):
                continue
            if "项目学习" in f.name or "开发参考" in f.name or "补充" in f.name:
                continue
            name = f.stem
            for pattern in GITLAB_SPECIAL_RECORD_PATTERNS:
                if re.search(pattern, name):
                    issues.append(f"专项记录应在 `专项记录/` 子目录: `{f.relative_to(VAULT_ROOT)}`")
                    break
    for f in VAULT_ROOT.glob("*.md"):
        if f.name.startswith("."):
            continue
        if "checkpoint" in f.name.lower():
            issues.append(f"知识库根目录不应存放 checkpoint 文件: `{f.relative_to(VAULT_ROOT)}`")
    return issues


def check_dataview_config_position() -> list[str]:
    issues = []
    expected = NOTE_DIR / "会话断点导航.md"
    old = PLANS_DIR / "会话断点.base"
    if old.exists():
        issues.append(f"`会话断点.base` 还在旧位置，应移到 `会话断点/会话断点导航.md`")
    if not expected.exists():
        issues.append(f"缺少 `会话断点/会话断点导航.md`")
    for f in VAULT_ROOT.rglob("*.base"):
        if f != expected and f != old:
            issues.append(f"未知 .base 文件: `{f.relative_to(VAULT_ROOT)}`")
    return issues


def check_wikilinks() -> list[str]:
    file_index, file_index_ci = _build_file_index()
    IGNORE = {"wikilink", "placeholder", "占位符"}
    issues = []
    for src in _md_files():
        try:
            if src.relative_to(VAULT_ROOT) == REPORT_PATH.relative_to(VAULT_ROOT):
                continue
        except ValueError:
            pass
        text = _read_text(src)
        if not text:
            continue
        try:
            src_dir = src.relative_to(VAULT_ROOT).parent.as_posix()
        except ValueError:
            src_dir = ""
        links = _collect_wikilinks(text)
        for link in links:
            if link.lower() in IGNORE:
                continue
            file_part = link.split("#", 1)[0] if "#" in link else link
            if not file_part:
                continue
            if file_part in file_index or file_part.lower() in file_index_ci:
                continue
            if src_dir and src_dir != ".":
                rel = (src_dir + "/" + file_part).replace("//", "/")
                if rel in file_index or rel.lower() in file_index_ci:
                    continue
            stem = file_part.rsplit("/", 1)[-1]
            if stem in file_index:
                continue
            if _prefix_climb(src_dir, file_part, file_index, file_index_ci):
                continue
            issues.append(f"断裂链接 `[[{link}]]` 在 `{src.relative_to(VAULT_ROOT)}`")
    return issues


def _prefix_climb(src_dir: str, link: str, exact: dict, ci: dict) -> bool:
    parts = src_dir.split("/") if src_dir else []
    for i in range(len(parts), -1, -1):
        prefix = "/".join(parts[:i])
        cand = f"{prefix}/{link}" if prefix else link
        cand = cand.replace("//", "/")
        if cand in exact or cand.lower() in ci:
            return True
    return False


def check_Claude方案_root_cleanliness() -> list[str]:
    issues = []
    root = PLANS_DIR
    if not root.is_dir():
        return issues
    for f in root.glob("*"):
        if f.is_dir():
            continue
        if f.name == "项目总览.md":
            continue
        if f.name.startswith("."):
            continue
        if f.suffix == ".base":
            issues.append(f"Claude方案/ 根目录不应有 .base 文件: `{f.relative_to(VAULT_ROOT)}`")
            continue
        issues.append(f"Claude方案/ 根目录不应有散放文件: `{f.relative_to(VAULT_ROOT)}`")
    return issues


def check_project_directory_completeness() -> list[str]:
    issues = []
    root = PLANS_DIR
    if not root.is_dir():
        return issues
    META_DIRS = {"AI开发参考", "会话断点", "网站平台汇总", "运维"}
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if d.name in META_DIRS:
            continue
        if d.name.startswith("."):
            continue
        summary = d / "项目总结.md"
        if not summary.exists():
            issues.append(f"缺少项目总结: `{d.name}/项目总结.md`")
        overview = root / "项目总览.md"
        if overview.exists():
            text = _read_text(overview)
            if f"[[{d.name}/项目总结" not in text and f"[[{d.name}]]" not in text:
                issues.append(f"项目目录存在但未在 `项目总览.md` 中登记: `{d.name}/`")
    return issues


# ── 主流程 ────────────────────────────────────────────────────────────

def run_all_checks() -> list[str]:
    # 自生长诊断函数定义在文件末尾，在这里延迟引用
    all_issues = []
    CHECKS = [
        ("空文件", check_empty_files),
        ("断点 frontmatter", check_checkpoint_frontmatter),
        ("目录约定", check_directory_conventions),
        ("Dataview 配置位置", check_dataview_config_position),
        ("项目目录完整性", check_project_directory_completeness),
        ("Wiki-link 完整性", check_wikilinks),
        ("Claude方案 根目录清洁度", check_Claude方案_root_cleanliness),
        ("自生长建议", collect_self_growing_suggestions),
    ]
    for name, fn in CHECKS:
        try:
            result = fn()
            if result:
                all_issues.append(f"### {name} ({len(result)} 项)")
                all_issues.extend(f"- {r}" for r in result)
        except Exception as e:
            all_issues.append(f"### {name} (检查异常)")
            all_issues.append(f"- 执行出错: {e}")
    return all_issues


def _should_skip() -> bool:
    """上次报告 < THROTTLE_MINUTES 分钟且问题集合无变化则跳过。"""
    if not REPORT_PATH.exists():
        return False
    try:
        mtime = os.path.getmtime(str(REPORT_PATH))
        age = time.time() - mtime
        if age >= THROTTLE_MINUTES * 60:
            return False
    except OSError:
        return False

    # 时间不到 → 对比新旧问题集合，有变化才放行
    old_issues = _parse_issues_from_report()
    new_issues = set(run_all_checks())
    if new_issues - old_issues:
        return False  # 有新问题，放行
    return True


def _parse_issues_from_report() -> set[str]:
    """从已有报告中提取问题列表行。"""
    try:
        text = REPORT_PATH.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return set()
    lines = set()
    in_body = False
    for line in text.split("\n"):
        if line.startswith("---"):
            continue
        if line.startswith("## 修复提示"):
            break
        stripped = line.strip()
        if stripped.startswith("### "):
            in_body = True
            continue
        if in_body and stripped.startswith("- "):
            lines.add(stripped)
    return lines


def main():
    if _should_skip():
        return

    issues = run_all_checks()
    if not issues:
        return

    os.makedirs(REPORT_PATH.parent, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "\n".join(issues)
    content = f"""---
date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
project: 运维
tags: ["claude/方案", "运维", "知识库/健康检查"]
keywords: ["健康检查", "自动修复提示"]
aliases: ["知识库健康检查报告"]
---

# 知识库健康检查报告

> 自动生成 · {now}

{body}

---

## 修复提示

以上问题可通过 CLAUDE.md 中「知识库文件结构规范」的规则参考修复。
常见修复方式：
- 空文件 → 删除或补充内容
- frontmatter 格式 → 参考 `会话断点/会话断点导航.md` 同目录文件格式
- 断裂链接 → 更新 [[target]] 为实际文件名
- 文件位置错误 → 移动到对应子目录
"""
    REPORT_PATH.write_text(content, encoding="utf-8")
    print(f"[health-check] 发现 {len(issues)} 项问题，报告已写入 {REPORT_PATH}")


if __name__ == "__main__":
    main()


# ============================================================
# 自生长诊断 (Self-Growing Checks)
# ============================================================

def check_suggest_synthesize() -> list[str]:
    """同标签重叠≥3的断点≥5条 → 建议运行 /synthesize。"""
    suggestions = []
    # 收集断点的 tags
    tag_groups = {}
    for f in _md_files("Claude方案/会话断点"):
        if "导航" in f.name:
            continue
        fm = _parse_frontmatter(_read_text(f))
        if not fm:
            continue
        tags = fm.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        for t in tags:
            t = t.strip()
            if not t or t in ("/", "claude/方案"):
                continue
            tag_groups.setdefault(t, []).append(f.stem)
    # 找≥5条断点共享的标签
    for tag, stems in sorted(tag_groups.items(), key=lambda x: -len(x[1])):
        if len(stems) >= 5:
            suggestions.append(
                f"标签「{tag}」下已有 {len(stems)} 条断点（{', '.join(stems[:3])}…），"
                f"建议运行 `/synthesize` 合成为知识文档"
            )
        if len(suggestions) >= 5:
            break
    return suggestions


def check_suggest_merge() -> list[str]:
    """标题相似度≥70%的文档对 → 建议合并。"""
    suggestions = []
    docs = []
    for f in _md_files():
        try:
            rel = str(f.relative_to(PLANS_DIR))
        except ValueError:
            continue
        if rel.startswith("会话断点/") or rel.startswith("会话索引/"):
            continue
        if f.stem == "README":
            continue
        docs.append((rel, f.stem))
    # 简单判断：stem 包含相同核心词(≥3 chars) → 相似
    from difflib import SequenceMatcher
    seen = set()
    for i, (a_rel, a_stem) in enumerate(docs):
        for j, (b_rel, b_stem) in enumerate(docs):
            if i >= j:
                continue
            pair = tuple(sorted([a_rel, b_rel]))
            if pair in seen:
                continue
            seen.add(pair)
            ratio = SequenceMatcher(None, a_stem.lower(), b_stem.lower()).ratio()
            if ratio >= 0.7:
                suggestions.append(
                    f"「{a_stem}」与「{b_stem}」相似度 {ratio:.0%}，"
                    f"建议检查是否可合并为同一主题文档"
                )
        if len(suggestions) >= 5:
            break
    return suggestions[:5]


def check_contradiction() -> list[str]:
    """检测"不要""避免""禁止""注意"在不同文档中出现矛盾表述。"""
    suggestions = []
    warn_patterns = [
        (r"不要\s*(\S{2,10})", "不要"),
        (r"避免\s*(\S{2,10})", "避免"),
        (r"禁止\s*(\S{2,10})", "禁止"),
    ]
    doc_warnings = {}
    for f in _md_files():
        try:
            rel = str(f.relative_to(PLANS_DIR))
        except ValueError:
            continue
        if rel.startswith("会话断点/") or rel.startswith("会话索引/"):
            continue
        text = _read_text(f)
        if not text:
            continue
        phrases = set()
        for pat, prefix in warn_patterns:
            for m in re.findall(pat, text):
                phrases.add(f"{prefix}{m}")
        if phrases:
            doc_warnings[rel] = phrases
    # 找两个文档都有"不要X"但对 X 不同 → 不矛盾；相同 X 不同处理 → 矛盾
    # 简化: 同一 X 在两篇文档中一个说不要，一个说要 → 矛盾
    for i, (a_rel, a_warn) in enumerate(list(doc_warnings.items())[:20]):
        for j, (b_rel, b_warn) in enumerate(list(doc_warnings.items())[:20]):
            if i >= j:
                continue
            common = a_warn & b_warn
            if common:
                warnings_sample = ', '.join(list(common)[:3])
                suggestions.append(
                    f"「{a_rel}」和「{b_rel}」都存在「{warnings_sample}」警告/禁止项，"
                    f"建议人工裁决是否矛盾"
                )
        if len(suggestions) >= 5:
            break
    return suggestions[:5]


def check_stale_documents() -> list[str]:
    """超过 90 天未修改的 .md 文档 → 建议检查是否仍需保留。"""
    suggestions = []
    now = time.time()
    stale_seconds = 90 * 24 * 3600
    stale = []
    for f in _md_files():
        try:
            rel = str(f.relative_to(PLANS_DIR))
        except ValueError:
            continue
        if rel.startswith("会话断点/") or rel in ("项目总览.md",):
            continue
        try:
            mtime = os.path.getmtime(str(f))
        except OSError:
            continue
        if now - mtime > stale_seconds:
            stale.append((now - mtime, rel))
    stale.sort(key=lambda x: -x[0])
    for age_seconds, rel in stale[:5]:
        days = int(age_seconds / 86400)
        suggestions.append(f"「{rel}」已 {days} 天未修改，建议检查是否仍需保留或需更新")
    return suggestions


def collect_self_growing_suggestions() -> list[str]:
    """汇总所有自生长诊断建议。"""
    all_suggestions = []
    for check_fn, label in [
        (check_suggest_synthesize, "建议合成"),
        (check_suggest_merge, "建议合并"),
        (check_contradiction, "结论矛盾"),
        (check_stale_documents, "长期未更新"),
    ]:
        try:
            results = check_fn()
            for r in results:
                all_suggestions.append(f"[{label}] {r}")
        except Exception as e:
            print(f"[health-check] Self-growing check '{label}' failed: {e}", file=sys.stderr)
    return all_suggestions
