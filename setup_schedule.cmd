@echo off
REM Hermes 모니터링 스케줄 등록 — 작업 스케줄러에 작업 2개 생성
schtasks /Create /TN "Hermes-AlarmCheck" /TR "wscript.exe D:\SQ\hermes_agent\check_hidden.vbs" /SC MINUTE /MO 10 /F
schtasks /Create /TN "Hermes-AlarmDigest" /TR "wscript.exe D:\SQ\hermes_agent\digest_hidden.vbs" /SC DAILY /ST 09:00 /F
echo.
echo === 등록된 작업 ===
schtasks /Query /TN "Hermes-AlarmCheck" /FO LIST
schtasks /Query /TN "Hermes-AlarmDigest" /FO LIST
