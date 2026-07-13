---
name: search
description: 搜索知识库——按关键词查找 Claude方案/ 下的文档和断点笔记。Triggered by /search.
---

# /search — 搜索知识库

按关键词快速检索 `Claude方案/` 下的归档文档和断点笔记，优先使用 frontmatter 元数据，正文作为兜底。

## Triggers

- `/search <关键词>` 或 `/search`

### 1. 提取关键词

从用户输入中提取 1-3 个关键词。如果用户没说搜索词，问他要搜什么。传给 shell 前要安全引用关键词，不直接拼接未转义的用户输入。

### 2. 搜索 frontmatter 元数据

递归搜索全部 Markdown，不只搜索断点。分别记录 `aliases`、`keywords` 和 `tags` 命中，以便后续排序：

```bash
grep -RIl --include='*.md' -E '^aliases:.*(<关键词1>|<关键词2>|<关键词3>)' "$OBSIDIAN_VAULT/Claude方案"
grep -RIl --include='*.md' -E '^keywords:.*(<关键词1>|<关键词2>|<关键词3>)' "$OBSIDIAN_VAULT/Claude方案"
grep -RIl --include='*.md' -E '^tags:.*(<关键词1>|<关键词2>|<关键词3>)' "$OBSIDIAN_VAULT/Claude方案"
```

### 3. 全文递归搜索

正文搜索是旧文档和无元数据文档的兜底，始终保留：

```bash
grep -RIl --include='*.md' -E '(<关键词1>|<关键词2>|<关键词3>)' "$OBSIDIAN_VAULT/Claude方案"
```

### 4. 合并、去重和排序

每个文件只保留一次，按最高优先级命中排序：

1. `aliases` 命中
2. `keywords` 命中
3. `tags` 命中
4. 标题或正文命中

同一优先级内，命中关键词更多的文件在前；仍相同时优先项目总结和 AI 开发参考，再按路径稳定排序。

### 5. Read 最相关结果

只对排名最前的 1-2 个文件使用 Read，读取 frontmatter、H1 和足够判断内容的摘要段。不要逐篇读取全部匹配文件；其余结果只根据路径和命中字段列出。

### 6. 输出

```text
## 搜索结果：<关键词>

1. [[doc1]] — aliases 命中：<词> · 一句话说明
2. [[doc2]] — keywords 命中：<词> · 一句话说明
3. [[note1]] — tags 命中：<词> · 未读取正文
```

最多列出 5 个结果，并明确说明已实际读取哪 1-2 篇。没找到就诚实说找不到，不要编造结果。

> Windows: `grep` 换成 `Get-ChildItem -Recurse -Filter *.md | Select-String`，`$OBSIDIAN_VAULT` 换成 `$env:OBSIDIAN_VAULT`。
