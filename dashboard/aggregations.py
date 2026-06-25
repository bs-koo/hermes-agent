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

# 스캐너/봇 탐침(보안 노이즈): .git, 민감 actuator, wp-, phpmyadmin,
# 그리고 정보노출 탐침(.env / config.env / /api/env / /api/*.env / /api/endpoint 등).
SCANNER_RE = re.compile(
    r"\.git|wp-admin|wp-login|phpmyadmin|/vendor/|\.aws/|"
    r"\.env|/config\.env|/api/env|/api/shared/config\.env|/api/endpoint|"
    r"/api/[^?]*\.env|"
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
        # 전체 로그 수 근사(실사용자 + 헬스체크 + 스캐너). 프론트 "실사용자 N건,
        # 핑/헬스 M건 제외" 설명용. total 은 핑(UA 제외) 적용된 실사용자 기준.
        "total_all": total + health_hits + scanner_hits,
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


# ── 무료 메트릭 확장(EC2 호스트 + RDS 심화) ───────────────────────────
def _reduce(vals, stat):
    """단일 요약값으로 축약. vals 비면 None.
    stat: Average/Maximum/Minimum/Sum 지원(rds_perf 와 일관)."""
    if not vals:
        return None
    if stat == "Average":
        return sum(vals) / len(vals)
    if stat == "Maximum":
        return max(vals)
    if stat == "Minimum":
        return min(vals)
    if stat == "Sum":
        return sum(vals)
    return None


def metric_series(cw, namespace, metric, dims, stat, t0, t1, period=3600):
    """단일 메트릭의 시계열을 [(epoch, val)] 로 반환(Timestamps 오름차순 정렬).
    get_metric_data 만 사용(read-only). 빈 구간/실패는 [] 또는 일부 누락.
    cw 는 boto3 cloudwatch 클라이언트(주입). t0/t1 은 epoch 초."""
    q = [{"Id": "s0", "MetricStat": {
        "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dims},
        "Period": period, "Stat": stat}}]
    r = cw.get_metric_data(
        MetricDataQueries=q,
        StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
        EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc),
        ScanBy="TimestampAscending")
    res = r.get("MetricDataResults", [])
    if not res:
        return []
    r0 = res[0]
    ts = r0.get("Timestamps", [])
    vals = r0.get("Values", [])
    out = []
    for t, v in zip(ts, vals):
        try:
            epoch = int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
        except (TypeError, ValueError, OSError):
            continue
        out.append((epoch, v))
    out.sort(key=lambda x: x[0])
    return out


def list_ec2_instances(cw):
    """list_metrics(AWS/EC2, CPUUtilization)에서 모든 InstanceId 를 정렬·중복제거해
    리스트로 반환한다(계정 전체 EC2 모니터링용). DescribeInstances 권한 불필요.
    실패(예외)/빈 결과 시: config.EC2_INSTANCE_ID 가 있으면 [그 값], 없으면 []."""
    from dashboard import config
    found = set()
    try:
        paginator = cw.get_paginator("list_metrics")
        pages = paginator.paginate(Namespace="AWS/EC2", MetricName="CPUUtilization")
        for page in pages:
            for m in page.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "InstanceId" and d.get("Value"):
                        found.add(d["Value"])
    except Exception:  # noqa: BLE001 — 페이지네이터 미지원/권한/네트워크 실패 시 단발 폴백
        try:
            r = cw.list_metrics(Namespace="AWS/EC2", MetricName="CPUUtilization")
            for m in r.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "InstanceId" and d.get("Value"):
                        found.add(d["Value"])
        except Exception:  # noqa: BLE001 — 그래도 실패하면 기본값 폴백
            pass
    if found:
        return sorted(found)
    return [config.EC2_INSTANCE_ID] if config.EC2_INSTANCE_ID else []


