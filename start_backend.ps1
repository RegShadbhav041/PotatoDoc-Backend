# PotatoDoc backend launcher: uvicorn + free Cloudflare quick tunnel.
# Usage:  powershell -ExecutionPolicy Bypass -File start_backend.ps1
# Prints the public URL — paste it into ..\Potato\mobile\.env as EXPO_PUBLIC_API_URL
# (the URL changes every restart; quick tunnels are ephemeral and free).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
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

# stop previous instances of this stack
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='cloudflared.exe'" |
    Where-Object { $_.CommandLine -like "*uvicorn app:app*" -or $_.CommandLine -like "*tunnel --url*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Process -FilePath python -ArgumentList "-m","uvicorn","app:app","--host","127.0.0.1","--port","8000" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput "$logDir\uvicorn.out" -RedirectStandardError "$logDir\uvicorn.err"

Start-Sleep -Seconds 3
$ping = try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/ping -UseBasicParsing -TimeoutSec 10).Content } catch { $null }
if ($ping -ne "Hello, I am alive") { Get-Content "$logDir\uvicorn.err" -Tail 20; throw "backend failed to start" }

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
Write-Host "  Public  : $url"
Write-Host ""
Write-Host "  Set in mobile\.env ->  EXPO_PUBLIC_API_URL=$url"
Write-Host "  (restart of this script = new URL; keep PC awake while using the app)"
