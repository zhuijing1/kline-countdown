@echo off
rem 打包成单文件 exe：dist\K线倒计时.exe
rem 真正的构建逻辑在 build.py（那里用 UTF-8，中文名不会乱码）
setlocal
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%ProgramFiles%\Python313\python.exe" set "PY=%ProgramFiles%\Python313\python.exe"

if not defined PY (
  echo Python not found. Install Python 3.10+ first.
  pause
  exit /b 1
)

"%PY%" "%~dp0build.py"
if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Output: dist\
pause
endlocal
