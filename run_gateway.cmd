@echo off
REM Hermes Google Chat 게이트웨이 — UTF-8 모드(이모지/한글 cron 출력 정상화)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"C:\Users\SQI\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe" gateway run