def list_rds_instances(cw):
    """list_metrics(AWS/RDS, CPUUtilization)에서 모든 DBInstanceIdentifier 를 정렬·중복제거해
    리스트로 반환한다(계정 전체 RDS 모니터링용). cw 는 ap-northeast-2 cloudwatch 클라이언트(주입).
    실패(예외)/빈 결과 시: config.DBID 가 있으면 [config.DBID], 없으면 []."""
    from dashboard import config
    found = set()
    try:
        paginator = cw.get_paginator("list_metrics")
        pages = paginator.paginate(Namespace="AWS/RDS", MetricName="CPUUtilization")
        for page in pages:
            for m in page.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "DBInstanceIdentifier" and d.get("Value"):
                        found.add(d["Value"])
    except Exception:  # noqa: BLE001 — 페이지네이터 미지원/권한/네트워크 실패 시 단발 폴백
        try:
            r = cw.list_metrics(Namespace="AWS/RDS", MetricName="CPUUtilization")
            for m in r.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "DBInstanceIdentifier" and d.get("Value"):
                        found.add(d["Value"])
        except Exception:  # noqa: BLE001 — 그래도 실패하면 기본값 폴백
            pass
    if found:
        return sorted(found)
    return [config.DBID] if config.DBID else []


def ec2_metrics(cw, t0, t1, iid):
    """단일 EC2 인스턴스의 무료 메트릭(요약 + cpu_series)을 dict 로 반환.
    cw 는 boto3 cloudwatch 클라이언트(주입). iid 는 InstanceId. t0/t1 은 epoch 초.
    인스턴스 다수(계정 전체) 수집 시 payload 과대를 막기 위해 시계열은 cpu_series 만
    유지하고 net/ebs/credit/status 는 요약값만 둔다. 실패 키는 None/[]."""
    dims = [{"Name": "InstanceId", "Value": iid}]

    # 요약(출력 키 → (metric, stat))
    wanted = {
        "cpu_avg": ("CPUUtilization", "Average"),
        "cpu_max": ("CPUUtilization", "Maximum"),
        "net_in": ("NetworkIn", "Sum"),
        "net_out": ("NetworkOut", "Sum"),
        "ebs_read": ("EBSReadBytes", "Sum"),
        "ebs_write": ("EBSWriteBytes", "Sum"),
        "credit_min": ("CPUCreditBalance", "Minimum"),
        "status_failed": ("StatusCheckFailed", "Maximum"),
    }
    out = {"instance_id": iid}
    out.update({k: None for k in wanted})

    queries, idmap = [], {}
    for i, (key, (m, stat)) in enumerate(wanted.items()):
        qid = f"e{i}"
        idmap[qid] = key
        queries.append({"Id": qid, "MetricStat": {
            "Metric": {"Namespace": "AWS/EC2", "MetricName": m, "Dimensions": dims},
            "Period": 3600, "Stat": stat}})
    r = cw.get_metric_data(
        MetricDataQueries=queries,
        StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
        EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
    for res in r.get("MetricDataResults", []):
        key = idmap.get(res["Id"])
        if key is None:
            continue
        _, stat = wanted[key]
        out[key] = _reduce(res.get("Values", []), stat)

    # 시계열 3종(cpu + net in/out). 클릭 상세용. ebs 는 요약값만(payload 관리). period=3600.
    out["cpu_series"] = metric_series(cw, "AWS/EC2", "CPUUtilization", dims,
                                      "Average", t0, t1)
    # 시간별 최고 CPU(Maximum) — 순간 피크(예: 99%)를 차트에서 보이게(평균엔 묻힘)
    out["cpu_max_series"] = metric_series(cw, "AWS/EC2", "CPUUtilization", dims,
                                          "Maximum", t0, t1)
    # 최근 30일 일별(평균/최고) — "어느 날" 패턴 모니터링용
    t0_d = t1 - 30 * 86400
    out["cpu_series_d"] = metric_series(cw, "AWS/EC2", "CPUUtilization", dims,
                                        "Average", t0_d, t1, period=86400)
    out["cpu_max_series_d"] = metric_series(cw, "AWS/EC2", "CPUUtilization", dims,
                                            "Maximum", t0_d, t1, period=86400)
    out["net_in_series"] = metric_series(cw, "AWS/EC2", "NetworkIn", dims,
                                         "Sum", t0, t1)
    out["net_out_series"] = metric_series(cw, "AWS/EC2", "NetworkOut", dims,
                                          "Sum", t0, t1)
    return out


def rds_extended(cw, t0, t1, dbid="gseed-db"):
    """기존 rds_perf 10항목 + 무료 심화 메트릭 + 시계열을 dict 로 반환.
    기존 rds_perf 를 내부 재사용(회귀 방지)하고 추가 항목만 별도 집계한다.
    cw 는 boto3 cloudwatch 클라이언트(주입). t0/t1 은 epoch 초.
    실패 키는 None/[](시계열)로 채운다."""
    dims = [{"Name": "DBInstanceIdentifier", "Value": dbid}]

    # 1) 기존 10항목(회귀 방지 — rds_perf 그대로 재사용)
    out = rds_perf(cw, t0, t1, dbid=dbid)

    # 2) 추가 요약(출력 키 → (metric, stat))
    extra = {
        "mem_free": ("FreeableMemory", "Minimum"),
        "swap": ("SwapUsage", "Maximum"),
        "read_iops": ("ReadIOPS", "Average"),
        "write_iops": ("WriteIOPS", "Average"),
        "read_tput": ("ReadThroughput", "Average"),
        "write_tput": ("WriteThroughput", "Average"),
        "dbload_cpu": ("DBLoadCPU", "Average"),
        "dbload_noncpu": ("DBLoadNonCPU", "Average"),
        "max_txid": ("MaximumUsedTransactionIDs", "Maximum"),
        "net_rx_tput": ("NetworkReceiveThroughput", "Average"),
        "net_tx_tput": ("NetworkTransmitThroughput", "Average"),
    }
    for k in extra:
        out.setdefault(k, None)

    queries, idmap = [], {}
    for i, (key, (m, stat)) in enumerate(extra.items()):
        qid = f"x{i}"
        idmap[qid] = key
        queries.append({"Id": qid, "MetricStat": {
            "Metric": {"Namespace": "AWS/RDS", "MetricName": m, "Dimensions": dims},
            "Period": 3600, "Stat": stat}})
    r = cw.get_metric_data(
        MetricDataQueries=queries,
        StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
        EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
    for res in r.get("MetricDataResults", []):
        key = idmap.get(res["Id"])
        if key is None:
            continue
        _, stat = extra[key]
        out[key] = _reduce(res.get("Values", []), stat)

    # 3) 시계열(차트용, period=3600)
    out["cpu_series"] = metric_series(cw, "AWS/RDS", "CPUUtilization", dims,
                                      "Average", t0, t1)
    # 시간별 최고 CPU(Maximum) — 순간 피크를 차트에서 보이게
    out["cpu_max_series"] = metric_series(cw, "AWS/RDS", "CPUUtilization", dims,
                                          "Maximum", t0, t1)
    # 최근 30일 일별(평균/최고)
    t0_d = t1 - 30 * 86400
    out["cpu_series_d"] = metric_series(cw, "AWS/RDS", "CPUUtilization", dims,
                                        "Average", t0_d, t1, period=86400)
    out["cpu_max_series_d"] = metric_series(cw, "AWS/RDS", "CPUUtilization", dims,
                                            "Maximum", t0_d, t1, period=86400)
    out["mem_series"] = metric_series(cw, "AWS/RDS", "FreeableMemory", dims,
                                      "Average", t0, t1)
    out["dbload_series"] = metric_series(cw, "AWS/RDS", "DBLoad", dims,
                                         "Average", t0, t1)
    out["conn_series"] = metric_series(cw, "AWS/RDS", "DatabaseConnections", dims,
                                       "Average", t0, t1)
    return out


# ── CloudFront CDN(us-east-1 글로벌 메트릭) ───────────────────────────
def list_cloudfront_dists(cwe):
    """list_metrics(AWS/CloudFront, Requests)에서 모든 DistributionId 를 정렬·중복제거해
    리스트로 반환한다. cwe 는 us-east-1 cloudwatch 클라이언트(주입).
    실패(예외)/빈 결과 시 [] 반환(CloudFront 미사용 계정 등)."""
    found = set()
    try:
        paginator = cwe.get_paginator("list_metrics")
        pages = paginator.paginate(Namespace="AWS/CloudFront", MetricName="Requests")
        for page in pages:
            for m in page.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "DistributionId" and d.get("Value"):
                        found.add(d["Value"])
    except Exception:  # noqa: BLE001 — 페이지네이터 미지원/권한/네트워크 실패 시 단발 폴백
        try:
            r = cwe.list_metrics(Namespace="AWS/CloudFront", MetricName="Requests")
            for m in r.get("Metrics", []):
                for d in m.get("Dimensions", []):
                    if d.get("Name") == "DistributionId" and d.get("Value"):
                        found.add(d["Value"])
        except Exception:  # noqa: BLE001 — 그래도 실패하면 빈 리스트
            pass
    return sorted(found)


def cloudfront_metrics(cwe, t0, t1, dist):
    """단일 CloudFront 배포의 무료 메트릭(요약 + 시계열)을 dict 로 반환.
    cwe 는 us-east-1 cloudwatch 클라이언트(주입). dist 는 DistributionId. t0/t1 은 epoch 초.
    bytes 는 원단위(프론트 변환), ErrorRate 는 % 값. 실패 키는 None/[](시계열)."""
    dims = [{"Name": "DistributionId", "Value": dist},
            {"Name": "Region", "Value": "Global"}]

    # 요약(출력 키 → (metric, stat))
    wanted = {
        "requests": ("Requests", "Sum"),
        "bytes_down": ("BytesDownloaded", "Sum"),
        "bytes_up": ("BytesUploaded", "Sum"),
        "err_4xx": ("4xxErrorRate", "Average"),
        "err_5xx": ("5xxErrorRate", "Average"),
        "err_total": ("TotalErrorRate", "Average"),
        # 5xx 유형 분해 — CloudFront '추가 지표' 활성 시에만 값 존재(미활성이면 None)
        "err_502": ("502ErrorRate", "Average"),
        "err_503": ("503ErrorRate", "Average"),
        "err_504": ("504ErrorRate", "Average"),
    }
    out = {"dist_id": dist}
    out.update({k: None for k in wanted})

    queries, idmap = [], {}
    for i, (key, (m, stat)) in enumerate(wanted.items()):
        qid = f"c{i}"
        idmap[qid] = key
        queries.append({"Id": qid, "MetricStat": {
            "Metric": {"Namespace": "AWS/CloudFront", "MetricName": m, "Dimensions": dims},
            "Period": 3600, "Stat": stat}})
    r = cwe.get_metric_data(
        MetricDataQueries=queries,
        StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
        EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
    for res in r.get("MetricDataResults", []):
        key = idmap.get(res["Id"])
        if key is None:
            continue
        _, stat = wanted[key]
        out[key] = _reduce(res.get("Values", []), stat)

    # 시계열(차트용, period=3600)
    out["requests_series"] = metric_series(cwe, "AWS/CloudFront", "Requests", dims,
                                           "Sum", t0, t1)
    out["err_total_series"] = metric_series(cwe, "AWS/CloudFront", "TotalErrorRate", dims,
                                            "Average", t0, t1)
    return out


# ── EC2 인스턴스 메타(describe_instances, 권한 없을 수 있어 graceful) ──
def ec2_instance_meta(ec2c, iids):
    """describe_instances 로 인스턴스 메타를 조회해 {iid: {...}} 로 반환한다.
    ec2c 는 boto3 ec2 클라이언트(주입). iids 는 InstanceId 리스트.
    반환: {iid: {"private_ip", "name", "instance_type", "state"}}.
    권한 없음(UnauthorizedOperation 등)/예외/빈 iids 시 빈 dict {} 반환(graceful).
    일부 iid 가 응답에서 빠져도 그 iid 만 누락될 뿐 전체는 안전하다."""
    if not iids:
        return {}
    out = {}
    try:
        r = ec2c.describe_instances(InstanceIds=list(iids))
    except Exception:  # noqa: BLE001 — 권한/네트워크/유효성 실패 시 메타 없이 진행
        return {}
    for resv in r.get("Reservations", []):
        for inst in resv.get("Instances", []):
            iid = inst.get("InstanceId")
            if not iid:
                continue
            name = None
            for tag in inst.get("Tags", []):
                if tag.get("Key") == "Name":
                    name = tag.get("Value")
                    break
            out[iid] = {
                "private_ip": inst.get("PrivateIpAddress"),
                "name": name,
                "instance_type": inst.get("InstanceType"),
                "state": (inst.get("State") or {}).get("Name"),
            }
    return out
