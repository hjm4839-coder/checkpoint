---
name: checkpoint
description: Save current session checkpoint to Obsidian. Triggered by /checkpoint.
---

# /checkpoint — Save session checkpoint

Write current Codex session checkpoint to Obsidian vault.

## Triggers

- `/checkpoint`

## Steps

### 1. Find transcript

当前会话的 transcript 是全局最近修改的那个（任意项目目录），不要写死某个项目目录：

```bash
ls -t ~/.Codex/projects/*/*.jsonl | head -1
```

### 2. Get session ID

从 transcript 文件名取（去掉 `.jsonl`）：

```bash
SID=$(basename "$(ls -t ~/.Codex/projects/*/*.jsonl | head -1)" .jsonl)
```

### 3. Run

`--force` 让脚本重新综合主题/标签（忽略旧标题保留），手动触发时用它修正命名：

```bash
python3 ~/obsidian/.Codex/hooks/checkpoint.py --transcript <path> --session-id <id> --force
```

### 4. Report

Show session status, outputs, and any warnings (interrupted / incomplete_archive).
