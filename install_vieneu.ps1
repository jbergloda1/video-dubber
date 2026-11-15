# Cai VieNeu-TTS (giong Viet tu nhien, chay local tren GPU) vao moi truong RIENG, mac dinh o F:\VieNeu-TTS.
# Moi truong rieng vi VieNeu can torch 2.8 + transformers 4.57.6 - cai chung vao Python cua app se lam hong Whisper.
# Chay:  powershell -ExecutionPolicy Bypass -File install_vieneu.ps1        (hoac them: -Dir E:\VieNeu-TTS)
# (File nay chi dung chu khong dau: PowerShell 5.1 doc sai file UTF-8 khong co BOM.)
param([string]$Dir = "F:\VieNeu-TTS")
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force $Dir | Out-Null
$env:PIP_CACHE_DIR = Join-Path $Dir "pip-cache"     # file tai ve nam o o nay, khong chiem o C
$env:HF_HOME = Join-Path $Dir "hf-cache"            # model VieNeu (~1 GB) cung vay
$envDir = Join-Path $Dir "env"
$py = Join-Path $envDir "Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "== Tao moi truong Python 3.11 o $envDir"
    py -3.11 -m venv $envDir
}
Write-Host "== Cap nhat pip"
& $py -m pip install -q --upgrade pip
Write-Host "== Cai torch 2.8.0 (CUDA 12.8) - khoang 3 GB, vai phut"
& $py -m pip install -q torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "Cai torch loi" }
Write-Host "== Cai VieNeu-TTS"
& $py -m pip install -q "transformers==4.57.6" vieneu
if ($LASTEXITCODE -ne 0) { throw "Cai vieneu loi" }
Write-Host "== Kiem tra"
$check = Join-Path $Dir "check.py"
Set-Content -Path $check -Encoding ascii -Value "import torch, vieneu`nprint('torch', torch.__version__, 'GPU:', torch.cuda.is_available())"
& $py $check
Write-Host ""
Write-Host "Xong. App dung Python nay: $py  (doi duong dan trong Cai dat -> VieNeu-TTS neu cai cho khac)"
