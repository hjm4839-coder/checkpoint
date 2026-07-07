# Obsidian 知识库

## 启动时

读 `$OBSIDIAN_VAULT/Claude方案/会话索引/` 下今天和昨天的 `YYYY-MM-DD.md`，展示最近会话表格。`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`。

有 `interrupted` 或 `incomplete_archive` 的会话 → 提醒恢复，命令：`claude --resume <id>`。

## 方案归档

方案敲定后**直接 Write** 到 `$OBSIDIAN_VAULT/Claude方案/<项目名>/<方案标题>.md`（`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`）。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [claude/方案, ...]
---
# 标题
## 背景  ## 方案  ## 关键决策  ## 实施步骤  ## 相关笔记
```

规则：date 用 ISO 格式 · tags 含 `claude/方案` · 关联笔记用 `[[wikilink]]` · 修改用 Edit。

## 会话断点 (checkpoint)

- **自动**：会话结束 Stop Hook 自动生成断点笔记到 `Claude方案/会话断点/<主题>.md`（主题由 LLM 综合，session_id 存 frontmatter），并更新 `Claude方案/会话索引/YYYY-MM-DD.md` 每日索引
- **手动**：`/checkpoint`
- **标签**：`tags` = 动态内容标签，LLM 按对话内容和实际产出自由生成 2-5 个（支持层级如 `前端/Vue`、`obsidian/配置`）；`keywords` = 1-3 个补充精确搜索词
- **视图**：`Claude方案/会话断点.base`（不与笔记同目录）可按标签/状态/项目/关键词筛选分组

三种状态：✅ completed · ⚠️ interrupted · 📋 incomplete_archive（讨论了方案但没写）
