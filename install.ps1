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

Write-Host "[checkpoint] 仓库目录: $ScriptDir"
Write-Host "[checkpoint] hook 脚本: $HookPath"

if (-not (Test-Path $HookPath)) {
    Write-Error "[checkpoint] 找不到 hook 脚本: $HookPath"
    exit 1
}

$SettingsDir = Split-Path -Parent $Settings
if (-not (Test-Path $SettingsDir)) {
    New-Item -ItemType Directory -Path $SettingsDir | Out-Null
}

# 用 python 做 JSON 合并（与 install.sh 完全一致，跨 PS 5.1/7 稳定）
$py = @'
import json, sys, os, shutil
settings_path = os.path.expanduser(sys.argv[1])
hook_path = sys.argv[2]
try:
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}
if os.path.exists(settings_path):
    bak = settings_path + ".bak"
    shutil.copy2(settings_path, bak)
    print(f"[checkpoint] 已备份原配置: {bak}")
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])
stop[:] = [
    e for e in stop
    if not any("checkpoint.py" in h.get("command", "") for h in e.get("hooks", []))
]
stop.append({"hooks": [{"type": "command", "command": f'python "{hook_path}"'}]})
with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"[checkpoint] Stop hook 已注册到 {settings_path}")
print(f"[checkpoint]   命令: python \"{hook_path}\"")
'@

$py | python - $Settings $HookPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "[checkpoint] 安装完成。"
Write-Host "[checkpoint] 接下来:"
Write-Host "  1. API 凭证: Claude Code 已配的 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 自动复用。"
Write-Host "  2. Obsidian vault: 默认 ~/obsidian/知识库。若在别处，在 settings.json 的 env 里加 OBSIDIAN_VAULT。"
Write-Host "  3. 新开一个 claude 会话即生效。"
Write-Host "[checkpoint] 卸载: 删 settings.json 里 hooks.Stop 中指向 checkpoint.py 的条目，或恢复 .bak。"
