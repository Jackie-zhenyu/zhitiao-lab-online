@echo off
setlocal
if not exist "%~dp0..\.venv\Scripts\python.exe" (
    echo ERROR: Project .venv is missing or incomplete. Run scripts\install.cmd first.
    exit /b 1
)
"%~dp0..\.venv\Scripts\python.exe" "%~dp0manage.py" start %*
exit /b %errorlevel%
