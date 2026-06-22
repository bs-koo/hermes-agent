$ErrorActionPreference = "Stop"

# 1) 알람 감시 — 10분마다, ALARM 발생 시에만 Google Chat 알림
$a1 = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "D:\SQ\hermes_agent\check_hidden.vbs"
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "Hermes-AlarmCheck" -Action $a1 -Trigger $t1 `
    -Description "dataviz-prod CloudWatch alarm watch (alert only on ALARM)" -Force | Out-Null
Write-Host "OK Created: Hermes-AlarmCheck (every 10 min)"

# 2) 일일 다이제스트 — 매일 09:00, 항상 전송(정상 확인)
$a2 = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "D:\SQ\hermes_agent\digest_hidden.vbs"
$t2 = New-ScheduledTaskTrigger -Daily -At "10:00AM"
Register-ScheduledTask -TaskName "Hermes-AlarmDigest" -Action $a2 -Trigger $t2 `
    -Description "dataviz-prod daily alarm digest" -Force | Out-Null
Write-Host "OK Created: Hermes-AlarmDigest (daily 09:00)"

Write-Host ""
Write-Host "=== Registered Hermes tasks ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like 'Hermes*' } |
    Select-Object TaskName, State | Format-Table -AutoSize
