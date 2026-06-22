# -*- coding: utf-8 -*-
"""트래픽/품질/시간대 집계 + RDS 성능 집계 함수.

weekly_insight.py 의 즉시실행 영역(L188-242)·rds_perf(L75-112)을 함수로 추출·복제한 것이다.
weekly_insight.py 는 모듈 최상단에서 즉시 실행되어 import 가 불가능하므로(BR-5: 원본 불변)
정책·쿼리·파싱·메트릭 스펙을 여기에 동일하게 옮겨 둔다.

# weekly_insight.py와 동기화 유지
#   - PARSE / SCANNER_RE / HEALTH_RE / UA 제외값
#   - Top EP / 상태코드 버킷 / 에러 URL / 사용자(XFF) 집계 로직
#   - rds_perf 의 10개 메트릭(metric, stat) 스펙
weekly_insight.py 정책 변경 시 이 파일도 함께 갱신해야 한다(정책 2곳 존재)."""
import re
import sys
import time
import datetime


# ── 정책 상수(weekly_insight.py와 동기화 유지) ───────────────────────
PARSE = (r"""parse @message '* - - [*] "* * *" * * "*" "*" "*"' """
         r"""as ip, ts, method, url, proto, status, bytes, referer, ua, xff""")

# 가동률 핑(uptime_ping.py)의 자기 트래픽 제외용 User-Agent 토큰
UA_EXCLUDE = "dataviz-uptime-ping"

# 스캐너/봇 탐침(보안 노이즈): .git, 민감 actuator, wp-, phpmyadmin 등
SCANNER_RE = re.compile(r"\.git|/\.env|wp-admin|wp-login|phpmyadmin|/vendor/|\.aws/|"
                        r"/actuator/(env|heapdump|configprops|mappings|beans|threaddump|loggers)", re.I)
# 인프라 헬스체크(정상 트래픽이지만 '사용자 활동'은 아님 → 집계에서 분리)
HEALTH_RE = re.compile(r"/actuator/health|/actuator/info|/health$|/ping$", re.I)


def strip_qs(u):
    """URL 에서 쿼리스트링을 제거한다."""
    return u.split("?", 1)[0]


def query_logs_insights(logs, query_tail, t0, t1, limit=1000):
    """Logs Insights 쿼리 실행 후 [{field: value}] 리스트 반환.
    PARSE + UA 제외 필터를 앞에 붙이고 query_tail 을 이어 붙인다.
    logs 는 boto3 logs 클라이언트(호출측 주입). t0/t1 은 epoch 초.
    LG(로그그룹)는 config 에서 가져온다(주입 logs 와 분리해 SSOT 유지)."""
    from dashboard import config
    q = PARSE + "\n| filter ua not like /%s/\n| " % UA_EXCLUDE + query_tail
    qid = logs.start_query(logGroupName=config.LG, startTime=t0, endTime=t1,
                           queryString=q, limit=limit)["queryId"]
    r = {"status": "Running", "results": []}
    for _ in range(60):
        r = logs.get_query_results(queryId=qid)
        if r["status"] == "Complete":
            break
        time.sleep(1)
    if r.get("status") != "Complete":
        sys.stderr.write(
            f"[aggregations] Logs Insights 미완료(status={r.get('status')})\n")
        return []
    out = []
    for row in r.get("results", []):
        out.append({f["field"]: f["value"] for f in row})
    return out


def aggregate_traffic(rows):
    """query_logs_insights 로 모은 원시 행들을 트래픽/품질/사용자 집계로 변환.

    설계상 단일 함수가 여러 쿼리 결과를 받아 합치기 어려우므로,
    여기서는 weekly_insight.py 와 동일하게 '필요한 4개 쿼리를 호출측이 수행'한 뒤
    각 결과 리스트를 dict 로 모아 전달받는다.
      rows = {
        "count":  [{c}],                              # 총 요청수
        "urls":   [{method, url, hits}],              # Top EP 원시
        "status": [{status, hits}],                   # 상태코드 분포
        "err":    [{status, url, hits}],              # 에러 URL
        "users":  [{xff, hits}],                      # 사용자(XFF)
      }
    반환: {top_ep, buckets, top_err, n_users, total, scanner_hits, health_hits}"""
    # 총 요청수
    count_rows = rows.get("count", [])
    total = int(count_rows[0]["c"]) if count_rows else 0

    # Top 엔드포인트(쿼리스트링 제거 후 재집계 / 스캐너·헬스 분리)
    ep = {}
    scanner_hits = 0
    health_hits = 0
    for r in rows.get("urls", []):
        path = strip_qs(r["url"])
        h = int(r["hits"])
        if HEALTH_RE.search(path):
            health_hits += h
            continue
        if SCANNER_RE.search(path):
            scanner_hits += h
            continue
        key = f"{r['method']} {path}"
        ep[key] = ep.get(key, 0) + h
    top_ep = sorted(ep.items(), key=lambda x: -x[1])[:8]

    # 상태코드 분포 → 2xx/3xx/4xx/5xx
    buckets = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "기타": 0}
    for r in rows.get("status", []):
        try:
            s = int(r["status"])
        except ValueError:
            buckets["기타"] += int(r["hits"])
            continue
        k = f"{s // 100}xx"
        buckets[k] = buckets.get(k, 0) + int(r["hits"])

    # 에러 URL Top(노이즈 제외)
    err = {}
    for r in rows.get("err", []):
        path = strip_qs(r["url"])
        if SCANNER_RE.search(path) or HEALTH_RE.search(path):
            continue
        key = f"{r['status']} {path}"
        err[key] = err.get(key, 0) + int(r["hits"])
    top_err = sorted(err.items(), key=lambda x: -x[1])[:5]

    # 사용자(XFF 실IP)
    user_rows = rows.get("users", [])
    n_users = len(user_rows)

    return {
        "top_ep": [{"key": k, "hits": h} for k, h in top_ep],
        "buckets": buckets,
        "top_err": [{"key": k, "hits": h} for k, h in top_err],
        "n_users": n_users,
        "total": total,
        "scanner_hits": scanner_hits,
        "health_hits": health_hits,
    }


