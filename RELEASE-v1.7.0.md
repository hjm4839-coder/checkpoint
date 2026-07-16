## Breaking changes
- 移除 Lite/Full 双模式，`install.sh --lite` 不再支持
- 删除 `checkpoint-lite` skill

## 修复
- 后台项目知识更新（项目总结 + AI 开发参考）LLM 超时自动 fallback，不再阻塞父进程
- 后台进程日志写入 `Claude方案/运维/checkpoint-bg.log`，便于诊断
- non-force 重新运行时保留手工修改的 aliases/keywords
- `--projects` CLI 参数值自动与 transcript 解析结果合并
- PreToolUse Hook 注册 `pretool.py`（写入 `Claude方案/` 时自动提示已有文档）
- `_dataview-config.base` 自动生成，按状态/日期/项目分组导航
- `_read_frontmatter_all` 兼容传入 str 和 Path 两种参数
- 断裂 wikilink 修复、根目录散放文件归位

## 代码变更
- `checkpoint.py`: 77 行增删（移除 lite 模式分支 + 修复背景进程 + 参数解析增强）
- `checkpoint-lite/SKILL.md`: 已删除
- `verify/SKILL.md`: 清理 "lite" 引用
- `README.md`: v1.6.0 → v1.7.0
