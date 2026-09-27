@echo off
rem Start the local trip-planner agent server and open the planner (close this window to stop it)
cd /d "%~dp0.."
start "" cmd /c "timeout /t 2 >nul & start http://localhost:8787/"
python agent_server.py
pause
