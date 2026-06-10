<#
  build.ps1 — one-shot builder for AutoCut Pro (portable .zip + installer .exe)

  Run from anywhere:
      powershell -ExecutionPolicy Bypass -File "packaging\build.ps1"

  What it does (each step is idempotent / safe to re-run):
    1. Create a Python 3.11 build venv and install pinned deps + PyInstaller.
    2. Stage payload next to the future exe:
         - ffmpeg.exe + ffprobe.exe (copied from this machine's winget install,
           or from -FfmpegDir if given)
         - faster-whisper "small" model (downloaded once, then offline)
         - Noto Sans Thai font (best-effort; Tahoma is the fallback)
    3. PyInstaller -> dist\AutoCutPro\
    4. Copy the payload next to the exe.
    5. Produce release\AutoCutPro-portable.zip
    6. Compile release\AutoCutPro-Setup.exe with Inno Setup (if installed).
#>
[CmdletBinding()]
param(
    [string]$FfmpegDir = "",        # folder containing ffmpeg.exe/ffprobe.exe
    [switch]$SkipInstaller,         # only build the portable zip
    [switch]$Clean                  # wipe build/dist before building
)

$ErrorActionPreference = "Stop"
$PKG     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PROJ    = Split-Path -Parent $PKG
$VENV    = Join-Path $PKG ".buildvenv"
$PYV     = Join-Path $VENV "Scripts\python.exe"
$PAYLOAD = Join-Path $PKG "payload"
$DIST    = Join-Path $PROJ "dist\AutoCutPro"
$RELEASE = Join-Path $PROJ "release"

function Step($m) { Write-Host "`n==== $m ====" -ForegroundColor Cyan }
function Info($m) { Write-Host "  $m" -ForegroundColor Gray }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Die($m)  { Write-Host "  [ERROR] $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
Step "1/6  Python 3.11 build environment"

if ($Clean) {
    Info "Cleaning build / dist ..."
    Remove-Item -Recurse -Force (Join-Path $PROJ "build"), $DIST -ErrorAction SilentlyContinue
}

if (-not (Test-Path $PYV)) {
    # Locate a Python 3.11 interpreter.
    $py311 = $null
    try { & py -3.11 --version *> $null; if ($LASTEXITCODE -eq 0) { $py311 = "py -3.11" } } catch {}
    if (-not $py311) {
        $cand = Get-ChildItem "C:\Users\*\AppData\Local\Programs\Python\Python311\python.exe",
                              "C:\Program Files\Python311\python.exe",
                              "C:\Python311\python.exe" -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if ($cand) { $py311 = "`"$($cand.FullName)`"" }
    }
    if (-not $py311) { Die "Python 3.11 not found. Install it: winget install Python.Python.3.11" }
    Info "Creating venv with: $py311"
    Invoke-Expression "$py311 -m venv `"$VENV`""
    if (-not (Test-Path $PYV)) { Die "venv creation failed" }
} else {
    Info "Reusing existing venv"
}

Info "Upgrading pip ..."
& $PYV -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Die "pip upgrade failed" }
Info "Installing build dependencies (this can take a few minutes the first time) ..."
& $PYV -m pip install -r (Join-Path $PKG "requirements-build.txt") --quiet
if ($LASTEXITCODE -ne 0) { Die "dependency install failed" }
Ok "Build environment ready"

# ---------------------------------------------------------------------------
Step "2/6  Stage payload (ffmpeg + model + font)"

New-Item -ItemType Directory -Force (Join-Path $PAYLOAD "ffmpeg") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $PAYLOAD "models") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $PAYLOAD "fonts")  | Out-Null

# --- ffmpeg / ffprobe ---
$ffOut = Join-Path $PAYLOAD "ffmpeg"
if ((Test-Path (Join-Path $ffOut "ffmpeg.exe")) -and (Test-Path (Join-Path $ffOut "ffprobe.exe"))) {
    Info "ffmpeg already staged"
} else {
    $src = $FfmpegDir
    if (-not $src) {
        $hit = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { $src = $hit.DirectoryName }
    }
    if (-not $src) {
        $onPath = (Get-Command ffmpeg -ErrorAction SilentlyContinue)
        if ($onPath) { $src = Split-Path -Parent $onPath.Source }
    }
    if (-not $src -or -not (Test-Path (Join-Path $src "ffmpeg.exe"))) {
        Die "Could not find ffmpeg.exe. Pass -FfmpegDir 'C:\path\to\ffmpeg\bin'"
    }
    Info "Copying ffmpeg from: $src"
    Copy-Item (Join-Path $src "ffmpeg.exe")  $ffOut -Force
    Copy-Item (Join-Path $src "ffprobe.exe") $ffOut -Force
    Ok "ffmpeg + ffprobe staged"
}

