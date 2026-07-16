# 会话断点与知识库工具集 (checkpoint)

当前版本：v1.7.1

Claude Code 会话结束时自动生成断点笔记，按月份写入 Obsidian 知识库。同时提供本地混合语义检索引擎，通过「关键词匹配 + 向量语义召回」快速定位已有方案，避免重复推导。

Stop Hook 在检测到本次会话写入 `Claude方案/<项目名>/` 后，在后台刷新项目级滚动总结，并把跨项目可迁移经验沉淀到同类设计主题文件。下一次接手同项目时先读 `项目总结.md`，同类项目先读 `AI开发参考/`，避免恢复完整长 transcript。

## 版本说明

### v1.7.1

本地混合语义检索引擎。新增 `kb_search.py`，将纯 grep 搜索升级为「关键词 + 向量语义」双路检索。

**新增:**
- `scripts/kb_search.py` — 本地混合检索引擎
  - 词法检索：aliases > keywords > tags > 正文，保持与 `/search` 一致
  - 语义检索：`intfloat/multilingual-e5-small` (384 dim)，numpy 批量点积
  - SQLite 存储索引元数据，NumPy BLOB 存储向量，零外部依赖
  - 模型离线加载（`HF_HUB_OFFLINE=1 + local_files_only=True`），不联网
  - 模型不可用时自动降级到 grep

**命令:**
```
python3 kb_search.py index          # 增量索引
python3 kb_search.py search "查询"   # 混合搜索
python3 kb_search.py rebuild        # 重建全部索引
```

### v1.7.0

健壮性与简化版本。移除 Lite 模式、修复背景进程阻塞、补齐元数据保留逻辑。

**Breaking:**
- 移除 Lite/Full 双模式，`install.sh --lite` 不再支持
- 移除 `checkpoint-lite` skill

**修复:**
- 后台项目知识更新进程 LLM 超时→fallback，不阻塞父进程
- 后台进程日志写入 `Claude方案/运维/checkpoint-bg.log`
- non-force 重新运行时保留手工 aliases/keywords
- `--projects` CLI 参数值自动合并到 transcript 解析结果
- PreToolUse Hook 注册 `pretool.py`
- `_dataview-config.base` 自动生成（按状态/日期/项目分组）
- `_read_frontmatter_all` 兼容 str 和 Path 参数
- 断裂 wikilink 修复、散放文件归位
- 所有文档清理 lite 模式引用

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

**CLAUDE.md 瘦身：**
- `~/.claude/CLAUDE.md` 从 219 行大幅精简为 42 行（9543→1893 字节，-80%）
- 低频规范移入 `Claude方案/ClaudeCode操作规范.md` 按需 grep 加载
- 新增「按需检索的规范」章节：触发新建/写入时自动 grep 加载完整规范

**Token 优化成果：**
- 每次请求固定开销从 ~19K tokens 降至 ~12.9K tokens（-32%）
- 单 CLAUDE.md 从 ~3000→~600 tokens（省 2400 tokens）
- 移除 dolphinmem MCP 和 pts MCP，工具定义开销归零

### v1.4.0

自动化与结构精分版本。新增知识库自动健康检查、新建项目自动分类规则。

**新增组件：**
- `health_check.py` Stop Hook：7 项自动扫描，有问题写报告无问题静默退出
- `pretool.py` PreToolUse Hook 增强：写入时自动提示已有文档
- CLAUDE.md 新增知识库文件结构规范和自动分类规则

### v1.3.0

性能优化版本。项目总结和 AI 开发参考改为后台子进程执行，60 分钟内已更新过的跳过刷新。

### v1.2.0

知识库结构精简版本。checkpoint 统一按月归档，取消每日索引自动生成。

### v1.1.0

检索增强版本。为文档统一增加 aliases 和 keywords 元数据。

## 目录结构

```
├── README.md                    # 本文件
├── .gitignore                   # 排除个人笔记内容
├── scripts/                     # 工具脚本
│   └── kb_search.py             # 混合语义检索引擎
├── tests/                       # 单元测试
│   └── test_search_hybrid.py    # 检索引擎测试 (54 个)
└── Claude方案/版本更新/          # 详细发布记录
    └── v1.6.0.md
```
