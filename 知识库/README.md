# Claude Code 配置与知识库工具

个人 Claude Code 配置仓库，包含自定义技能、Hook 脚本和知识库检索引擎。

## 目录结构

```
├── scripts/                  # 工具脚本
│   └── kb_search.py          # 知识库混合检索引擎
├── tests/                    # 单元测试
│   └── test_search_hybrid.py # 检索引擎测试
└── Claude方案/版本更新/       # 发布记录
    ├── v1.6.0.md
    └── v1.6.1.md
```

## 核心工具

### kb_search.py — 知识库混合检索引擎

本地「关键词匹配 + 向量语义召回」搜索 Obsidian 知识库。

**特点：**

- 100% 本地运行，不发送任何笔记到外部
- 基于 intfloat/multilingual-e5-small 嵌入模型
- SQLite + NumPy 存储，零外部依赖
- 搜索前自动 SHA-256 增量索引
- 模型不可用时自动降级为纯关键词搜索

**依赖：**

```bash
pip3 install sentence-transformers
```

**用法：**

```bash
# 搜索
python3 kb_search.py search "断点恢复" --top-k 5

# 增量索引
python3 kb_search.py index --incremental

# 重建全部索引
python3 kb_search.py rebuild
```

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.6.1 | 2026-07-16 | 知识库结构优化 |
| v1.6.0 | 2026-07-15 | 混合语义检索引擎 |
| v1.5.0 | 2026-07-14 | Token 优化与按需检索 |
| v1.4.0 | 2026-07-14 | 自动化与结构精分 |