# --- Whisper small model (offline) ---
$modelDir = Join-Path $PAYLOAD "models\faster-whisper-small"
if (Test-Path (Join-Path $modelDir "model.bin")) {
    Info "Whisper model already staged"
} else {
    Info "Downloading faster-whisper 'small' model (~460MB, one time) ..."
    $dl = "from faster_whisper import download_model; " +
          "download_model('small', output_dir=r'$modelDir')"
    & $PYV -c $dl
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $modelDir "model.bin"))) {
        Die "model download failed"
    }
    Ok "Whisper model staged"
}

# --- Thai font (best-effort; Tahoma is the guaranteed fallback) ---
$fontFile = Join-Path $PAYLOAD "fonts\NotoSansThai.ttf"
if (Test-Path $fontFile) {
    Info "Thai font already staged"
} else {
    $url = "https://github.com/google/fonts/raw/main/ofl/notosansthai/NotoSansThai%5Bwdth,wght%5D.ttf"
    try {
        Info "Downloading Noto Sans Thai (optional) ..."
        Invoke-WebRequest -Uri $url -OutFile $fontFile -UseBasicParsing -TimeoutSec 60
        Ok "Thai font staged"
    } catch {
        Info "Font download skipped (will use Windows Tahoma): $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
Step "3/6  PyInstaller build"
Push-Location $PROJ
try {
    & $PYV -m PyInstaller --noconfirm --clean (Join-Path $PKG "AutoCutPro.spec")
    if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
} finally { Pop-Location }
if (-not (Test-Path (Join-Path $DIST "AutoCutPro.exe"))) { Die "exe not produced" }
Ok "PyInstaller build complete"

# ---------------------------------------------------------------------------
Step "4/6  Copy payload next to exe"
Copy-Item (Join-Path $PAYLOAD "ffmpeg") $DIST -Recurse -Force
Copy-Item (Join-Path $PAYLOAD "models") $DIST -Recurse -Force
if (Get-ChildItem (Join-Path $PAYLOAD "fonts") -Filter *.ttf -ErrorAction SilentlyContinue) {
    Copy-Item (Join-Path $PAYLOAD "fonts") $DIST -Recurse -Force
}
# A friendly readme for the customer in the portable folder.
$readme = Join-Path $DIST "อ่านก่อนใช้งาน.txt"
@"
AutoCut Pro — โปรแกรมตัดต่อวิดีโอรีวิวอัตโนมัติ

วิธีใช้:
  1. ดับเบิลคลิก  AutoCutPro.exe
  2. โปรแกรมจะเปิดเบราว์เซอร์ให้อัตโนมัติ (http://localhost:5000)
  3. ลากไฟล์วิดีโอลงไป -> ตั้งค่า -> กด "ตัดต่อวิดีโออัตโนมัติ"

หมายเหตุ:
  - ทำงานแบบออฟไลน์ 100% ไม่ต้องต่ออินเทอร์เน็ต
  - ครั้งแรกที่เปิด Windows อาจถามเรื่องไฟร์วอลล์ ให้กด Allow
  - ปิดโปรแกรมด้วยการปิดหน้าต่างสีดำ (Console)
"@ | Out-File -FilePath $readme -Encoding utf8
Ok "Payload copied"

# ---------------------------------------------------------------------------
Step "5/6  Portable zip"
New-Item -ItemType Directory -Force $RELEASE | Out-Null
$zip = Join-Path $RELEASE "AutoCutPro-portable.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $DIST -DestinationPath $zip -CompressionLevel Optimal
Ok "Portable zip: $zip"

# ---------------------------------------------------------------------------
Step "6/6  Installer (.exe)"
if ($SkipInstaller) {
    Info "Skipped (-SkipInstaller)"
} else {
    $iscc = Get-ChildItem "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                          "C:\Program Files\Inno Setup 6\ISCC.exe",
                          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $iscc) {
        Info "Inno Setup not found — skipping installer. (winget install JRSoftware.InnoSetup)"
    } else {
        & $iscc.FullName (Join-Path $PKG "installer.iss")
        if ($LASTEXITCODE -ne 0) { Die "Inno Setup compile failed" }
        Ok "Installer: $(Join-Path $RELEASE 'AutoCutPro-Setup.exe')"
    }
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " BUILD DONE" -ForegroundColor Green
Write-Host " Portable : $zip"
if (-not $SkipInstaller) { Write-Host " Installer: $(Join-Path $RELEASE 'AutoCutPro-Setup.exe')" }
Write-Host "========================================" -ForegroundColor Green
