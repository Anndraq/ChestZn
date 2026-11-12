@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Установка Dotmark · Система маркировки

echo ====================================================================
echo        МАСТЕР УСТАНОВКИ DOTMARK · СИСТЕМА МАРКИРОВКИ (v2.2)
echo ====================================================================
echo.

set "PY="

REM 1. Проверяем локальное виртуальное окружение проекта
if exist "env\Scripts\python.exe" set "PY=env\Scripts\python.exe"
if "%PY%"=="" if exist "..\env\Scripts\python.exe" set "PY=..\env\Scripts\python.exe"
if "%PY%"=="" if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if "%PY%"=="" if exist "..\venv\Scripts\python.exe" set "PY=..\venv\Scripts\python.exe"
if "%PY%"=="" if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

REM 2. Проверяем системный Python через py launcher или PATH
if "%PY%"=="" (
    py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if "%PY%"=="" (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)

REM 3. Если Python не найден на ПК, но уже есть собранный dist\Dotmark.exe — ставим напрямую!
if "%PY%"=="" (
    if exist "dist\Dotmark.exe" (
        echo [!] Python не найден, выполняем прямую установку готового Dotmark.exe...
        set "TARGET_DIR=%LOCALAPPDATA%\Programs\Dotmark"
        if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
        copy /Y "dist\Dotmark.exe" "%TARGET_DIR%\Dotmark.exe" >nul
        if exist "app.ico" copy /Y "app.ico" "%TARGET_DIR%\app.ico" >nul
        if exist "%APPDATA%\ChestZn" (
            REM ok
        ) else (
            mkdir "%APPDATA%\ChestZn"
            mkdir "%APPDATA%\ChestZn\logs"
            mkdir "%APPDATA%\ChestZn\backups"
        )
        powershell -NoProfile -NonInteractive -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Dotmark · Система маркировки.lnk'); $s.TargetPath = '%LOCALAPPDATA%\Programs\Dotmark\Dotmark.exe'; $s.WorkingDirectory = '%LOCALAPPDATA%\Programs\Dotmark'; $s.IconLocation = '%LOCALAPPDATA%\Programs\Dotmark\app.ico'; $s.Save()"
        echo.
        echo [OK] Dotmark установлен в %LOCALAPPDATA%\Programs\Dotmark
        echo [OK] Ярлык создан на Рабочем столе!
        start "" "%LOCALAPPDATA%\Programs\Dotmark\Dotmark.exe"
        pause
        exit /b 0
    ) else (
        echo [!] Python не обнаружен на компьютере. Попытка автоматической установки Python 3.11 через winget...
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        set "PY=python"
    )
)

echo [1/2] Используется интерпретатор: %PY%
%PY% -m pip install --upgrade pip >nul 2>&1
echo [2/2] Запуск мастера установки Dotmark...
%PY% install_dotmark.py %*

if errorlevel 1 (
    echo.
    echo [!] Возникла ошибка при выполнении установщика.
    pause
)
