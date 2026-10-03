# PotatoDoc backend launcher: uvicorn + stable Cloudflare named tunnel.
# Usage:  powershell -ExecutionPolicy Bypass -File start_backend.ps1
# Public URL is FIXED: https://potatodoc.shadbhavregmi.com.np
# (tunnel connector runs separately via cloudflared service; this script only starts uvicorn).
# Old quick-tunnel mode (random trycloudflare.com) is kept with -Quick flag for fallback.

$ErrorActionPreference = "Stop"
param([switch]$Quick)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stableUrl = "https://potatodoc.shadbhavregmi.com.np"
$logDir = Join-Path $env:TEMP "potatodoc-backend"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$cfCandidates = @(
    "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
    "$env:ProgramFiles\cloudflared\cloudflared.exe",
    "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
)
$cloudflared = $cfCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cloudflared) {
    $cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
}
if (-not $cloudflared) { throw "cloudflared not found. Install: winget install cloudflare.cloudflared" }

# stop previous uvicorn only — DO NOT kill the named-tunnel connector
# (it runs as `cloudflared tunnel run`, not `--url`, so this filter spares it).
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='cloudflared.exe'" |
    Where-Object { $_.CommandLine -like "*uvicorn app:app*" -or $_.CommandLine -like "*tunnel --url*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Process -FilePath python -ArgumentList "-m","uvicorn","app:app","--host","127.0.0.1","--port","8000" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput "$logDir\uvicorn.out" -RedirectStandardError "$logDir\uvicorn.err"

# Cold start imports torch + torchvision (~15 s on a test machine), and uvicorn
# only binds the port after the app module has loaded — poll rather than guess.
$ping = $null
foreach ($i in 1..25) {
    Start-Sleep -Seconds 2
    $ping = try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/ping -UseBasicParsing -TimeoutSec 5).Content } catch { $null }
    if ($ping -eq "Hello, I am alive") { break }
}
if ($ping -ne "Hello, I am alive") { Get-Content "$logDir\uvicorn.err" -Tail 20; throw "backend failed to start" }

if ($Quick) {
$tunnelLog = "$logDir\tunnel.log"
Remove-Item $tunnelLog -ErrorAction SilentlyContinue
Start-Process -FilePath $cloudflared -ArgumentList "tunnel","--url","http://127.0.0.1:8000" `
    -WindowStyle Hidden -RedirectStandardError $tunnelLog

$url = $null
foreach ($i in 1..20) {
    Start-Sleep -Seconds 2
    $m = Select-String -Path $tunnelLog -Pattern 'https://\S+?trycloudflare\.com' -ErrorAction SilentlyContinue
    if ($m) { $url = $m[0].Matches.Value; break }
}
if (-not $url) { Get-Content $tunnelLog -Tail 20; throw "tunnel failed to start" }

Write-Host ""
Write-Host "  Backend : http://127.0.0.1:8000  (local)"
Write-Host "  Public  : $url  (quick-tunnel fallback)"
Write-Host ""
Write-Host "  Set in mobile\.env ->  EXPO_PUBLIC_API_URL=$url"
Write-Host "  (restart of this script = new URL; keep PC awake while using the app)"
return
}

# Stable named-tunnel mode: connector already runs separately, just verify origin.
$pub = $null
foreach ($i in 1..6) {
    Start-Sleep -Seconds 2
    $pub = try { (Invoke-WebRequest -Uri "$stableUrl/ping" -UseBasicParsing -TimeoutSec 10).Content } catch { $null }
    if ($pub -eq "Hello, I am alive") { break }
}

Write-Host ""
Write-Host "  Backend : http://127.0.0.1:8000  (local)"
Write-Host "  Public  : $stableUrl"
Write-Host ""
Write-Host "  Set in mobile\.env ->  EXPO_PUBLIC_API_URL=$stableUrl"
if ($pub -eq "Hello, I am alive") {
Write-Host "  Tunnel  : OK (connector -> 127.0.0.1:8000)"
} else {
Write-Host "  Tunnel  : DNS/propagation pending or connector not pointing to :8000."
Write-Host "  Fix     : ipconfig /flushdns, 5-10 min wait, check Routes tab = Published application http://127.0.0.1:8000"
}
