@echo off
REM Routine Runner launcher (Windows). Forwards all arguments to run.py.
REM   run.bat serve --reload
REM   run.bat seed
setlocal
cd /d "%~dp0"

REM Activate a local virtualenv if present.
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

if "%PYTHON%"=="" set "PYTHON=python"
"%PYTHON%" run.py %*
endlocal
