# 知识库混合语义检索引擎

基于本地嵌入模型的 Obsidian 知识库混合检索工具。

## 目录

```
├── scripts/                  # 工具脚本
│   └── kb_search.py          # 混合检索引擎
├── tests/                    # 单元测试
│   └── test_search_hybrid.py
└── Claude方案/版本更新/       # 发布记录
    └── v1.6.0.md
```

## kb_search.py

本地「关键词匹配 + 向量语义召回」混合检索引擎。

**特点：**

- 完全本地运行，笔记内容不离开本机
- 基于 intfloat/multilingual-e5-small 嵌入模型
- SQLite + NumPy 存储，零外部服务依赖
- 搜索前自动增量索引，新笔记即时可搜
- 模型不可用时自动降级为纯关键词搜索

**安装依赖：**

```bash
pip3 install sentence-transformers
```

**用法：**

```bash
# 搜索
python3 scripts/kb_search.py search "断点恢复" --top-k 5

# 增量索引
python3 scripts/kb_search.py index --incremental

# 重建索引
python3 scripts/kb_search.py rebuild

# 运行测试
python3 -m pytest tests/ -q
```

## 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.6.0 | 2026-07-15 | 混合语义检索引擎 |
