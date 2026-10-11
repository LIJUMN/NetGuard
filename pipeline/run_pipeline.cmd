@echo off
rem Wrapper used by the scheduled tasks.
rem Kept as a .cmd because schtasks mangles nested PowerShell quoting
rem (a "-Command ..." string inside /TR breaks argument parsing).
rem %~dp0 is this script's folder; the repo root is one level up.
setlocal
set LOGDIR=%~dp0..\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden ^
  -File "%~dp0pipeline.ps1" %* > "%LOGDIR%\pipeline_run.log" 2>&1

echo %date% %time% pipeline exit=%ERRORLEVEL%>> "%LOGDIR%\tasks.log"
endlocal & exit /b %ERRORLEVEL%