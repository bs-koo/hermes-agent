# -*- coding: utf-8 -*-
"""dataviz-prod 주간 인사이트 리포트.
nginx access.log(CloudWatch Logs Insights)를 집계해 트래픽/사용자/품질 인사이트를 만들고,
(권한 있으면) Cost Explorer 비용까지 붙여 Google Chat 스페이스로 전송.

cloudwatch-mcp 컨테이너에서 boto3로 실행:
  docker run --rm -v C:/Users/SQI/.aws:/root/.aws:ro -v D:/SQ/hermes_agent:/work:ro \
    -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 -e POST=1 \
    --entrypoint python cloudwatch-mcp:0.1.4 /work/weekly_insight.py

환경변수:
  POST=1   → 실제 Google Chat 전송 (기본 0: 콘솔 출력만, 테스트용)
  DAYS=7   → 집계 기간(일)
"""
import os, sys, time, json, datetime, urllib.request, re
import boto3
from _env import load_env

load_env()
REGION = "ap-northeast-2"
LG = "/aws/elasticbeanstalk/dataviz-prod/var/log/nginx/access.log"
WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")

DAYS = int(os.environ.get("DAYS", "7"))
DO_POST = os.environ.get("POST") == "1"

logs = boto3.client("logs", region_name=REGION)
now = int(time.time())
start = now - DAYS * 24 * 3600
prev_start = start - DAYS * 24 * 3600  # 직전 동기간(전주 대비용)

PARSE = (r"""parse @message '* - - [*] "* * *" * * "*" "*" "*"' """
         r"""as ip, ts, method, url, proto, status, bytes, referer, ua, xff""")

# 스캐너/봇 탐침(보안 노이즈): .git, 민감 actuator, wp-, phpmyadmin 등
SCANNER_RE = re.compile(r"\.git|/\.env|wp-admin|wp-login|phpmyadmin|/vendor/|\.aws/|"
                        r"/actuator/(env|heapdump|configprops|mappings|beans|threaddump|loggers)", re.I)
# 인프라 헬스체크(정상 트래픽이지만 '사용자 활동'은 아님 → 집계에서 분리)
HEALTH_RE = re.compile(r"/actuator/health|/actuator/info|/health$|/ping$", re.I)


def insights(tail, t0, t1, limit=1000):
    """Logs Insights 쿼리 실행 후 [{field:value}] 리스트 반환.
    가동률 핑(uptime_ping.py)의 자기 트래픽은 User-Agent로 제외해 통계 오염 방지."""
    q = PARSE + "\n| filter ua not like /dataviz-uptime-ping/\n| " + tail
    qid = logs.start_query(logGroupName=LG, startTime=t0, endTime=t1,
                           queryString=q, limit=limit)["queryId"]
    for _ in range(60):
        r = logs.get_query_results(queryId=qid)
        if r["status"] == "Complete":
            break
        time.sleep(1)
    out = []
    for row in r.get("results", []):
        out.append({f["field"]: f["value"] for f in row})
    return out


def strip_qs(u):
    return u.split("?", 1)[0]


def fmt_int(n):
    return f"{n:,}"


def kst_today():
    return (datetime.datetime.fromtimestamp(now, datetime.timezone.utc) + datetime.timedelta(hours=9))


def fnum(v, fmt="{:.1f}", scale=1.0, suffix=""):
    return (fmt.format(v * scale) + suffix) if v is not None else "—"


