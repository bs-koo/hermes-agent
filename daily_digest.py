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
