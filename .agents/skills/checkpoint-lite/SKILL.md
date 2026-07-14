---
name: checkpoint-lite
description: Save current session checkpoint to Obsidian without an extra API call. Triggered by /checkpoint-lite.
---

# /checkpoint-lite — Save session checkpoint (Lite)

用**当前对话模型**生成主题/标签/关键词，不额外调 API。

## Triggers

- `/checkpoint-lite`

## Steps

### 1. 综合元数据

基于当前对话内容，生成：

- **主题**：≤20 汉字，概括本次会话做了什么
- **分类标签**：2-5 个动态标签，覆盖宽泛分类和具体技术/领域，可用 / 表示层级（如 obsidian/配置、运维/网络、前端/Vue），按实际内容自由归类
- **内容关键词**：1-3 个，用于精确搜索

输出为：`主题: xxx` / `标签: a,b` / `关键词: x,y`

### 2. 找当前会话 transcript

```bash
ls -t ~/.Codex/projects/*/*.jsonl | head -1
```
session_id 取文件名去 `.jsonl`。

### 3. 跑脚本（传入元数据，不调 LLM）

```bash
python3 ~/obsidian/.Codex/hooks/checkpoint.py \
  --transcript <path> \
  --session-id <id> \
  --topic "主题" \
  --tags "标签1,标签2" \
  --keywords "关键词1,关键词2,关键词3"
```

### 4. 报告

展示：状态、话题、标签、关键词、产出。有 ⚠️/📋 提醒恢复。
