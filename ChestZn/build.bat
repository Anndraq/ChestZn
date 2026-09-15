@echo off
chcp 65001 >nul
echo === Сборка ChestZn.exe ===

if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

pyinstaller ChestZn.spec --noconfirm

echo.
echo Готово: dist\ChestZn.exe
pause