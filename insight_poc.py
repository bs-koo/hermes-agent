# -*- coding: utf-8 -*-
"""인사이트 PoC — nginx access.log를 CloudWatch Logs Insights로 집계."""
import time, boto3

logs = boto3.client("logs")
LG = "/aws/elasticbeanstalk/dataviz-prod/var/log/nginx/access.log"
now = int(time.time())
start = now - 8 * 24 * 3600  # 최근 8일

PARSE = r"""parse @message '* - - [*] "* * *" * * "*" "*" "*"' as ip, ts, method, url, proto, status, bytes, referer, ua, xff"""

def run(title, tail):
    q = PARSE + "\n| " + tail
    qid = logs.start_query(logGroupName=LG, startTime=start, endTime=now,
                           queryString=q, limit=10000)["queryId"]
    for _ in range(40):
        r = logs.get_query_results(queryId=qid)
        if r["status"] == "Complete":
            break
        time.sleep(1)
    rows = r.get("results", [])
    print(f"\n=== {title}  (rows={len(rows)}, scanned={r.get('statistics',{}).get('recordsScanned','?')}) ===")
    for row in rows[:20]:
        d = {f["field"]: f["value"] for f in row}
        print("  " + " | ".join(f"{k}={v}" for k, v in d.items()))

run("① 트래픽 Top URL (요청수)",
    "filter ispresent(url) | stats count(*) as hits by method, url | sort hits desc | limit 15")
run("② HTTP 상태코드 분포",
    "filter ispresent(status) | stats count(*) as hits by status | sort hits desc | limit 15")
run("③ Top 실제 클라이언트(XFF) ",
    "filter ispresent(xff) | stats count(*) as hits by xff | sort hits desc | limit 10")
run("④ 4xx/5xx 에러 URL Top",
    "filter status >= 400 | stats count(*) as hits by status, url | sort hits desc | limit 15")
run("⑤ 일자별 요청량",
    "filter ispresent(url) | stats count(*) as hits by bin(1d) as day | sort day desc | limit 10")