def rds_perf(t0, t1, dbid="gseed-db"):
    """RDS 성능/부하 메트릭(서버 무수정, AWS-side 측정)을 집계해 {(metric,stat):value} 반환."""
    cw = boto3.client("cloudwatch", region_name=REGION)
    dims = [{"Name": "DBInstanceIdentifier", "Value": dbid}]
    wanted = [
        ("CPUUtilization", "Average"), ("CPUUtilization", "Maximum"),
        ("DatabaseConnections", "Average"), ("DatabaseConnections", "Maximum"),
        ("ReadLatency", "Average"), ("WriteLatency", "Average"),
        ("DBLoad", "Average"), ("DBLoad", "Maximum"),
        ("DiskQueueDepth", "Maximum"), ("FreeStorageSpace", "Minimum"),
    ]
    queries, idmap = [], {}
    for i, (m, stat) in enumerate(wanted):
        qid = f"q{i}"
        idmap[qid] = (m, stat)
        queries.append({"Id": qid, "MetricStat": {
            "Metric": {"Namespace": "AWS/RDS", "MetricName": m, "Dimensions": dims},
            "Period": 3600, "Stat": stat}})
    try:
        r = cw.get_metric_data(
            MetricDataQueries=queries,
            StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
            EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
    except Exception:
        return {}
    agg = {}
    for res in r["MetricDataResults"]:
        m, stat = idmap[res["Id"]]
        vals = res["Values"]
        if not vals:
            agg[(m, stat)] = None
        elif stat == "Average":
            agg[(m, stat)] = sum(vals) / len(vals)
        elif stat == "Maximum":
            agg[(m, stat)] = max(vals)
        else:  # Minimum
            agg[(m, stat)] = min(vals)
    return agg


def canary_perf(t0, t1, name="dataviz-prod-uptime"):
    """Synthetics 캐너리(가동률·응답시간) 집계. 캐너리 없으면 전부 None."""
    cw = boto3.client("cloudwatch", region_name=REGION)

    def pull(metric, stat, dims):
        try:
            r = cw.get_metric_data(MetricDataQueries=[{"Id": "q", "MetricStat": {
                "Metric": {"Namespace": "CloudWatchSynthetics", "MetricName": metric, "Dimensions": dims},
                "Period": 3600, "Stat": stat}}],
                StartTime=datetime.datetime.fromtimestamp(t0, datetime.timezone.utc),
                EndTime=datetime.datetime.fromtimestamp(t1, datetime.timezone.utc))
            vals = r["MetricDataResults"][0]["Values"]
            if not vals:
                return None
            return sum(vals) / len(vals) if stat == "Average" else max(vals)
        except Exception:
            return None

    cdim = [{"Name": "CanaryName", "Value": name}]
    out = {"success": pull("SuccessPercent", "Average", cdim),
           "dur_avg": pull("Duration", "Average", cdim),
           "dur_max": pull("Duration", "Maximum", cdim),
           "steps": {}}
    for step in ("health", "homepage"):
        sdim = cdim + [{"Name": "StepName", "Value": step}]
        out["steps"][step] = pull("Duration", "Average", sdim)
    return out


def uptime_from_csv(t0, t1):
    """uptime_ping.py가 쌓은 CSV($0 PC 핑)에서 가동률·응답시간 집계. 데이터 없으면 None."""
    import csv as _csv
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uptime_log.csv")
    try:
        with open(path, encoding="utf-8") as f:
            rd = list(_csv.DictReader(f))
    except Exception:
        return None
    h_ok = h_tot = 0
    ms_by = {}
    for r in rd:
        try:
            e = int(r["epoch"])
        except Exception:
            continue
        if not (t0 <= e <= t1):
            continue
        ep = r.get("endpoint", "")
        try:
            ms = float(r["ms"]); ok = r["ok"] == "1"
        except Exception:
            continue
        ms_by.setdefault(ep, []).append(ms)
        if ep == "health":
            h_tot += 1
            h_ok += 1 if ok else 0
    if h_tot == 0:
        return None

    def avg(l):
        return sum(l) / len(l) if l else None

    def p95(l):
        if not l:
            return None
        s = sorted(l)
        return s[min(len(s) - 1, int(len(s) * 0.95))]

    return {"uptime": h_ok / h_tot * 100, "checks": h_tot, "down": h_tot - h_ok,
            "h_avg": avg(ms_by.get("health", [])), "h_p95": p95(ms_by.get("health", [])),
            "home_avg": avg(ms_by.get("home", []))}


# ── 1) 집계 ──────────────────────────────────────────────────────────
# 총 요청수(이번 기간 / 직전 기간)
this_cnt = insights("filter ispresent(url) | stats count(*) as c", start, now)
prev_cnt = insights("filter ispresent(url) | stats count(*) as c", prev_start, start)
total = int(this_cnt[0]["c"]) if this_cnt else 0
prev_total = int(prev_cnt[0]["c"]) if prev_cnt else 0

# Top 엔드포인트(쿼리스트링 제거 후 재집계)
raw_urls = insights("filter ispresent(url) | stats count(*) as hits by method, url "
                    "| sort hits desc | limit 300", start, now)
ep = {}
scanner_hits = 0
health_hits = 0
for r in raw_urls:
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
status_rows = insights("filter ispresent(status) | stats count(*) as hits by status "
                       "| sort hits desc", start, now)
buckets = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "기타": 0}
for r in status_rows:
    try:
        s = int(r["status"])
    except ValueError:
        buckets["기타"] += int(r["hits"]); continue
    k = f"{s // 100}xx"
    buckets[k] = buckets.get(k, 0) + int(r["hits"])

