# 全局指令

## 回答前必须检索知识库

在回答任何技术、方案、配置、运维、开发类问题之前，**必须先搜索知识库**，不要默认从零回答：

1. 从用户问题中提取 1-3 个关键词
2. `grep -RIl --include='*.md' "<关键词>" "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"` 找匹配文档
3. 同时 grep 标签：`grep -RIl --include='*.md' 'tags:.*<关键词>' "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"`
4. Read 最相关的 1-2 篇，已有结论**直接引用原文**，只补充新内容
5. 读了 2 篇没找到 → 告诉用户"知识库暂无相关记录"，然后正常回答
6. 纯闲聊/简单问答可跳过检索

**例外：涉及测试/验证/排查/排错/检查/诊断时**，不要直接引用知识库已有结论：
- 必须在当前环境中**实际执行、获取真实结果**
- 将新结果与知识库已有结论对比，对比结论写回知识库对应文档
- 若差异显著 → 更新知识库，标注日期和差异原因
- 结论一致 → 写回"已验证，与 xx 结论一致"
- 知识库已有结论仅作为参考基准，不做判断依据

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
| GitHub 发布 | `grep -RIl --include='*.md' '发布规范' "${OBSIDIAN_VAULT:-$HOME/obsidian/知识库}/Claude方案"`（本规则优先于下方内联版本） |

会话结束时的项目总结和 AI 开发参考由 Stop Hook 自动处理，无需手动干预。

## GitHub 发布规范

完成代码改动并验证通过后，按以下流程发布：

### 提交范围

**只提交可复用代码/配置：**
| 可提交 | 不可提交 |
|--------|----------|
| `.claude/hooks/*.py` 脚本 | `settings.local.json`（含本地权限） |
| `.claude/skills/*/SKILL.md` | `settings.json`（含个人 token/环境变量） |
| `.claude/scripts/*.py` 工具 | `知识库/` 内任何 `.md`（个人笔记/方案/断点） |
| `CLAUDE.md` 项目规则 | `.claude/logs/`、`__pycache__/` |
| `install.sh` / `uninstall.sh` | `.env`、`.pytest_cache/` |
| `tests/` 测试代码 | 含 IP/密码/token/密钥/Bearer 的任何文件 |
| `README.md` | `.claude/worktrees/` |

**敏感数据检查清单**（提交前 grep 确认）：
```bash
grep -RIl 'sk-\|ghp_\|github_pat_\|Bearer\|ANTHROPIC_AUTH_TOKEN\|password\|密钥\|密码\|token.*=' . --include='*.py' --include='*.md'
```
命中任何文件 → 脱敏处理或移出暂存区后再提交。

### 发布步骤

1. `git add -A && git diff --cached --stat` 确认提交范围无误
2. `git commit -m "<type>: <描述>" && git push origin main`
3. `git tag -a v<X.Y.Z> -m "<简述>" && git push origin v<X.Y.Z>`
4. `GH_TOKEN=$(gh auth token) gh release create v<X.Y.Z> --title "<标题>" --notes "<内容>"`
5. Release 标题：`v<X.Y.Z>: <一句话总结>`
6. Release 内容：分点列出新增/修复/Breaking changes，不写流水账
7. Breaking changes 显式标注并说明迁移方式
