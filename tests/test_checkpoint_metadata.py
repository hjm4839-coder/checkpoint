import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "checkpoint.py"
spec = importlib.util.spec_from_file_location("checkpoint_metadata", MODULE_PATH)
checkpoint = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checkpoint
spec.loader.exec_module(checkpoint)


def _frontmatter_value(text, key):
    return checkpoint._parse_frontmatter_list(text, key)


def test_parse_frontmatter_list_supports_inline_delimited_and_yaml_block():
    text = """---
aliases: ["Netplan", "网卡切换"]
keywords: Docker，Nginx, MySQL
tags:
  - 运维/网络
  - shell/Netplan
---
"""

    assert checkpoint._parse_frontmatter_list(text, "aliases") == ["Netplan", "网卡切换"]
    assert checkpoint._parse_frontmatter_list(text, "keywords") == ["Docker", "Nginx", "MySQL"]
    assert checkpoint._parse_frontmatter_list(text, "tags") == ["运维/网络", "shell/Netplan"]
    assert checkpoint._parse_frontmatter_list(text, "missing") == []


def test_metadata_values_deduplicate_filter_noise_and_apply_limit():
    values = checkpoint._metadata_values(
        ["Netplan", "netplan", " / ", "ouyangkai", "worktrees"],
        ["网卡切换", "Docker", "Nginx"],
        limit=4,
    )

    assert values == ["Netplan", "网卡切换", "Docker", "Nginx"]


def test_fallback_tags_skip_system_and_worktree_path_parts():
    tags, keywords = checkpoint._fallback_tags_from_files(
        [
            "/Users/ouyangkai/obsidian/.claude/worktrees/twinkling-wiggling-creek/项目甲/src/netplan-switch.py",
        ]
    )

    combined = {value.casefold() for value in tags + keywords}
    assert not {"/", "users", "ouyangkai", "obsidian", "worktrees", "twinkling-wiggling-creek", "src"} & combined
    assert "项目甲" in tags
    assert "netplan-switch" in combined


def test_checkpoint_keywords_fall_back_to_tag_leaf_names():
    assert checkpoint.build_checkpoint_keywords([], ["运维/网络", "shell/Netplan", "Netplan"]) == ["网络", "Netplan"]


def test_generate_session_note_writes_stable_aliases_and_keywords():
    ctx = {
        "topic": "Ubuntu 网卡切换",
        "category": ["运维管理"],
        "tags": ["运维/网络", "shell/Netplan"],
        "keywords": ["Netplan", "netplan", "/"],
        "aliases": ["网卡切换", "NETPLAN"],
        "projects": set(),
        "user_prompts": ["配置 Ubuntu 网卡切换"],
        "written_files": set(),
        "used_plan_mode": False,
        "verbal_plan_snippets": [],
    }

    note = checkpoint.generate_session_note("metadata-session", ctx, "completed")

    assert _frontmatter_value(note, "keywords") == ["Netplan"]
    assert _frontmatter_value(note, "aliases") == [
        "Ubuntu 网卡切换",
        "网卡切换",
        "NETPLAN",
        "运维/网络",
        "shell/Netplan",
    ]


def test_project_frontmatter_contains_clean_metadata():
    frontmatter = checkpoint._frontmatter(
        ["项目总结"],
        "测试项目",
        "项目总结",
        keywords=["checkpoint", "Checkpoint", "/"],
        aliases=["会话恢复", "checkpoint"],
    )

    assert _frontmatter_value(frontmatter, "keywords") == ["checkpoint", "项目总结"]
    assert _frontmatter_value(frontmatter, "aliases") == [
        "测试项目",
        "项目总结",
        "会话恢复",
        "checkpoint",
        "claude/方案",
        "知识库/自动总结",
    ]