# 에러 URL Top
err_rows = insights("filter status >= 400 | stats count(*) as hits by status, url "
                    "| sort hits desc | limit 40", start, now)
err = {}
for r in err_rows:
    path = strip_qs(r["url"])
    if SCANNER_RE.search(path) or HEALTH_RE.search(path):
        continue  # 노이즈는 에러 목록에서 제외(진짜 앱 에러만)
    key = f"{r['status']} {path}"
    err[key] = err.get(key, 0) + int(r["hits"])
top_err = sorted(err.items(), key=lambda x: -x[1])[:5]

# 사용자(XFF 실IP)
user_rows = insights("filter ispresent(xff) | stats count(*) as hits by xff "
                     "| sort hits desc | limit 10", start, now)
n_users = len(user_rows)
top_users = [(r["xff"], int(r["hits"])) for r in user_rows[:3]]

# ── 1.5) 가동률·응답시간(PC 핑 / Synthetics) + DB 성능/부하 ──
uptime = uptime_from_csv(start, now)
canary = canary_perf(start, now)
perf = rds_perf(start, now)

# (비용 섹션 제거됨 — Cost Explorer API는 호출당 $0.01 과금이라 사용 안 함)

# ── 3) 메시지 구성 ───────────────────────────────────────────────────
period = f"{(kst_today()-datetime.timedelta(days=DAYS)).strftime('%m/%d')}~{kst_today().strftime('%m/%d')}"
L = []
L.append(f"📊 *dataviz-prod 주간 인사이트* · {period} (최근 {DAYS}일)")
L.append("")

# 트래픽
if prev_total:
    diff = (total - prev_total) / prev_total * 100
    trend = f"전주 대비 {diff:+.0f}%"
else:
    trend = "전주 데이터 없음"
L.append(f"🌐 *트래픽*  총 {fmt_int(total)}건 · 일평균 {fmt_int(total // max(DAYS,1))}건 · {trend}")
L.append(f"👥 *사용자*  활성 클라이언트 {n_users}명"
         + (("  ·  " + ", ".join(f"{ip}({h})" for ip, h in top_users)) if top_users else ""))
L.append("")

# Top 화면/API
L.append("🔥 *많이 쓴 화면/API*")
if top_ep:
    for key, h in top_ep:
        L.append(f"  • {key} — {fmt_int(h)}")
else:
    L.append("  (데이터 없음)")
L.append("")

# 품질
q = (f"2xx {buckets['2xx']} / 3xx {buckets['3xx']} / "
     f"4xx {buckets['4xx']} / 5xx {buckets['5xx']}")
