# -*- coding: utf-8 -*-
"""dataviz-prod 일일 점검 — 알람(각각)·가동률(엔드포인트별)·DB를 상세하게 출력.
stdout에는 '메시지만' 깔끔하게 출력(봇 cron --no-agent가 그대로 전달).
POST=1이면 웹훅으로도 전송(상태는 stderr). cloudwatch-mcp 컨테이너에서 실행."""
import os, sys, json, csv, time, datetime, urllib.request
import boto3
from _env import load_env

load_env()
REGION = "ap-northeast-2"
WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")
DO_POST = os.environ.get("POST") == "1"
DOW = ["월", "화", "수", "목", "금", "토", "일"]


def fetch_alarms():
    cw = boto3.client("cloudwatch", region_name=REGION)
    out = []
    for page in cw.get_paginator("describe_alarms").paginate():
        out += page.get("MetricAlarms", [])
        out += page.get("CompositeAlarms", [])
    return out


def uptime_24h():
    """엔드포인트별 {pct, ok, tot, avg} 반환."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uptime_log.csv")
    cutoff = int(time.time()) - 24 * 3600
    agg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    if int(r["epoch"]) < cutoff:
                        continue
                    ep = r["endpoint"]
                    d = agg.setdefault(ep, {"ok": 0, "tot": 0, "ms": []})
                    d["tot"] += 1
                    d["ok"] += 1 if r["ok"] == "1" else 0
                    d["ms"].append(float(r["ms"]))
                except Exception:
                    continue
    except Exception:
        return {}
    res = {}
    for ep, d in agg.items():
        res[ep] = {"pct": d["ok"] / d["tot"] * 100, "ok": d["ok"], "tot": d["tot"],
                   "avg": sum(d["ms"]) / len(d["ms"]) if d["ms"] else None}
    return res


def rds_brief():
    cw = boto3.client("cloudwatch", region_name=REGION)
    dims = [{"Name": "DBInstanceIdentifier", "Value": "gseed-db"}]
    spec = [("CPUUtilization", "Average"), ("CPUUtilization", "Maximum"),
            ("DatabaseConnections", "Average"), ("FreeStorageSpace", "Minimum")]
    q = [{"Id": f"q{i}", "MetricStat": {
            "Metric": {"Namespace": "AWS/RDS", "MetricName": m, "Dimensions": dims},
            "Period": 86400, "Stat": s}} for i, (m, s) in enumerate(spec)]
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        r = cw.get_metric_data(MetricDataQueries=q, StartTime=now - datetime.timedelta(days=1), EndTime=now)
        v = {res["Id"]: (res["Values"][0] if res["Values"] else None) for res in r["MetricDataResults"]}
        return {"cpu_avg": v["q0"], "cpu_max": v["q1"], "conn": v["q2"], "free": v["q3"]}
    except Exception:
        return None


# ── 대시보드 API 재사용(AI 분석·두레이 주간보고) ──────────────────────
# 대시보드(상시 가동)가 만든 인사이트 AI 코멘트·두레이 데이터를 HTTP 로 가져온다.
# 봇 cron 이 다른 호스트/컨테이너면 DASH_URL 로 주소를 조정(기본 localhost:8090).
def _dash_get(path):
    url = os.environ.get("DASH_URL", "http://localhost:8090").rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — 대시보드 미가동 시 해당 섹션만 생략
        return None


def ai_section():
    """인사이트 룰 + Gemini 종합 분석(채팅과 동일 톤). 없으면 None."""
    d = _dash_get("/api/insights")
    if not d:
        return None
    c = (d.get("ai_comment") or "").strip()
    if not c:
        return None
    return "🤖 *AI 분석*\n" + c


def dooray_section():
    """두레이 '파트업무진행' 주간 업무 — 파트장 메일 형식(담당자 제외, 레이아웃 순서)."""
    d = _dash_get("/api/dooray")
    if not d or d.get("empty"):
        return None
    lay = (_dash_get("/api/dooray/layout") or {}).get("layout") or {"buckets": []}
    tasks = d.get("tasks") or []
    by_tag = {}
    for t in tasks:
        for tag in (t.get("tags") or ["기타"]):
            by_tag.setdefault(tag, []).append(t)
    assigned, buckets = set(), []
    for b in lay.get("buckets", []):
        projs = [(tag, by_tag[tag]) for tag in b.get("tags", []) if by_tag.get(tag)]
        for tag, _ in projs:
            assigned.add(tag)
        buckets.append((b.get("label", ""), b.get("goal", ""), projs))
    etc = [(tag, by_tag[tag]) for tag in by_tag if tag not in assigned]
    out = ["📋 *데이터플랫폼파트 주간 업무*", "", "🗓️ 전주 실적"]

    def emit(label, goal, projs):
        out.append(("[%s] %s" % (label, goal)) if goal else "[%s]" % label)
        for tag, ts in projs:
            out.append(tag)
            seen = set()
            for t in ts:
                s = (t.get("subject") or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append("o " + s)
            out.append("")

    for label, goal, projs in buckets:
        if projs:
            emit(label, goal, projs)
    if etc:
        emit("기타", "", etc)
    out += ["🗓️ 금주 계획", "(다음 주 계획을 작성하세요)", "", "📋 기타사항", "특이사항 없음"]
    return "\n".join(out).rstrip()


def build():
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    date_str = now.strftime("%Y-%m-%d") + f" ({DOW[now.weekday()]})"
    L = [f"🔔 *{date_str} 일일점검*", ""]

    # 알람 — 각각
    alarms = fetch_alarms()
    state_icon = {"OK": "✅", "ALARM": "🚨", "INSUFFICIENT_DATA": "⚪"}
    state_kr = {"OK": "정상", "ALARM": "ALARM", "INSUFFICIENT_DATA": "데이터부족"}
    n_alarm = sum(1 for a in alarms if a["StateValue"] == "ALARM")
    head = "🚨" if n_alarm else "✅"
    L.append(f"{head} *알람*  전체 {len(alarms)}건 · ALARM {n_alarm}건")
    for a in sorted(alarms, key=lambda x: x["StateValue"] != "ALARM"):
        sv = a["StateValue"]
        L.append(f"   {state_icon.get(sv,'•')} {a['AlarmName']} — {state_kr.get(sv, sv)}")
    L.append("")

    # 가동률 — 엔드포인트별 %
    up = uptime_24h()
    if up:
        L.append("📈 *가동률 (최근 24h)*")
        label = {"health": "health(앱헬스)", "home": "home(홈페이지)"}
        for ep in ("health", "home"):
            if ep in up:
                d = up[ep]
                avg = f" · 평균 {d['avg']:.0f}ms" if d["avg"] is not None else ""
                L.append(f"   • {label.get(ep, ep)}  {d['pct']:.2f}%  ({d['ok']}/{d['tot']}){avg}")
        L.append("")

    # DB — 항목별
    db = rds_brief()
    if db:
        L.append("🗄️ *DB (gseed-db, 24h)*")
        if db["cpu_avg"] is not None:
            L.append(f"   • CPU  평균 {db['cpu_avg']:.1f}%"
                     + (f" · 피크 {db['cpu_max']:.1f}%" if db["cpu_max"] is not None else ""))
        if db["conn"] is not None:
            L.append(f"   • 연결  평균 {db['conn']:.0f}개")
        if db["free"] is not None:
            L.append(f"   • 여유공간  {db['free']/1e9:.1f}GB")

    # AI 종합 분석(인사이트 룰 + Gemini, 채팅과 동일 전문가 톤)
    ai = ai_section()
    if ai:
        L.append("")
        L.append(ai)

    # 금요일(weekday 4) 오전: AWS 보고에 두레이 주간 업무 보고를 함께 첨부
    if now.weekday() == 4:
        dr = dooray_section()
        if dr:
            L.append("")
            L.append("────────────────────")
            L.append("")
            L.append(dr)

    return "\n".join(L).rstrip()


def post(text):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body,
                                 headers={"Content-Type": "application/json; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status


if __name__ == "__main__":
    msg = build()
    sys.stdout.write(msg + "\n")          # stdout = 메시지만(봇 cron 전달용)
    if DO_POST:
        sys.stderr.write(f"\n[webhook POST http {post(msg)}]\n")
