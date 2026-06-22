@echo off
REM dataviz-prod $0 가동률 핑 — 5분마다, 다운/복구 전환 시에만 Google Chat 알림 (AWS 비용 0)
"C:\Users\SQI\AppData\Local\Programs\Python\Python312\python.exe" "D:\SQ\hermes_agent\uptime_ping.py"
