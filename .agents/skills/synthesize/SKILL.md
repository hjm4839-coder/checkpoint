---
name: synthesize
description: 跨会话知识合成——将同类断点聚合成知识文档。Triggered by /synthesize.
---

# /synthesize — Synthesize knowledge from sessions

从多条相关断点笔记中提炼知识文档。

## Triggers

- `/synthesize`

## Steps

### 1. 选范围

问用户要合成的范围：
- 按标签（如 `obsidian/配置`）
- 按项目
- 或由 Codex 自动找标签重叠 ≥3 的断点集群

### 2. 读相关断点

```bash
find ~/obsidian/知识库/Codex方案/会话断点 -type f -name '*.md'
grep -RIl --include='*.md' 'tags:.*<标签>' ~/obsidian/知识库/Codex方案/会话断点
```

Read 相关的 3-8 条断点笔记。

### 3. 合成知识文档

从多条断点中提炼共同主题、关键决策、最佳实践、踩坑记录，Write 到：

```
$OBSIDIAN_VAULT/Codex方案/<项目>/<主题>.md
```

格式：
```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [Codex/方案, <相关标签>]
---
# 标题
## 背景  ## 关键结论  ## 最佳实践  ## 踩坑记录  ## 相关会话
```

### 4. 报告

展示合成的文档路径、覆盖的会话数、提炼的要点数。

> Windows: `grep` 换成 `Select-String`，`$OBSIDIAN_VAULT` 换成 `$env:OBSIDIAN_VAULT`。
