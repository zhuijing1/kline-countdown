@echo off
setlocal
cd /d "%~dp0"

set "PYW="
where pythonw >nul 2>nul && set "PYW=pythonw"

if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PYW if exist "%ProgramFiles%\Python313\pythonw.exe" set "PYW=%ProgramFiles%\Python313\pythonw.exe"
if not defined PYW if exist "%ProgramFiles%\Python312\pythonw.exe" set "PYW=%ProgramFiles%\Python312\pythonw.exe"
if not defined PYW if exist "%ProgramFiles%\Python311\pythonw.exe" set "PYW=%ProgramFiles%\Python311\pythonw.exe"

if not defined PYW goto nopython

start "" "%PYW%" "%~dp0main.py"
exit /b 0

:nopython
echo.
echo   Python not found. Please install Python 3.10 or newer:
echo     https://www.python.org/downloads/windows/
echo   During setup, check "Add Python to PATH".
echo.
pause
exit /b 1
