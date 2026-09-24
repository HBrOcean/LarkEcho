@echo off
title kgm-decryptor build tool
echo ============================================
echo    Build kgm-decryptor (Windows)
echo ============================================
echo.

echo [0/4] Checking Python ...
python --version >nul 2>&1
if errorlevel 1 goto err_python

echo [1/4] Installing dependencies (pyinstaller, pywebview, mutagen) ...
python -m pip install --upgrade pyinstaller pywebview mutagen
if errorlevel 1 goto err_dep

echo.
echo [2/4] Building CLI (kgm2all) ...
python tools\build.py cli
if errorlevel 1 goto err_pack

echo.
echo [3/4] Building GUI (kgm2gui) ...
python tools\build.py gui
if errorlevel 1 goto err_pack

echo.
echo [4/4] Done !
echo.
echo    Output files : dist\kgm2all.exe
echo                   dist\kgm2gui.exe
echo.
pause
exit /b 0

:err_python
echo.
echo [ERROR] Python not found.
echo         Install Python from python.org and CHECK "Add Python to PATH",
echo         then run this script again.
echo.
pause
exit /b 1

:err_dep
echo.
echo [ERROR] Failed to install dependencies. Check your network and retry.
echo.
pause
exit /b 1

:err_pack
echo.
echo [ERROR] Build failed. See the messages above.
echo.
pause
exit /b 1
