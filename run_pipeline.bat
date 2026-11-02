@echo off
REM Windows: double-click to build the cluster and run the whole pipeline + ML (run_pipeline.ps1)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_pipeline.ps1" %*
pause
