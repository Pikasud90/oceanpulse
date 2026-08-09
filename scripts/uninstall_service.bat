@echo off
REM Remove the OceanPulse Windows service. Run as administrator.
SETLOCAL
cd /d "%~dp0.."

net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: administrator rights are required.
    pause
    exit /b 1
)

set "NSSM=%CD%\tools\nssm.exe"
if not exist "%NSSM%" (
    echo NSSM not found at %NSSM% - was the service ever installed?
    pause
    exit /b 1
)

"%NSSM%" stop OceanPulse
"%NSSM%" remove OceanPulse confirm
echo Service removed.
pause
ENDLOCAL
