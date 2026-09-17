@echo off
REM Double-click for real use: the gateway with your real provider keys.
REM No demo traffic is generated, so only what your apps send costs quota.
cd /d "%~dp0"

echo Starting the gateway (real providers, keys from .env)...
start "llm-gateway" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --port 8080"

ping -n 6 127.0.0.1 >nul
start "" http://localhost:8080/dashboard

echo.
echo   Dashboard : http://localhost:8080/dashboard
echo   Nocturne  : start it after this - it routes through here
echo   To stop   : close the window titled llm-gateway
echo.
ping -n 4 127.0.0.1 >nul
