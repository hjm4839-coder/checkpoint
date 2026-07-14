#!/usr/bin/env python3
"""
Claude Code Stop Hook: 知识库结构健康检查。
每次会话结束时静默扫描，仅当发现问题才写入报告。
"""

import json
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
THROTTLE_MINUTES = 120  # 上次报告 < 120 分钟则跳过

# 节流锁文件：健康检查每次会话只运行一次
LOCK_FILE = Path.home() / ".claude" / ".health_check_lock"

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
    # 先将 \\| 还原为 |
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
        if f.name.startswith("_"):
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
    expected = NOTE_DIR / "_dataview-config.base"
    old = PLANS_DIR / "会话断点.base"
    if old.exists():
        issues.append(f"`会话断点.base` 还在旧位置，应移到 `会话断点/_dataview-config.base`")
    if not expected.exists():
        issues.append(f"缺少 `会话断点/_dataview-config.base`")
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

CHECKS = [
    ("空文件", check_empty_files),
    ("断点 frontmatter", check_checkpoint_frontmatter),
    ("目录约定", check_directory_conventions),
    ("Dataview 配置位置", check_dataview_config_position),
    ("项目目录完整性", check_project_directory_completeness),
    ("Wiki-link 完整性", check_wikilinks),
    ("Claude方案 根目录清洁度", check_Claude方案_root_cleanliness),
]


def run_all_checks() -> list[str]:
    all_issues = []
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


def _acquire_lock() -> bool:
    """同一批次只允许一次健康检查。获取锁返回 True，已存在锁返回 False。"""
    try:
        if LOCK_FILE.exists():
            # 检查锁是否过期（超过 5 分钟自动释放）
            age = time.time() - os.path.getmtime(str(LOCK_FILE))
            if age < 1800:
                return False
            # 过期，删除重建
            LOCK_FILE.unlink()
        LOCK_FILE.write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def _release_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def _should_skip() -> bool:
    """上次报告 < THROTTLE_MINUTES 分钟且无新问题则跳过。"""
    try:
        if REPORT_PATH.exists():
            mtime = os.path.getmtime(str(REPORT_PATH))
            age = time.time() - mtime
            return age < THROTTLE_MINUTES * 60
    except OSError:
        pass
    return False


def main():
    # 同一批次只运行一次
    if not _acquire_lock():
        return

    try:
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
- frontmatter 格式 → 参考 `会话断点/_dataview-config.base` 同目录文件格式
- 断裂链接 → 更新 [[target]] 为实际文件名
- 文件位置错误 → 移动到对应子目录
"""
        REPORT_PATH.write_text(content, encoding="utf-8")
        print(f"[health-check] 发现 {len(issues)} 项问题，报告已写入 {REPORT_PATH}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
