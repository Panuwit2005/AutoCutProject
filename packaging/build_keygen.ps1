<#
  build_keygen.ps1 - build the ADMIN-only app "AutoCut Admin" (AutoCutAdmin.exe).

  Run:
      powershell -ExecutionPolicy Bypass -File "packaging\build_keygen.ps1"

  Output: release\admin\AutoCutAdmin.exe  (+ a copy of admin_private_key.pem)
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

Write-Host "`n==== Building AutoCutAdmin.exe ====" -ForegroundColor Cyan
Push-Location $PROJ
try {
    & $PYV -m PyInstaller --noconfirm --clean (Join-Path $PKG "Keygen.spec")
    if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
} finally { Pop-Location }

$exe = Join-Path $PROJ "dist\AutoCutAdmin.exe"
if (-not (Test-Path $exe)) { Die "exe not produced" }

New-Item -ItemType Directory -Force $ADMIN | Out-Null
Copy-Item $exe $ADMIN -Force
Copy-Item (Join-Path $PKG "admin_private_key.pem") $ADMIN -Force

$readme = Join-Path $ADMIN "อ่านก่อนใช้-แอดมิน.txt"
@"
AutoCut Admin — เครื่องมือสำหรับแอดมิน (ห้ามส่งให้ลูกค้า)
========================================================

โปรแกรมนี้มี 2 หน้าที่ (แท็บด้านบน):

1) สร้างคีย์ (เปิดใช้งานให้ลูกค้า)
   - ดับเบิลคลิก AutoCutAdmin.exe
   - ขอ "รหัสเครื่อง (Machine ID)" จากลูกค้า (ลูกค้าก๊อปจากหน้าแอป)
   - วางรหัสเครื่อง -> ใส่ชื่อ/วันหมดอายุ (ไม่บังคับ) -> กด "สร้างคีย์"
   - ก๊อปคีย์ส่งกลับให้ลูกค้าไปวางเปิดใช้งาน (คีย์ผูกกับเครื่องนั้นเครื่องเดียว)

2) เผยแพร่อัปเดต (ส่งโค้ดใหม่ให้ลูกค้าอัตโนมัติ)
   - ใส่เลขเวอร์ชันใหม่ (ต้องสูงขึ้นเสมอ เช่น 1.5) -> กดสร้าง
   - จะได้ไฟล์ในโฟลเดอร์ update\ -> push ขึ้น GitHub -> ลูกค้าได้อัปเดตเอง

⚠️ สำคัญมาก:
   - ไฟล์ admin_private_key.pem คือ "กุญแจลับ" สำหรับเซ็นคีย์และอัปเดต
   - ต้องอยู่โฟลเดอร์เดียวกับ AutoCutAdmin.exe เสมอ
   - ห้ามส่งให้ลูกค้า / ห้ามอัปขึ้นอินเทอร์เน็ต / สำรองไว้ที่ปลอดภัย
     ถ้ากุญแจหาย จะออกคีย์และอัปเดตไม่ได้อีกเลย
"@ | Out-File -FilePath $readme -Encoding utf8

Write-Host "`n  [OK] $ADMIN\AutoCutAdmin.exe" -ForegroundColor Green
Write-Host "  [OK] private key copied next to it (keep this folder private)" -ForegroundColor Green
