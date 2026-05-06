@echo off
setlocal

:: Switch to UTF-8
chcp 65001 >nul

REM Windows Build Script
REM Move to project root
cd /d "%~dp0.."

echo ============================================================
echo Starting Build Process (Windows EXE)...
echo Current Dir: %cd%
echo ============================================================

REM Install dependencies
echo ^>^>^> Installing dependencies...
python -m pip install pyinstaller pycryptodome Pillow pymupdf --quiet

REM Clean old builds
echo ^>^>^> Cleaning old build files...
if exist build rd /s /q build
if exist dist rd /s /q dist

REM Set PYTHONPATH to include src
set "PYTHONPATH=%PYTHONPATH%;%cd%\src"

REM Packaging
echo ^>^>^> Packaging tool 1: Extract tool (Console)...
python -m PyInstaller --onefile --console --name "一键还原工具" src/extract_client.py

echo ^>^>^> Packaging tool 2: Viewer tool (GUI)...
python -m PyInstaller --onefile --noconsole --name "安全预览工具" src/viewer_client.py

echo.
echo ============================================================
echo Build Complete! Check the 'dist' folder for:
echo "一键还原工具.exe" and "安全预览工具.exe"
echo ============================================================
pause

