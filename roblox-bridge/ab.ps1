# ab.ps1 — one-liner for the Arena bridge. No env vars, no visible token.
#
#   .\ab.ps1 health
#   .\ab.ps1 ping
#   .\ab.ps1 runfile jobs\baseline_survey.lua
#   .\ab.ps1 run "return game:GetService('HttpService'):JSONEncode({place=game.Name})"
#   .\ab.ps1 survey
#
# Sets BRIDGE_URL (default http://127.0.0.1:8077) and reads the token from
# bridge.token next to itself when BRIDGE_TOKEN is not already set, so the
# token never appears in commands or shell history.

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Cmd)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:BRIDGE_URL) { $env:BRIDGE_URL = "http://127.0.0.1:8077" }
if (-not $env:BRIDGE_TOKEN) {
	$tok = Join-Path $here "bridge.token"
	if (Test-Path $tok) { $env:BRIDGE_TOKEN = (Get-Content $tok -Raw).Trim() }
}
if (-not $env:BRIDGE_TOKEN) {
	Write-Error "No token found. Set BRIDGE_TOKEN or put a bridge.token file next to ab.ps1."
	exit 1
}
python (Join-Path $here "arena_studio.py") @Cmd
exit $LASTEXITCODE
