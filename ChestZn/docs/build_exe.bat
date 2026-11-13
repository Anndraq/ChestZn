@echo off
cd /d "%~dp0"
echo ====================================================
echo             Building Dotmark.exe
echo ====================================================
echo.

set "PY="

REM 1. Check virtual environments
if exist "env\Scripts\python.exe" set "PY=env\Scripts\python.exe"
if "%PY%"=="" if exist "..\env\Scripts\python.exe" set "PY=..\env\Scripts\python.exe"
if "%PY%"=="" if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if "%PY%"=="" if exist "..\venv\Scripts\python.exe" set "PY=..\venv\Scripts\python.exe"
if "%PY%"=="" if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

REM 2. Check py launcher for Python 3.12 / 3.11 / 3.10 with PySide6
if "%PY%"=="" (
    py -3.12 -c "import PySide6" >nul 2>&1 && set "PY=py -3.12"
)
if "%PY%"=="" (
    py -3.11 -c "import PySide6" >nul 2>&1 && set "PY=py -3.11"
)
if "%PY%"=="" (
    py -3.10 -c "import PySide6" >nul 2>&1 && set "PY=py -3.10"
)

REM 3. Check default python with PySide6
if "%PY%"=="" (
    python -c "import PySide6" >nul 2>&1 && set "PY=python"
)

REM 4. Fallback
if "%PY%"=="" set "PY=python"

echo [1/3] Using Python: %PY%
%PY% --version
echo.

echo [2/3] Checking dependencies (PySide6, pywin32, pyserial, pyodbc, pyinstaller)...
%PY% -m pip install PySide6 pywin32 pyserial pyodbc pyinstaller
echo.

%PY% -c "import PySide6; print('  [OK] PySide6 found:', PySide6.__file__)"
if errorlevel 1 (
    echo.
    echo [ERROR] PySide6 is not installed in %PY%!
    echo If you use Visual Studio, open Terminal in Visual Studio and run:
    echo     python -m PyInstaller --clean ChestZn.spec
    pause
    exit /b 1
)

echo.
echo [3/3] Compiling Dotmark.exe...
if exist "ChestZn.spec" (
    %PY% -m PyInstaller --clean --noconfirm ChestZn.spec
) else (
    %PY% -m PyInstaller --clean --noconfirm --onefile --windowed --name Dotmark --collect-all PySide6 --hidden-import win32print --hidden-import win32api --hidden-import serial --hidden-import pyodbc main.py
)
echo.
if exist "dist\Dotmark.exe" (
    echo ====================================================
    echo   SUCCESS! File created: dist\Dotmark.exe
    echo ====================================================
) else (
    echo [ERROR] dist\Dotmark.exe was not created! Check errors above.
)
pause
