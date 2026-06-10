<#
  build_keygen.ps1 - build the ADMIN-only key generator app (AutoCutKeygen.exe).

  Run:
      powershell -ExecutionPolicy Bypass -File "packaging\build_keygen.ps1"

  Output: release\admin\AutoCutKeygen.exe  (+ a copy of admin_private_key.pem)
  Keep that folder private - never give it to a customer.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$PKG  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PROJ = Split-Path -Parent $PKG
$PYV  = Join-Path $PKG ".buildvenv\Scripts\python.exe"
$ADMIN = Join-Path $PROJ "release\admin"

function Info($m){ Write-Host "  $m" -ForegroundColor Gray }
function Die($m){ Write-Host "  [ERROR] $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $PYV)) { Die "build venv not found - run build.ps1 first" }
if (-not (Test-Path (Join-Path $PKG "admin_private_key.pem"))) {
    Die "admin_private_key.pem not found in packaging\ - generate it first"
}

Write-Host "`n==== Building AutoCutKeygen.exe ====" -ForegroundColor Cyan
Push-Location $PROJ
try {
    & $PYV -m PyInstaller --noconfirm --clean (Join-Path $PKG "Keygen.spec")
    if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
} finally { Pop-Location }

$exe = Join-Path $PROJ "dist\AutoCutKeygen.exe"
if (-not (Test-Path $exe)) { Die "exe not produced" }

New-Item -ItemType Directory -Force $ADMIN | Out-Null
Copy-Item $exe $ADMIN -Force
Copy-Item (Join-Path $PKG "admin_private_key.pem") $ADMIN -Force

$readme = Join-Path $ADMIN "READ-ME-ADMIN.txt"
@"
AutoCut Pro - Admin Key Generator
=================================
- Double-click AutoCutKeygen.exe
- Paste the customer's Machine ID, optional name + expiry, click Generate, copy the key.
- admin_private_key.pem MUST stay in this folder. It is SECRET - never send it to a customer.
"@ | Out-File -FilePath $readme -Encoding utf8

Write-Host "`n  [OK] $ADMIN\AutoCutKeygen.exe" -ForegroundColor Green
Write-Host "  [OK] private key copied next to it (keep this folder private)" -ForegroundColor Green
