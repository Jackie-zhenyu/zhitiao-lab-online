@echo off
setlocal
rem Optional first argument: path to a Python 3.12 executable. No policy changes.
if not "%~1"=="" goto explicit_python
if exist "%~dp0..\.venv\Scripts\python.exe" goto existing_venv
where py >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python launcher was not found. Run install.cmd "C:\path\to\Python312\python.exe".
    exit /b 1
)
py -3.12 "%~dp0manage.py" install
if errorlevel 1 (
    echo ERROR: Installation failed. Python 3.12 must be available; read the error above.
    exit /b 1
)
exit /b 0

:explicit_python
"%~1" "%~dp0manage.py" install
exit /b %errorlevel%

:existing_venv
"%~dp0..\.venv\Scripts\python.exe" "%~dp0manage.py" install
exit /b %errorlevel%
