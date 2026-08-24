# TalkCash - P0 dogrulama (PowerShell)
#
# Usage:
#   .\scripts\p0-verify.ps1                                    # localhost'a karsi (guvenli)
#   .\scripts\p0-verify.ps1 -ApiUrl https://... -ConfirmProd   # canliya karsi (test kullanıcisi ACAR!)
#
# NOT: Smoke test hedef API'de gecici kullanici olusturur. Canliya karsi
# calistirmadan once -ConfirmProd bayragini acikca vermelisiniz.
param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$InternalSecret = $env:INTERNAL_UPGRADE_SECRET,
    [switch]$ConfirmProd
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent

if ($ApiUrl -match "onrender\.com" -and -not $ConfirmProd) {
    Write-Host "RED: Bu script hedef API'de GECICI TEST KULLANICISI olusturur." -ForegroundColor Red
    Write-Host "Canliya karsi calistirmak icin: -ConfirmProd"
    exit 1
}

$Api = $ApiUrl.TrimEnd("/")

Write-Host "==> Health: $Api/health" -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "$Api/health" -TimeoutSec 45
Write-Host "  status: $($health.status)"

if ($InternalSecret) {
    try {
        $headers = @{ "x-internal-upgrade-secret" = $InternalSecret }
        $detailed = Invoke-RestMethod -Uri "$Api/health/detailed" -Headers $headers -TimeoutSec 45
        Write-Host "  version: $($detailed.observability.version)"
        Write-Host "  launch_readiness:" -ForegroundColor Yellow
        $detailed.launch_readiness | Format-List
    } catch {
        Write-Host "  /health/detailed erisilemedi ($($_.Exception.Message)) - detaylar atlandi" -ForegroundColor Yellow
    }
} else {
    Write-Host "  INTERNAL_UPGRADE_SECRET verilmedi - detayli health atlandi" -ForegroundColor DarkGray
}

Write-Host "==> Smoke test" -ForegroundColor Cyan
$env:API_URL = $Api
python (Join-Path $Root "scripts\smoke_test.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`nP0 verify OK" -ForegroundColor Green
