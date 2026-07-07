# 会话断点 (checkpoint)

Claude Code 会话结束时自动把对话沉淀成 Obsidian 断点笔记——主题命名、大小类标签、状态 triage、可恢复。无需 Obsidian 也能用。

> **仅支持 Claude Code**（依赖 hooks 机制），暂不兼容其他 AI 编程工具。

## 安装

```bash
git clone <仓库地址> && cd <目录>
./install.sh            # Full 模式（自动 Stop hook，推荐）
./install.sh --lite     # Lite 模式（仅手动 /checkpoint，零额外 API）
```

Windows：`powershell -ExecutionPolicy Bypass -File .\install.ps1 [--lite]`

## 两种模式

| | Full（推荐） | Lite |
|---|---|---|
| 自动 Stop hook | ✅ | ❌ |
| 手动 /checkpoint | ✅ 脚本调 LLM | ✅ 对话模型起标题 |
| 额外 API 调用 | 每次 1 次（~50 token） | 0 |

## 功能

- **自动断点**：任意目录启动，会话结束自动生成
- **智能命名**：LLM 综合对话内容 + 实际产出起名，思考型模型自动兜底
- **大小类标签**：`category`（大类）+ `tags`（小类）+ `keywords`（搜索词），一次 LLM 产出
- **状态 triage**：✅ 完成 · ⚠️ 中断 · 📋 方案未归档
- **知识库首页**：`_知识库首页.md` 自动刷新，概览 + 标签云 + 待恢复列表
- **会话关联**：同标签笔记自动 wikilink 互连
- **PreToolUse 提醒**：写文件前提醒已有相关文档
- **知识合成**：`/synthesize` 同类断点→知识文档
- **Provider 无关**：兼容 Anthropic / OpenAI / 网关 / 思考型模型

## 前置

- Claude Code + python3（仅标准库）
- Obsidian 可选——指向任意文件夹也能用，只是没 Bases 视图/wikilink

## 目录结构

```
vault/
├── _知识库首页.md
└── Claude方案/
    ├── 会话索引/     # 每日索引 YYYY-MM-DD.md
    ├── 会话断点/     # 断点笔记 <主题>.md
    ├── <项目名>/     # 归档方案
    └── 会话断点.base # Bases 视图
```

## 卸载

```bash
./uninstall.sh  # 清理 hooks+skills+env，保留笔记文件
```

Windows：`powershell -ExecutionPolicy Bypass -File .\uninstall.ps1`
