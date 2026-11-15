# Copy Python packages (and Playwright's Chromium) that were installed from inside the Claude desktop app
# to the normal Windows locations, so run.bat / any PowerShell window can use them.
#
# Why: the Claude desktop app is an MSIX package. Anything it installs into %APPDATA% / %LOCALAPPDATA%
# (pip install --user, playwright install) lands in the app's private copy:
#   %LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Python   (instead of %APPDATA%\Python)
# Outside Claude, Python cannot see it -> "No module named uvicorn".
#
# Run it in a normal PowerShell window (NOT from inside Claude):  powershell -ExecutionPolicy Bypass -File fix_env.ps1
# Safe to run again after new installs: it only copies newer/missing files, never deletes anything.

$ErrorActionPreference = "Stop"
$pkgs = Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue
if (-not $pkgs) { Write-Host "Khong thay thu muc ao cua app Claude - khong can lam gi."; exit 0 }

foreach ($p in $pkgs) {
    $cache = Join-Path $p.FullName "LocalCache"
    $pairs = @(
        @{ src = Join-Path $cache "Roaming\Python";       dst = Join-Path $env:APPDATA "Python" },
        @{ src = Join-Path $cache "Local\ms-playwright";  dst = Join-Path $env:LOCALAPPDATA "ms-playwright" }
    )
    foreach ($x in $pairs) {
        if (-not (Test-Path $x.src)) { continue }
        Write-Host "Chep $($x.src)  ->  $($x.dst)"
        robocopy $x.src $x.dst /E /XO /R:1 /W:1 /NFL /NDL /NP /MT:16 | Select-Object -Last 12
        if ($LASTEXITCODE -ge 8) { throw "robocopy loi (ma $LASTEXITCODE)" }
    }
}

Write-Host ""
python -c "import uvicorn, fastapi, torch; print('OK: uvicorn', uvicorn.__version__, '| torch', torch.__version__, '| CUDA', torch.cuda.is_available())"
if ($LASTEXITCODE -eq 0) { Write-Host "Xong. Bay gio nhap dup run.bat de chay server." }