L.append(f"✅ *응답 품질*  {q}")
if top_err:
    L.append("  ⚠️ 에러 URL:")
    for key, h in top_err:
        L.append(f"    - {key} ({h})")
if scanner_hits:
    L.append(f"  🤖 스캐너/봇 탐침 {scanner_hits}건 무시(.git·actuator 등)")
L.append("")

# 가동률·응답시간 (PC 핑 측정 — uptime_ping.py CSV)
if uptime:
    L.append("📈 *가동률·응답시간*  (PC 핑, 5분 주기)")
    L.append(f"  가동률 {uptime['uptime']:.2f}%  ({uptime['checks']}회 점검"
             + (f", 실패 {uptime['down']}회" if uptime['down'] else "") + ")")
    L.append(f"  health 응답 평균 {fnum(uptime['h_avg'],'{:.0f}')}ms · P95 {fnum(uptime['h_p95'],'{:.0f}')}ms")
    if uptime["home_avg"] is not None:
        L.append(f"  홈페이지 평균 {fnum(uptime['home_avg'],'{:.0f}')}ms")
    L.append("")

# 가동률·응답시간 (Synthetics 캐너리 — 생성됐을 때만 표시; PC 핑이 없을 때 대체)
if not uptime and canary and (canary["success"] is not None or canary["dur_avg"] is not None):
    L.append("📈 *가동률·응답시간*  (Synthetics 능동 측정)")
    if canary["success"] is not None:
        L.append(f"  가동률 {canary['success']:.1f}%")
    if canary["dur_avg"] is not None:
        L.append(f"  응답시간 평균 {canary['dur_avg']:.0f}ms · 피크 {fnum(canary['dur_max'],'{:.0f}')}ms")
    steps = [f"{k} {v:.0f}ms" for k, v in canary["steps"].items() if v is not None]
    if steps:
        L.append("  엔드포인트별: " + " · ".join(steps))
    L.append("")

# DB 성능/부하 (서버 무수정 · AWS 메트릭 기반)
def pg(m, s):
    return perf.get((m, s))
if perf:
    L.append(f"🗄️ *DB 성능/부하*  (gseed-db, 최근 {DAYS}일 · AWS 메트릭)")
    L.append(f"  CPU 평균 {fnum(pg('CPUUtilization','Average'))}% · 피크 {fnum(pg('CPUUtilization','Maximum'))}%")
    L.append(f"  연결 평균 {fnum(pg('DatabaseConnections','Average'),'{:.0f}')} · 피크 {fnum(pg('DatabaseConnections','Maximum'),'{:.0f}')}")
    L.append(f"  쿼리지연(평균) 읽기 {fnum(pg('ReadLatency','Average'),'{:.2f}',1000)}ms · 쓰기 {fnum(pg('WriteLatency','Average'),'{:.2f}',1000)}ms")
    L.append(f"  DB부하(활성세션) 평균 {fnum(pg('DBLoad','Average'),'{:.2f}')} · 피크 {fnum(pg('DBLoad','Maximum'),'{:.0f}')}")
    L.append(f"  디스크큐 피크 {fnum(pg('DiskQueueDepth','Maximum'),'{:.2f}')} · 여유공간 최소 {fnum(pg('FreeStorageSpace','Minimum'),'{:.1f}',1e-9)}GB")
    L.append("  ※ per-URL 응답시간은 앱 계측이 없어 미측정 — DB 지연/부하로 병목 추정")
    L.append("")

msg = "\n".join(L)


def post(text):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body,
                                 headers={"Content-Type": "application/json; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status


if __name__ == "__main__":
    sys.stdout.write(msg + "\n")          # stdout = 메시지만(봇 cron 전달용)
    sys.stderr.write("\n--- meta: total=%d prev=%d scanner=%d health=%d ---\n"
                     % (total, prev_total, scanner_hits, health_hits))
    if DO_POST:
        sys.stderr.write(f"[webhook POST http {post(msg)}]\n")
