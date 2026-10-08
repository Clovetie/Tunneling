<#
  relay.ps1 — Arena <-> Roblox Studio relay client. Runs on YOUR PC.

  Arena's sandbox cannot reach your machine, so the wire is inverted: this
  script polls Arena's relay, runs each job against your local bridge
  (http://127.0.0.1:8077), and posts the answer back. Leave it running while
  we work — that is the whole connection.

  Usage (from your roblox-bridge folder, next to bridge.token):

      .\relay.ps1 -Url https://8787-<sandbox>.e2b.app

      # if that gives HTTP 403, the preview is token-gated: open the preview
      # in a browser tab, copy the ?e2b-traffic-access-token=... value out of
      # the address bar, and pass it:
      .\relay.ps1 -Url https://8787-<sandbox>.e2b.app -TrafficToken <value>

  The token is read from .\bridge.token (or -Token / -BridgeToken / $env vars).
  Nothing is written to disk; Ctrl+C stops it.
#>
param(
  [string]$Url = $env:RELAY_URL,
  [string]$Token,
  [string]$Bridge = "http://127.0.0.1:8077",
  [string]$BridgeToken,
  [string]$TrafficToken = $env:E2B_TRAFFIC_TOKEN,
  [int]$Interval = 2,
  [int]$Wait = 60,
  [switch]$Once,
  [switch]$Once_ReportOnly
)

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tokFile = Join-Path $here 'bridge.token'
if (-not $BridgeToken -and (Test-Path $tokFile)) { $BridgeToken = (Get-Content $tokFile -Raw).Trim() }
if (-not $Token -and $BridgeToken) { $Token = $BridgeToken }
if (-not $Token) { $Token = $env:BRIDGE_TOKEN }
if (-not $BridgeToken) { $BridgeToken = $env:BRIDGE_TOKEN }
if (-not $Url)   { Write-Host "[relay] no -Url given (the https://...e2b.app preview URL from Arena)." -ForegroundColor Red; exit 1 }
if (-not $Token) { Write-Host "[relay] no token: expected bridge.token next to this script, or -Token." -ForegroundColor Red; exit 1 }
$Url = $Url.TrimEnd('/')
$Headers = @{}
if ($TrafficToken) { $Headers['e2b-traffic-access-token'] = $TrafficToken }

function Relay([string]$path, [string]$method = 'GET', [string]$body = '') {
  $sep = if ($path.Contains('?')) { '&' } else { '?' }
  $uri = "$Url$path$sep" + "token=" + [uri]::EscapeDataString($Token)
  if ($TrafficToken) { $uri += "&e2b-traffic-access-token=" + [uri]::EscapeDataString($TrafficToken) }
  $a = @{ Uri = $uri; Method = $method; TimeoutSec = 140; UseBasicParsing = $true }
  if ($Headers.Count) { $a.Headers = $Headers }
  if ($body) { $a.Body = [Text.Encoding]::UTF8.GetBytes($body); $a.ContentType = 'application/json; charset=utf-8' }
  return Invoke-WebRequest @a
}

function LocalBridge([string]$body) {
  $uri = "$Bridge/api/jobs?token=" + [uri]::EscapeDataString($BridgeToken)
  return Invoke-WebRequest -Uri $uri -Method Post -TimeoutSec 140 -UseBasicParsing `
    -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8'
}

function Report([string]$json) { try { Relay '/api/report' 'POST' $json | Out-Null } catch {} }

# ---- 1. can we see Arena's relay? -----------------------------------------
Write-Host "[relay] polling $Url ..."
try {
  $st = (Relay '/api/state').Content | ConvertFrom-Json
  Write-Host ("[relay] Arena relay v{0} reachable — {1} client(s) attached." -f $st.version, $st.sse_clients) -ForegroundColor Green
} catch {
  $code = ''
  try { $code = [int]$_.Exception.Response.StatusCode } catch {}
  Write-Host "[relay] CANNOT reach the relay: $($_.Exception.Message)" -ForegroundColor Red
  if ($code -eq 403) {
    Write-Host "[relay] 403 = the preview URL is token-gated. Open the Arena preview in a" -ForegroundColor Yellow
    Write-Host "[relay] browser tab, copy e2b-traffic-access-token from the address bar and" -ForegroundColor Yellow
    Write-Host "[relay] re-run with -TrafficToken <value>. (Or just use the browser page.)" -ForegroundColor Yellow
  } else {
    Write-Host "[relay] Check the URL, or use the browser page instead (open the preview)." -ForegroundColor Yellow
  }
  exit 2
}

# ---- 2. is the local bridge (and Studio) there? ---------------------------
$healthRaw = ''
try {
  $healthRaw = (Invoke-WebRequest -Uri "$Bridge/api/health?token=" + [uri]::EscapeDataString($BridgeToken) `
      -UseBasicParsing -TimeoutSec 10).Content
  $h = $healthRaw | ConvertFrom-Json
  if ($h.studio_connected) {
    Write-Host "[relay] local bridge ok — Studio CONNECTED, place `"$($h.studio.place)`", plugin $($h.studio.client)" -ForegroundColor Green
  } else {
    Write-Host "[relay] local bridge ok, but Studio has not polled (closed, or plugin off)." -ForegroundColor Yellow
  }
  Report ('{"where":"powershell","ok":true,"health":' + $healthRaw + '}')
} catch {
  Write-Host "[relay] local bridge NOT reachable at $Bridge — start it (python server.py)." -ForegroundColor Red
  Report ('{"where":"powershell","ok":false,"error":"' + ($_.Exception.Message -replace '"', "'") + '"}')
}
if ($Once) { exit 0 }

# ---- 3. the loop ----------------------------------------------------------
$client = 'ps-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
$polls = 0; $done = 0; $failed = 0; $tick = 0
Write-Host "[relay] attached as $client. Ctrl+C to stop." -ForegroundColor Cyan
while ($true) {
  $polls++; $tick++
  try {
    $jobs = ((Relay "/api/jobs?client=$client&kind=powershell").Content | ConvertFrom-Json).jobs
  } catch {
    Write-Host "[relay] poll failed: $($_.Exception.Message)" -ForegroundColor Red
    Start-Sleep -Seconds 5; continue
  }
  foreach ($job in $jobs) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Write-Host ("[relay] job {0} {1} ..." -f $job.id, $job.type)
    try {
      $resp = LocalBridge $job.body
      $sw.Stop()
      $result = '{"id":"' + $job.id + '","response":' + $resp.Content + ',"ms":' + $sw.ElapsedMilliseconds + '}'
      $ok = ($resp.Content -match '"status"\s*:\s*"done"')
      if ($ok) { $done++; Write-Host ("[relay] job {0} done in {1} ms" -f $job.id, $sw.ElapsedMilliseconds) -ForegroundColor Green }
      else     { $failed++; Write-Host ("[relay] job {0} FAILED" -f $job.id) -ForegroundColor Red }
    } catch {
      $sw.Stop(); $failed++
      $msg = ($_.Exception.Message -replace '"', "'") -replace '[\r\n]', ' '
      Write-Host ("[relay] job {0} error: {1}" -f $job.id, $msg) -ForegroundColor Red
      $result = '{"id":"' + $job.id + '","error":"' + $msg + '","ms":' + $sw.ElapsedMilliseconds + '}'
    }
    try { Relay '/api/result' 'POST' $result | Out-Null } catch { Write-Host "[relay] could not return result: $($_.Exception.Message)" -ForegroundColor Red }
  }
  if ($tick -ge 8) {
    $tick = 0
    Write-Host ("[relay] watching — {0} poll(s), {1} job(s) ok, {2} failed." -f $polls, $done, $failed) -ForegroundColor DarkGray
    try {   # keep Arena's view of Studio fresh
      $raw = (Invoke-WebRequest -Uri "$Bridge/api/health?token=" + [uri]::EscapeDataString($BridgeToken) `
        -UseBasicParsing -TimeoutSec 10).Content
      Report ('{"where":"powershell","ok":true,"health":' + $raw + '}')
    } catch {}
  }
  if ($jobs.Count -eq 0) { Start-Sleep -Seconds $Interval }
}
