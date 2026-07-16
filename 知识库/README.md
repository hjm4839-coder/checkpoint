# 会话断点与知识库工具集 (checkpoint)

当前版本：v1.6.0

Claude Code 会话结束时自动生成断点笔记，按月份写入 Obsidian 知识库。同时提供本地混合语义检索引擎，通过「关键词匹配 + 向量语义召回」快速定位已有方案，避免重复推导。

Stop Hook 在检测到本次会话写入 `Claude方案/<项目名>/` 后，在后台刷新项目级滚动总结，并把跨项目可迁移经验沉淀到同类设计主题文件。下一次接手同项目时先读 `项目总结.md`，同类项目先读 `AI开发参考/`，避免恢复完整长 transcript。

---

## 安装

```bash
git clone git@github.com:hjm4839-coder/checkpoint.git && cd checkpoint
./install.sh            # Full 模式（自动 Stop hook）
./install.sh --lite     # Lite 模式（仅手动 /checkpoint，零额外 API 调用）
```

### 依赖

```bash
# 混合语义检索需要
pip3 install sentence-transformers

# 运行测试需要
pip3 install pytest
```

---

## 使用指南

### 混合语义检索 (/search)

在 Claude Code 中输入 `/search <关键词>` 即可搜索知识库。系统自动完成关键词匹配和语义召回，结果按相关性排序。

**命令行使用：**

```bash
# 搜索知识库
python3 scripts/kb_search.py search "断点恢复" --top-k 5

# JSON 格式输出（方便程序调用）
python3 scripts/kb_search.py search "VPN 自动连接" --json --top-k 10

# 纯关键词搜索（不用语义模型）
python3 scripts/kb_search.py search "checkpoint" --no-semantic

# 增量索引（手动触发，平时不需要）
python3 scripts/kb_search.py index --incremental

# 重建全部索引（模型升级后需要）
python3 scripts/kb_search.py rebuild
```

**搜索结果示例：**

```
## 搜索结果：断点恢复

### 1. [[obsidian集成/checkpoint机制全貌]] — Checkpoint 会话断点机制全貌
   命中类型: aliases
   匹配词: 断点恢复
   章节: 核心能力

### 2. [[会话断点/2026-07/Claude Code会话总结自动化机制]]
   命中类型: semantic-only
   语义相似度: 0.89
   片段: 每次会话结束时自动生成断点笔记...
```

**搜索优先级：**别名 > 关键词 > 标签 > 正文 > 语义匹配。有明确关键词匹配的结果不会被语义结果挤掉。

---

### 会话断点 (/checkpoint)

每句对话结束后，Stop Hook 自动生成断点笔记保存到 `Claude方案/会话断点/YYYY-MM/`。也可以手动触发：

```
/checkpoint
```

**恢复未完成的工作：**

```bash
claude --resume <session_id>
```

session_id 在每条断点笔记的 frontmatter 中。

**断点状态：**

| 状态 | 含义 |
|:---:|------|
| ✅ 正常结束 | 会话顺利完成 |
| ⚠️ 会话中断 | 未完成，需要恢复 |
| 📋 方案未归档 | 讨论了方案但未写入知识库 |

---

### 知识合成 (/synthesize)

多条同类断点可以合成为一篇稳定的知识文档：

```
/synthesize
```

Claude 会询问合成范围（按标签、按项目、或自动聚类），然后从 3-8 条相关断点中提炼共同主题、关键决策和踩坑记录。

---

### 自动健康检查

每次会话结束时，Stop Hook 自动扫描知识库的 7 项健康指标：

- 空文件检测
- 断点 frontmatter 格式完整性
- 目录约定遵守情况
- Dataview 配置文件位置
- 项目目录是否有项目总结
- Wiki-link 是否有断裂
- 根目录是否清洁

有问题的生成报告，无问题静默退出。60 分钟内不重复扫描。

---

### 写入防重复提醒

当你向 `Claude方案/` 写入新文件时，系统自动提示同目录下已有文档，避免重复推导已有结论。

---

## 运行测试

```bash
cd checkpoint
python3 -m pytest tests/ -v
```

54 个单元测试覆盖：frontmatter 解析、文本切块、词法检索、语义搜索、混合排序、增量索引、格式化输出。

---

## 版本说明

### v1.6.0

混合语义检索版本。从纯 grep 关键词匹配升级为「关键词 + 向量语义」混合检索引擎。

**新增脚本：**
- `scripts/kb_search.py` — 本地混合检索引擎
  - 词法检索：aliases > keywords > tags > 标题/正文，保持与旧版一致的优先级
  - 语义检索：intfloat/multilingual-e5-small 本地嵌入模型（384 维向量）
  - SQLite + NumPy 存储，零外部依赖，完全本地运行
  - 搜索前自动 SHA-256 增量索引，新增/修改/删除文件自动更新
  - 模型不可用时自动降级为纯词法检索
- `tests/test_search_hybrid.py` — 54 个单元测试

**技术选型：**
- 嵌入模型：intfloat/multilingual-e5-small（中英文支持，CPU 可运行，476MB）
- 向量存储：SQLite + NumPy（百篇级知识库足够，零运维）
- 索引策略：搜索时增量索引（覆盖手动编辑、Git 拉取、文件删除）
- 降级策略：模型不可用时自动回退纯词法检索

**验收：**
- 搜索"恢复上次中断的会话"能召回 checkpoint 相关文档，语义分数 0.89
- 排序优先级 aliases > keywords > tags > body > semantic-only 不退化
- 正文命中排在纯语义命中之前，同一文档合并去重
- 索引和模型全部本地，不发送任何笔记内容到外部服务

### v1.5.0

Token 优化版本。全局 CLAUDE.md 瘦身 80%，低频规范改为知识库按需检索。

- 每次请求固定开销从 ~19K tokens 降至 ~12.9K tokens（-32%）
- 移除 dolphinmem MCP 和 pts MCP，工具定义开销归零

### v1.4.0

自动化与结构精分版本。新增知识库自动健康检查、新建项目自动分类规则。

- `health_check.py` Stop Hook：7 项自动扫描
- `pretool.py` PreToolUse Hook 增强：写入时自动提示已有文档

### v1.3.0

性能优化版本。项目总结和 AI 开发参考改为后台子进程执行，60 分钟内已更新过的跳过刷新。

### v1.2.0

知识库结构精简版本。checkpoint 统一按月归档，取消每日索引自动生成。

### v1.1.0

检索增强版本。为文档统一增加 aliases 和 keywords 元数据。

---

## 目录结构

```
├── README.md                    # 本文件
├── .gitignore
├── scripts/                     # 工具脚本
│   └── kb_search.py             # 混合语义检索引擎
├── tests/                       # 单元测试
│   └── test_search_hybrid.py    # 检索引擎测试 (54 个)
└── Claude方案/版本更新/          # 详细发布记录
    └── v1.6.0.md
```
