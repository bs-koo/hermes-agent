# -*- coding: utf-8 -*-
"""nginx 로그 형식 파악용 샘플러 v2 — 최신 스트림 시각 확인 후 샘플."""
import time, datetime, boto3

logs = boto3.client("logs")

def kst(ms):
    return (datetime.datetime.utcfromtimestamp(ms/1000) + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")

def sample(lg, n, title):
    print(f"=== {title} ===")
    print(f"  {lg}")
    try:
        st = logs.describe_log_streams(logGroupName=lg, orderBy="LastEventTime",
                                       descending=True, limit=1).get("logStreams", [])
        if not st:
            print("  (스트림 없음)\n"); return
        last = st[0].get("lastEventTimestamp")
        print(f"  최신 이벤트: {kst(last) if last else '없음'}  스트림={st[0]['logStreamName']}")
        if last:
            r = logs.get_log_events(logGroupName=lg, logStreamName=st[0]["logStreamName"],
                                    limit=n, startFromHead=False)
            for e in r.get("events", []):
                print("  | " + e["message"].rstrip()[:300])
    except Exception as ex:
        print("  ERROR:", ex)
    print()

sample("/aws/elasticbeanstalk/dataviz-prod/var/log/nginx/access.log", 6, "ACCESS")
sample("/aws/elasticbeanstalk/dataviz-prod/var/log/nginx/error.log", 4, "ERROR")
sample("/aws/rds/instance/gseed-db/postgresql", 4, "RDS POSTGRES")
