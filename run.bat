@echo off
cd /d "%~dp0"

rem Server da chay roi thi chi mo trinh duyet
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul
if %errorlevel%==0 (
  echo Server da chay san tai http://127.0.0.1:8765 - mo trinh duyet.
  start "" http://127.0.0.1:8765
  timeout /t 3 >nul
  exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
  echo KHONG TIM THAY python. Cai Python 3.11 va tick "Add python.exe to PATH". Xem README.md.
  pause
  exit /b 1
)

python -c "import uvicorn, fastapi" 2>nul
if errorlevel 1 (
  echo Chua cai thu vien. Chay: python -m pip install -r requirements.txt   ^(xem README.md^)
  pause
  exit /b 1
)

echo Video Dubber dang chay tai http://127.0.0.1:8765
python -c "from dubber import access; ip = access.tailscale_ip(); print('Tren dien thoai (Tailscale): http://' + ip + ':8765' if ip else '')" 2>nul
echo GIU CUA SO NAY MO. Dong cua so = tat server. Ctrl+C de tat.
start "" http://127.0.0.1:8765
rem DUBBER_SUPERVISED: the app may restart itself (button "Khoi dong lai server") by exiting with code 3
set DUBBER_SUPERVISED=1
:serve
python -m uvicorn dubber.app:app --host 0.0.0.0 --port 8765
if %errorlevel%==3 (
  echo.
  echo === Khoi dong lai server de nap code moi... ===
  goto serve
)
echo.
echo Server da dung. Doc dong loi phia tren neu co.
pause
