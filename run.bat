@echo off
REM OceanPulse launcher for Windows.
REM Creates a virtual environment, installs dependencies, then starts the app.
REM All arguments are passed straight through to run.py.

SETLOCAL EnableDelayedExpansion
cd /d "%~dp0"

REM UTF-8 console. Without this, a place name or a project path containing a
REM non-ASCII character can raise UnicodeEncodeError inside logging on a
REM default cp1252 console and take the process down.
chcp 65001 >nul 2>&1

set "PYTHON="
for %%P in (py python python3) do (
    if not defined PYTHON (
        %%P -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 set "PYTHON=%%P"
    )
)

if not defined PYTHON (
    echo ERROR: Python 3.10 or newer is required but was not found on your PATH.
    echo.
    echo Download it from https://www.python.org/downloads/ and make sure you
    echo tick "Add Python to PATH" on the first screen of the installer.
    echo That single checkbox is the most common cause of setup failure.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment ^(one time only^)...
    %PYTHON% -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: could not create a virtual environment.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

REM Reinstall only when requirements.txt has actually changed, compared by
REM content hash so a git checkout does not trigger a pointless reinstall.
REM run.sh uses the identical rule, so both platforms behave the same.
set "STAMP=.venv\.requirements-stamp"
set "WANT="
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile requirements.txt SHA256 2^>nul') do (
    if not defined WANT set "WANT=%%H"
)
set "WANT=%WANT: =%"
set "HAVE="
if exist "%STAMP%" set /p HAVE=<"%STAMP%"

if not "%WANT%"=="%HAVE%" (
    echo Installing dependencies ^(this takes about a minute the first time^)...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: dependency installation failed.
        pause
        exit /b 1
    )
    >"%STAMP%" echo|set /p="%WANT%"
)

python run.py %*
ENDLOCAL
