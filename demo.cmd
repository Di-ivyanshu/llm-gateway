@echo off
REM Double-click to run the demo: gateway + traffic + dashboard.
REM Fake providers, so no API keys are used and no quota is spent.
cd /d "%~dp0"

set GATEWAY_FAKE_PROVIDERS=1
set GATEWAY_FAKE_LATENCY_MS=120
set BREAKER_COOLDOWN_S=8
set BREAKER_MIN_SAMPLES=3

echo Starting the gateway (fake providers)...
start "llm-gateway" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --port 8080"

echo Waiting for it to come up...
ping -n 6 127.0.0.1 >nul

echo Starting demo traffic...
start "demo-traffic" cmd /k ".venv\Scripts\python.exe -m bench.traffic --rate 3"

start "" http://localhost:8080/dashboard

echo.
echo   Dashboard : http://localhost:8080/dashboard
echo   To stop   : close the windows titled llm-gateway and demo-traffic
echo.
ping -n 4 127.0.0.1 >nul
