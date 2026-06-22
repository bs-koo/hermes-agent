# -*- coding: utf-8 -*-
"""EB 플랫폼/런타임/앱 로그 경로 힌트 탐지 — eb-engine.log 분석."""
import time, boto3, re

logs = boto3.client("logs")
LG = "/aws/elasticbeanstalk/dataviz-prod/var/log/eb-engine.log"

st = logs.describe_log_streams(logGroupName=LG, orderBy="LastEventTime",
                               descending=True, limit=1)["logStreams"][0]
print("최신 스트림:", st["logStreamName"])

# 최근 배포 로그에서 플랫폼/런타임/경로 단서 수집
ev = logs.get_log_events(logGroupName=LG, logStreamName=st["logStreamName"],
                         limit=400, startFromHead=False)["events"]
hits = []
pat = re.compile(r"(amazon linux|al2023|al2\b|platform|solution stack|corretto|java|node|"
                 r"python|docker|tomcat|nginx|/var/log/[^\s]+\.log|web\.stdout|application)", re.I)
seen = set()
for e in ev:
    m = e["message"].strip()
    for mm in pat.findall(m):
        key = mm.lower()
        if key not in seen:
            seen.add(key)
            hits.append((key, m[:160]))
for k, line in hits[:25]:
    print(f"[{k}] {line}")
