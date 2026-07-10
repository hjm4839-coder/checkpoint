---
name: search
description: 搜索知识库——按关键词查找 Claude方案/ 下的文档和断点笔记。Triggered by /search.
---

# /search — 搜索知识库

按关键词快速检索 `Claude方案/` 下的归档文档和断点笔记。

## Triggers

- `/search <关键词>` 或 `/search`

### 1. 提取关键词

从用户输入中提取 1-3 个关键词。如果用户没说搜索词，问他要搜什么。

### 2. 搜索归档文档

```bash
grep -Ril --include='*.md' "<关键词1>\|<关键词2>\|<关键词3>" $OBSIDIAN_VAULT/Claude方案
```

### 3. 搜索断点笔记的标签

```bash
grep -RIl --include='*.md' 'tags:.*<关键词1>\|tags:.*<关键词2>' $OBSIDIAN_VAULT/Claude方案/会话断点
```

### 4. Read 匹配结果的 H1 标题

对每个匹配文件，用 Read 只读前 5 行拿到 H1 标题和摘要。

### 5. 输出

列出匹配结果：

```
## 搜索结果：<关键词>

**归档文档**
- [[doc1]] — 一句话说明
- [[doc2]] — 一句话说明

**断点笔记**
- [[note1]] — 日期 · 状态emoji
```

没找到就诚实说找不到。不要编造结果。

> Windows: `grep` 换成 `Select-String`，`$OBSIDIAN_VAULT` 换成 `$env:OBSIDIAN_VAULT`。
