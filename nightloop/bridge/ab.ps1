# HANDS MODE & CLI helper for Windows PowerShell (v3.0).
# Reads BRIDGE_TOKEN from bridge.token next to this script (or auto-recovers
# from installed ArenaBridge.lua in %LOCALAPPDATA%\Roblox\Plugins!) so tokens
# never appear in shell history. Enforces UTF-8 so em-dashes & symbols are never
# mangled by Windows cp1252.
#
# Usage (from roblox-bridge on your PC):
#   .\ab.ps1 health
#   .\ab.ps1 ping
#   .\ab.ps1 state
#   .\ab.ps1 check ..\nightloop\src
#   .\ab.ps1 drift ..\nightloop\src ServerScriptService.NightLoop
#   .\ab.ps1 sync  ..\nightloop\src ServerScriptService.NightLoop
#   .\ab.ps1 runfile ..\jobs\baseline_survey.lua

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Cmd)

[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $env:BRIDGE_URL) {
    $env:BRIDGE_URL = "http://127.0.0.1:8077"
}

if (-not $env:BRIDGE_TOKEN) {
    $tokFile = Join-Path $here "bridge.token"
    if (Test-Path $tokFile) {
        $env:BRIDGE_TOKEN = (Get-Content -Encoding UTF8 $tokFile -Raw).Trim()
    } else {
        $pluginPath = Join-Path $env:LOCALAPPDATA "Roblox\Plugins\ArenaBridge.lua"
        if (Test-Path $pluginPath) {
            $m = Select-String -Path $pluginPath -Pattern 'local BRIDGE_TOKEN = "([^"]+)"' | Select-Object -First 1
            if ($m -and $m.Matches[0].Groups[1].Value -ne "arena-demo-7f3a") {
                $env:BRIDGE_TOKEN = $m.Matches[0].Groups[1].Value
            }
        }
    }
    if (-not $env:BRIDGE_TOKEN) {
        Write-Error "No token found. Run 'python setup.py --install-only' or place bridge.token next to ab.ps1."
        exit 1
    }
}

python (Join-Path $here "arena_studio.py") @Cmd
exit $LASTEXITCODE
