# 会话断点 (checkpoint)

Claude Code 会话结束时自动生成断点笔记，写入 Obsidian 知识库——主题命名、大小类标签、状态 triage、可恢复。断点笔记 + 每日索引 + 首页仪表盘 + Bases 视图，让你随时找到"上次那件事聊到哪了"。

> **仅支持 Claude Code**（依赖 hooks 机制），暂不兼容其他 AI 编程工具。

## 安装

**前置**：Claude Code 已装好 + python3 可用（仅标准库，无需 pip）。Obsidian 可选——不装也能用，只是没 Bases 视图和 wikilink 跳转。

```bash
git clone <仓库地址> && cd <目录>
./install.sh            # Full 模式（自动 Stop hook，推荐）

# 或不想额外调 API：
./install.sh --lite     # Lite 模式（仅手动 /checkpoint，零额外 API）
```

Windows：`powershell -ExecutionPolicy Bypass -File .\install.ps1 [--lite]`

安装脚本会自动：注册 Stop/PreToolUse hook → 配置 vault 路径 → 装 `/checkpoint` + `/synthesize` + `/search` skill → 创建全局 CLAUDE.md。幂等，重复运行不重复注册，不动已有配置。

### Full vs Lite

| | Full（推荐） | Lite |
|---|---|---|
| 自动 Stop hook | ✅ 会话结束自动生成断点 | ❌ |
| `/checkpoint` 手动 | ✅ 脚本调 LLM 起标题/标签 | ✅ 对话模型起标题（零额外 API） |
| 额外 API 调用 | 每次 Stop hook ~50 token | 0 |
| 适合谁 | 有 API 凭证、要全自动 | 不想额外调 API |

## 日常怎么用

1. **正常聊天**——什么也不用做，结束自动生成断点
2. **想回顾**——打开 `_知识库首页.md` 看总览，或翻 `Claude方案/会话索引/` 每日索引
3. **没干完的事**——笔记里有 `claude --resume <id>`，拿 id 恢复接着干
4. **方案敲定了**——让 Claude 把方案 Write 到 `Claude方案/<项目>/`，断点自动变 ✅
5. **知识积累多了**——跑 `/synthesize` 把同类断点合成知识文档

## 功能

- **自动断点**：任意目录启动 claude，会话结束自动生成笔记
- **智能命名 + 大小类标签**：LLM 一次调用产出主题 + `category`（大类，如 技术开发/运维管理）+ `tags`（小类，如 shell/Netplan）+ `keywords`（搜索词）。思考型模型和非思考型模型都兼容
- **状态 triage**：✅ 完成 · ⚠️ 中断 · 📋 方案未归档。纯问答不误标 ⚠️
- **知识库首页**：`_知识库首页.md` 自动刷新，概览 + 标签云 + 待恢复列表 + 完成率
- **PreToolUse 提醒**：往 `Claude方案/` 写文件时自动提醒已有相关文档
- **知识合成 + 搜索**：`/synthesize` 按标签合并同类断点→知识文档，`/search` 按关键词搜全库。文档含 `aliases` 别名提高命中率
- **跨平台 + Provider 无关**：macOS/Linux/Windows，兼容 Anthropic/OpenAI/网关

## 命令（任意目录可用）

| 命令 | 作用 |
|---|---|
| `/checkpoint` | 手动生成/刷新当前会话断点 |
| `/search <关键词>` | 搜索知识库（归档文档 + 断点标签） |
| `/synthesize` | 按标签合并同类断点→知识文档 |

## 目录结构

```
vault/
├── _知识库首页.md       # 仪表盘
└── Claude方案/
    ├── 会话索引/         # 每日索引 YYYY-MM-DD.md
    ├── 会话断点/         # 断点笔记 <主题>.md
    ├── <项目名>/         # 归档方案
    └── 会话断点.base     # Bases 视图（筛选/分组）
```


## 迁移到新电脑

```bash
# 旧电脑：打包知识库 + 会话 transcript
./pack.sh    # 生成 checkpoint-migrate-*.tar.gz

# 传到新电脑，然后：
./unpack.sh checkpoint-migrate-*.tar.gz
git clone https://github.com/hjm4839-coder/checkpoint.git ~/obsidian
cd ~/obsidian && ./install.sh
```
## 卸载

```bash
./uninstall.sh    # 清理 hooks + skills + env，保留笔记文件
```

Windows：`powershell -ExecutionPolicy Bypass -File .\uninstall.ps1`
