#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dataviz-prod CloudWatch 알람 상태를 조회해 Google Chat 스페이스로 요약 전송.
컨테이너(cloudwatch-mcp) 안에서 실행: boto3(AWS) + urllib(webhook) 모두 사용 가능.
"""
import os, json, urllib.request, datetime
import boto3
from _env import load_env

load_env()
REGION = "ap-northeast-2"
WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")

def fetch_alarms():
    cw = boto3.client("cloudwatch", region_name=REGION)
    alarms = []
    for page in cw.get_paginator("describe_alarms").paginate():
        alarms += page.get("MetricAlarms", [])
        alarms += page.get("CompositeAlarms", [])
    return alarms

def build_message(alarms):
    kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    ts = kst.strftime("%Y-%m-%d %H:%M KST")
    by = {"ALARM": [], "OK": [], "INSUFFICIENT_DATA": []}
    for a in alarms:
        by.setdefault(a["StateValue"], []).append(a)
    n_alarm = len(by["ALARM"])
    lines = []
    if n_alarm > 0:
        lines.append(f"🚨 *[알람 발생] dataviz-prod* · {ts}")
        lines.append(f"현재 *ALARM* 상태 {n_alarm}건 / 전체 {len(alarms)}건")
        for a in by["ALARM"]:
            reason = (a.get("StateReason") or "").strip()
            lines.append(f"• *{a['AlarmName']}* → ALARM")
            if reason:
                lines.append(f"    {reason[:200]}")
    else:
        lines.append(f"✅ *[정상] dataviz-prod 알람 점검* · {ts}")
        lines.append(f"활성(ALARM) 알람 없음 — 전체 {len(alarms)}건 정상권")
        ok = len(by["OK"]); insf = len(by["INSUFFICIENT_DATA"])
        lines.append(f"(OK {ok} / 데이터부족 {insf})")
    return "\n".join(lines)

def post(text):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK, data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status

if __name__ == "__main__":
    import os
    alarms = fetch_alarms()
    active = [a for a in alarms if a["StateValue"] == "ALARM"]
    # ALERT_ONLY=1 이면 ALARM 발생 시에만 전송(평소 조용). 미설정 시 항상 전송(다이제스트).
    if os.environ.get("ALERT_ONLY") == "1" and not active:
        print("alert-only: 활성 알람 없음 → 전송 생략 | alarms:", len(alarms))
    else:
        status = post(build_message(alarms))
        print("posted http", status, "| alarms:", len(alarms), "| active:", len(active))
