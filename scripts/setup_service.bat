@echo off
REM Install the OceanPulse ingestion daemon as a Windows service via NSSM.
REM Must be run elevated: right-click this file and choose
REM "Run as administrator".

SETLOCAL EnableDelayedExpansion
cd /d "%~dp0.."
set "PROJECT_ROOT=%CD%"

net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: administrator rights are required to install a service.
    echo Right-click setup_service.bat and choose "Run as administrator".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: no virtual environment found.
    echo Run run.bat once first - the service needs the environment it creates.
    pause
    exit /b 1
)

set "NSSM=%PROJECT_ROOT%\tools\nssm.exe"
if not exist "%NSSM%" (
    echo Downloading NSSM...
    if not exist "%PROJECT_ROOT%\tools" mkdir "%PROJECT_ROOT%\tools"
    powershell -NoProfile -Command ^
      "try { Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%TEMP%\nssm.zip' -UseBasicParsing; Expand-Archive -Path '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force; Copy-Item '%TEMP%\nssm\nssm-2.24\win64\nssm.exe' '%NSSM%' -Force } catch { exit 1 }"
    if not exist "%NSSM%" (
        echo.
        echo ERROR: could not download NSSM automatically - usually a proxy or firewall.
        echo Download nssm-2.24.zip from https://nssm.cc/download, then copy
        echo win64\nssm.exe into: %PROJECT_ROOT%\tools\
        pause
        exit /b 1
    )
)

"%NSSM%" install OceanPulse "%PROJECT_ROOT%\.venv\Scripts\python.exe" "%PROJECT_ROOT%\run.py" daemon
"%NSSM%" set OceanPulse AppDirectory "%PROJECT_ROOT%"
"%NSSM%" set OceanPulse DisplayName "OceanPulse Ingestion Daemon"
"%NSSM%" set OceanPulse Description "Collects marine observations into the local OceanPulse database."
"%NSSM%" set OceanPulse Start SERVICE_AUTO_START
"%NSSM%" set OceanPulse AppStdout "%PROJECT_ROOT%\logs\service.log"
"%NSSM%" set OceanPulse AppStderr "%PROJECT_ROOT%\logs\service_error.log"
"%NSSM%" start OceanPulse

echo.
echo Installed. Check it in services.msc under "OceanPulse Ingestion Daemon".
echo.
echo Start the dashboard with:  run.bat --no-daemon
echo so you do not end up with two pollers competing.
pause
ENDLOCAL
