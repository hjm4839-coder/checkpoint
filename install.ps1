# checkpoint 机制安装脚本 (Windows / PowerShell)
# 把 Stop hook 注册到用户级 %USERPROFILE%\.claude\settings.json
# 幂等：重复运行不重复注册。不动已有的 env / 其他 hook。
$ErrorActionPreference = "Stop"

# PS 5.1 默认 GBK，切 UTF-8 避免中文乱码 + 给 python 子进程设编码
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HookPath  = Join-Path $ScriptDir ".claude\hooks\checkpoint.py"
$Settings  = Join-Path $env:USERPROFILE ".claude\settings.json"
$DefaultVault = Join-Path $env:USERPROFILE "obsidian\知识库"

Write-Host "[checkpoint] 仓库目录: $ScriptDir"
Write-Host "[checkpoint] hook 脚本: $HookPath"

if (-not (Test-Path $HookPath)) {
    Write-Error "[checkpoint] 找不到 hook 脚本: $HookPath"
    exit 1
}

# 询问 Obsidian vault 路径
Write-Host ""
Write-Host "断点笔记会写到你的 Obsidian vault 下的 Claude方案/ 目录。"
$Vault = Read-Host "你的 Obsidian vault 路径 [默认: $DefaultVault]"
if (-not $Vault) { $Vault = $DefaultVault }

$SettingsDir = Split-Path -Parent $Settings
if (-not (Test-Path $SettingsDir)) {
    New-Item -ItemType Directory -Path $SettingsDir | Out-Null
}

# 用 python 做 JSON 合并（与 install.sh 完全一致，跨 PS 5.1/7 稳定）
$py = @'
import json, sys, os, shutil
settings_path = os.path.expanduser(sys.argv[1])
hook_path = sys.argv[2]
vault = os.path.expanduser(sys.argv[3])
try:
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}
if os.path.exists(settings_path):
    bak = settings_path + ".bak"
    shutil.copy2(settings_path, bak)
    print(f"[checkpoint] 已备份原配置: {bak}")
# 注册 Stop hook（幂等去重）
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])
stop[:] = [
    e for e in stop
    if not any("checkpoint.py" in h.get("command", "") for h in e.get("hooks", []))
]
stop.append({"hooks": [{"type": "command", "command": f'python "{hook_path}"'}]})
# 写入 OBSIDIAN_VAULT（覆盖旧值，保留其他 env）
env = data.setdefault("env", {})
env["OBSIDIAN_VAULT"] = vault
with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"[checkpoint] Stop hook 已注册: python \"{hook_path}\"")
print(f"[checkpoint] OBSIDIAN_VAULT = {vault}")
'@

$py | python - $Settings $HookPath $Vault
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 装 skill 到用户级（任意目录可用 /checkpoint）
$SkillSrc = Join-Path $ScriptDir ".claude\skills\checkpoint"
$SkillDst = Join-Path $env:USERPROFILE ".claude\skills\checkpoint"
$SkillDir = Split-Path -Parent $SkillDst
if (-not (Test-Path $SkillDir)) { New-Item -ItemType Directory -Path $SkillDir | Out-Null }
if (Test-Path $SkillDst) { Remove-Item -Recurse -Force $SkillDst }
Copy-Item -Recurse $SkillSrc $SkillDst
# SKILL.md 里的 hook 路径替换成本机实际路径
$SkillMd = Join-Path $SkillDst "SKILL.md"
(Get-Content $SkillMd -Raw -Encoding UTF8) -replace '~/obsidian/\.claude/hooks/checkpoint\.py', $HookPath | Set-Content $SkillMd -Encoding UTF8
Write-Host "[checkpoint] /checkpoint skill 已装到 $SkillDst"

# 若用户还没用户级 CLAUDE.md，创建带归档约定的模板
$UserClaude = Join-Path $env:USERPROFILE ".claude\CLAUDE.md"
if (-not (Test-Path $UserClaude)) {
    $ClaudeDir = Split-Path -Parent $UserClaude
    if (-not (Test-Path $ClaudeDir)) { New-Item -ItemType Directory -Path $ClaudeDir | Out-Null }
    @'
# 全局指令

## 方案归档

方案敲定后直接 Write 到 `$OBSIDIAN_VAULT/Claude方案/<项目名>/<方案标题>.md`。
`$OBSIDIAN_VAULT` 默认为 `~/obsidian/知识库/`，可通过环境变量覆盖。

```yaml
---
date: YYYY-MM-DD
project: 项目名
tags: [claude/方案, ...]
---
# 标题
## 背景  ## 方案  ## 关键决策  ## 实施步骤  ## 相关笔记
```

归档后会话断点会自动变 ✅，并链接到方案文件。
'@ | Set-Content $UserClaude -Encoding UTF8
    Write-Host "[checkpoint] 已创建 $UserClaude（全局归档指令）"
} else {
    Write-Host "[checkpoint]   $UserClaude 已存在，跳过（如需归档指令请手动合并）"
}

Write-Host ""
Write-Host "[checkpoint] 安装完成。"
Write-Host "  - API 凭证: Claude Code 已配的自动复用。"
Write-Host "  - vault 路径已写入 env.OBSIDIAN_VAULT，想改重跑本脚本。"
Write-Host "  - 新开一个 claude 会话即生效。"
Write-Host "[checkpoint] 卸载: 删 settings.json 里 hooks.Stop 中指向 checkpoint.py 的条目，或恢复 .bak。"