def test_update_project_knowledge_preserves_existing_frontmatter_metadata(tmp_path, monkeypatch):
    plans = tmp_path / "Claude方案"
    project_dir = plans / "测试项目"
    experience_dir = plans / "AI开发参考"
    project_dir.mkdir(parents=True)
    experience_dir.mkdir(parents=True)
    summary_path = project_dir / checkpoint.PROJECT_SUMMARY_NAME
    reference_path = experience_dir / "通用项目经验.md"
    summary_path.write_text(
        '---\nkeywords: ["手工总结词"]\naliases: ["手工总结别名"]\n---\n# 旧总结\n',
        encoding="utf-8",
    )
    reference_path.write_text(
        '---\nkeywords: ["手工参考词"]\naliases: ["手工参考别名"]\n---\n# 旧参考\n',
        encoding="utf-8",
    )
    session_note = tmp_path / "session.md"
    session_note.write_text("# Session\n", encoding="utf-8")

    monkeypatch.setattr(checkpoint, "PLANS_DIR", plans)
    monkeypatch.setattr(checkpoint, "PLANS_DIR_STR", str(plans))
    monkeypatch.setattr(checkpoint, "EXPERIENCE_DIR", experience_dir)
    monkeypatch.setattr(checkpoint, "synthesize_project_summary", lambda *_: "# 新总结")
    monkeypatch.setattr(checkpoint, "synthesize_reusable_experience", lambda *_: "# 新参考")
    monkeypatch.setattr(checkpoint, "classify_experience_theme", lambda *_: "通用项目经验")

    written = checkpoint.update_project_knowledge(
        {
            "projects": {"测试项目"},
            "keywords": ["Netplan"],
            "aliases": ["网卡切换"],
            "status": "completed",
        },
        session_note,
    )

    assert written == [summary_path, reference_path]
    summary = summary_path.read_text(encoding="utf-8")
    reference = reference_path.read_text(encoding="utf-8")
    assert _frontmatter_value(summary, "keywords")[:2] == ["手工总结词", "Netplan"]
    assert "手工总结别名" in _frontmatter_value(summary, "aliases")
    assert _frontmatter_value(reference, "keywords")[:2] == ["手工参考词", "Netplan"]
    assert "手工参考别名" in _frontmatter_value(reference, "aliases")


def test_existing_checkpoint_refresh_preserves_aliases_and_fills_missing_keywords(tmp_path, monkeypatch):
    vault = tmp_path / "知识库"
    vault.mkdir()
    plans = vault / "Claude方案"
    index_dir = plans / "会话索引"
    note_dir = plans / "会话断点"
    experience_dir = plans / "AI开发参考"
    transcript = tmp_path / "metadata-session.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "优化 Netplan 网卡切换"}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(checkpoint, "VAULT_ROOT", vault)
    monkeypatch.setattr(checkpoint, "PLANS_DIR", plans)
    monkeypatch.setattr(checkpoint, "PLANS_DIR_STR", str(plans))
    monkeypatch.setattr(checkpoint, "INDEX_DIR", index_dir)
    monkeypatch.setattr(checkpoint, "NOTE_DIR", note_dir)
    monkeypatch.setattr(checkpoint, "EXPERIENCE_DIR", experience_dir)
    monkeypatch.setattr(checkpoint, "update_dashboard", lambda: None)
    monkeypatch.setattr(
        checkpoint,
        "_parse_cli",
        lambda: {
            "transcript": str(transcript),
            "session": "metadata-session",
            "cwd": str(tmp_path),
            "force": False,
            "lite": False,
            "lite_topic": None,
            "lite_category": [],
            "lite_tags": [],
            "lite_keywords": [],
        },
    )

    synthesized = {
        "topic": "Netplan 网卡切换",
        "category": ["运维管理"],
        "tags": ["运维/网络"],
        "keywords": ["Netplan"],
    }
    monkeypatch.setattr(checkpoint, "synthesize_topic_and_tags", lambda *_: synthesized)

    with pytest.raises(SystemExit) as first_exit:
        checkpoint.main()
    assert first_exit.value.code == 0

    notes = list(note_dir.rglob("*.md"))
    assert len(notes) == 1
    note_path = notes[0]
    first_text = note_path.read_text(encoding="utf-8")
    first_text = checkpoint.re.sub(r"^keywords:.*$", "keywords: []", first_text, flags=checkpoint.re.MULTILINE)
    first_text = checkpoint.re.sub(
        r"^aliases:.*$",
        'aliases: ["手工网卡别名"]',
        first_text,
        flags=checkpoint.re.MULTILINE,
    )
    note_path.write_text(first_text, encoding="utf-8")

    synthesized["topic"] = "不应覆盖旧标题"
    synthesized["category"] = ["其他分类"]
    synthesized["tags"] = ["其他标签"]
    synthesized["keywords"] = ["networkctl"]

    with pytest.raises(SystemExit) as second_exit:
        checkpoint.main()
    assert second_exit.value.code == 0

    refreshed = note_path.read_text(encoding="utf-8")
    assert _frontmatter_value(refreshed, "category") == ["运维管理"]
    assert _frontmatter_value(refreshed, "tags") == ["运维/网络"]
    assert _frontmatter_value(refreshed, "keywords") == ["networkctl"]
    assert _frontmatter_value(refreshed, "aliases") == [
        "Netplan 网卡切换",
        "手工网卡别名",
        "networkctl",
        "运维/网络",
    ]
    assert len(list(note_dir.rglob("*.md"))) == 1
