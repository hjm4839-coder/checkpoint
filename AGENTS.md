# Obsidian 知识库

## 启动时

读 `$OBSIDIAN_VAULT/Codex方案/会话索引/` 下今天和昨天的 `YYYY-MM-DD.md`，展示最近会话表格。`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`。

有 `interrupted` 或 `incomplete_archive` 的会话 → 提醒恢复，命令：`Codex --resume <id>`。

## 方案归档

方案敲定后**直接 Write** 到 `$OBSIDIAN_VAULT/Codex方案/<项目名>/<方案标题>.md`（`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`）。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [Codex/方案, ...]
---
# 标题
## 背景  ## 方案  ## 关键决策  ## 实施步骤  ## 相关笔记
```

规则：date 用 ISO 格式 · tags 含 `Codex/方案` · 关联笔记用 `[[wikilink]]` · 修改用 Edit。

## 会话断点 (checkpoint)

- **自动**：会话结束 Stop Hook 自动生成断点笔记到 `Codex方案/会话断点/YYYY-MM/DD/<主题>.md`（主题由 LLM 综合，session_id 存 frontmatter），并更新 `Codex方案/会话索引/YYYY-MM-DD.md` 每日索引
- **项目总结**：若本次会话写入了 `Codex方案/<项目名>/` 下的方案/记录，Stop Hook 会同步刷新 `Codex方案/<项目名>/项目总结.md`，作为下次新会话接手项目的首读摘要
- **AI开发参考**：Stop Hook 会把跨项目可迁移经验写入 `Codex方案/AI开发参考/<同类设计主题>.md`。同类设计归类到一个文件，不再按项目拆成 `<项目名>-AI开发参考.md`；内容必须沉淀关键技术节点、创作思路、实施思路、踩坑点，可补充验收清单、下次执行顺序、关键词和相关笔记。后续同类项目优先读对应主题参考文件，不恢复长 transcript。
- **手动**：`/checkpoint`
- **标签**：`tags` = 动态内容标签，LLM 按对话内容和实际产出自由生成 2-5 个（支持层级如 `前端/Vue`、`obsidian/配置`）；`keywords` = 1-3 个补充精确搜索词
- **视图**：`Codex方案/会话断点.base`（不与笔记同目录）可按标签/状态/项目/关键词筛选分组

三种状态：✅ completed · ⚠️ interrupted · 📋 incomplete_archive（讨论了方案但没写）

## 新会话恢复原则

1. 同项目继续工作时，先读 `Codex方案/<项目名>/项目总结.md`；平台类项目统一先读 `Codex方案/网站平台汇总/README.md` 和对应的主题级 `Codex方案/AI开发参考/` 文件。
2. 同类项目复用参考时，先读 `Codex方案/AI开发参考/<同类设计主题>.md`，例如平台技术部署、平台 UI/交互、知识库/自动总结、部署/运维、报告生成等主题参考。
3. 检索知识库时用递归搜索覆盖子文件夹，例如 `grep -RIl --include='*.md' "关键词" "$OBSIDIAN_VAULT/Codex方案"`。
4. 只补读 1-2 篇最相关方案或修复记录，避免恢复完整长会话导致 `cache_read_input_tokens` 膨胀。
5. 新项目、新服务器、新报告阶段优先开新会话，用主题参考和案例索引承接上下文。