def aggregate_traffic_hourly(logs, t0, t1):
    """시간대별 요청 추세를 [(hour_epoch, count)] 로 반환한다.
    Logs Insights 의 stats count(*) by bin(1h) 를 사용한다.
    bin 결과의 시각 필드는 'YYYY-MM-DD HH:MM:SS.000'(UTC) 형태이므로 epoch 로 변환한다."""
    rows = query_logs_insights(
        logs,
        "filter ispresent(url) | stats count(*) as c by bin(1h) as t | sort t asc",
        t0, t1)
    out = []
    for r in rows:
        ts = r.get("t")
        c = r.get("c")
        if ts is None or c is None:
            continue
        hour_epoch = _parse_insights_time(ts)
        if hour_epoch is None:
            continue
        try:
            out.append((hour_epoch, int(c)))
        except (TypeError, ValueError):
            continue
    return out


def _parse_insights_time(ts):
    """Logs Insights bin() 시각 문자열을 epoch(int)로 변환. 실패 시 None.
    예: '2026-06-22 03:00:00.000' (UTC). 소수점/타임존 변형을 관용 처리한다."""
    s = ts.strip()
    # 소수점 이하 제거
    if "." in s:
        s = s.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def rds_perf(cw, t0, t1, dbid="gseed-db"):
    """RDS 성능/부하 메트릭(FR-13 10항목)을 집계해 dict 로 반환한다.
    cw 는 boto3 cloudwatch 클라이언트(호출측 주입). t0/t1 은 epoch 초.
    weekly_insight.rds_perf 와 동일한 (metric, stat) 스펙·집계 규칙을 따른다.
    실패 시 빈 dict 가 아니라 모든 키 None 으로 채워 반환한다(패널 렌더 일관성)."""
    dims = [{"Name": "DBInstanceIdentifier", "Value": dbid}]
    wanted = [
        ("CPUUtilization", "Average"), ("CPUUtilization", "Maximum"),
        ("DatabaseConnections", "Average"), ("DatabaseConnections", "Maximum"),
        ("ReadLatency", "Average"), ("WriteLatency", "Average"),
        ("DBLoad", "Average"), ("DBLoad", "Maximum"),
        ("DiskQueueDepth", "Maximum"), ("FreeStorageSpace", "Minimum"),
    ]
    # FR-13 10항목 키 매핑((metric, stat) → 출력 키)
    keymap = {
        ("CPUUtilization", "Average"): "cpu_avg",
        ("CPUUtilization", "Maximum"): "cpu_max",
        ("DatabaseConnections", "Average"): "conn_avg",
        ("DatabaseConnections", "Maximum"): "conn_max",
        ("ReadLatency", "Average"): "read_lat",
        ("WriteLatency", "Average"): "write_lat",
        ("DBLoad", "Average"): "dbload_avg",
        ("DBLoad", "Maximum"): "dbload_max",
        ("DiskQueueDepth", "Maximum"): "disk_q",
        ("FreeStorageSpace", "Minimum"): "free_storage",
    }
    out = {v: None for v in keymap.values()}

    queries, idmap = [], {}
    for i, (m, stat) in enumerate(wanted):
        qid = f"q{i}"
        idmap[qid] = (m, stat)
        queries.append({"Id": qid, "MetricStat": {
            "Metric": {"Namespace": "AWS/RDS", "MetricName": m, "Dimensions": dims},
            "Period": 3600, "Stat": stat}})
    r = cw.get_metric_data(
        MetricDataQueries=queries,
        StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
        EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
    for res in r["MetricDataResults"]:
        m, stat = idmap[res["Id"]]
        vals = res["Values"]
        if not vals:
            val = None
        elif stat == "Average":
            val = sum(vals) / len(vals)
        elif stat == "Maximum":
            val = max(vals)
        else:  # Minimum
            val = min(vals)
        out[keymap[(m, stat)]] = val
    return out
