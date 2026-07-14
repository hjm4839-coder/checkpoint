# 全局指令

## 回答前必须检索知识库

在回答任何技术、方案、配置、运维、开发类问题之前，**必须先搜索知识库**，不要默认从零回答：

1. 从用户问题中提取 1-3 个关键词
2. `grep -RIl --include='*.md' "<关键词>" "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"` 找匹配文档
3. 同时 grep 标签：`grep -RIl --include='*.md' 'tags:.*<关键词>' "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"`
4. Read 最相关的 1-2 篇，已有结论**直接引用原文**，只补充新内容
5. 读了 2 篇没找到 → 告诉用户"知识库暂无相关记录"，然后正常回答
6. 纯闲聊/简单问答可跳过检索

知识库位置：`$OBSIDIAN_VAULT/Claude方案/`（默认 `~/obsidian/知识库/Claude方案/`）

## 方案归档

方案敲定后直接 Write 到 `$OBSIDIAN_VAULT/Claude方案/<项目名>/<方案标题>.md`。`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`，可通过环境变量覆盖。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [claude/方案, <分类标签>, <关键词>]
---
# 标题
## 背景  ## 方案  ## 关键决策  ## 实施步骤  ## 相关笔记
```

`tags` 里除了 `claude/方案` 还要加上本次会话的分类标签和关键词，方便后续按标签检索。

## 按需检索的规范

以下规范**不在每次会话预加载**，触发对应场景时先 `grep` 知识库再执行：

| 场景 | 检索方式 |
|------|----------|
| 新建项目 / 项目分类 / 创建骨架 / 合并复用判断 | `grep -RIl --include='*.md' 'ClaudeCode操作规范' "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"` |
| 写入断点 / frontmatter 格式 / 文件存放目录 | 同上 |
| 项目总结格式 / AI 开发参考格式 | 同上 |

会话结束时的项目总结和 AI 开发参考由 Stop Hook 自动处理，无需手动干预。
