# -*- coding: utf-8 -*-
"""dataviz-prod $0 가동률·응답시간 핑 (호스트 python, AWS 불필요).
공개 엔드포인트를 HTTP GET해 응답시간/생존을 측정 → CSV 기록 → 다운/복구 '전환' 시에만 Google Chat 알림.
스케줄: 5분마다(Hermes-UptimePing). 수동: python uptime_ping.py
"""
import os, time, json, csv, datetime, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = [
    ("health", "https://app.gx-viz.com/actuator/health"),
    ("home",   "https://app.gx-viz.com/"),
]
LOG = os.path.join(HERE, "uptime_log.csv")
STATE = os.path.join(HERE, "uptime_state.json")
from _env import load_env
load_env()
WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "")
TIMEOUT = 10
MAX_ROWS = 20000  # CSV 크기 제한(약 70일치)


def check(url):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dataviz-uptime-ping/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, round((time.time() - t0) * 1000, 1), ""
    except urllib.error.HTTPError as e:
        return e.code, round((time.time() - t0) * 1000, 1), ""
    except Exception as e:
        return 0, round((time.time() - t0) * 1000, 1), str(e)[:80]


def is_ok(status):
    return 200 <= status < 400


def post(text):
    try:
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK, data=body,
                                     headers={"Content-Type": "application/json; charset=UTF-8"})
        urllib.request.urlopen(req, timeout=20)
    except Exception:
        pass


def load_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


now = datetime.datetime.now()
epoch = int(time.time())
ts = now.strftime("%Y-%m-%d %H:%M:%S")  # KST(호스트 로컬) 가독용
state = load_json(STATE, {})
rows = []
for name, url in TARGETS:
    status, ms, err = check(url)
    ok = is_ok(status)
    rows.append([epoch, ts, name, status, ms, int(ok), err])
    if name == "health":  # 알림은 health 기준, 상태 전환 시에만(스팸 방지)
        prev = state.get("health_ok")
        if prev is True and not ok:
            post(f"🔴 *[다운] dataviz-prod* · {ts}\nhealth 응답 실패 (HTTP {status}{', ' + err if err else ''})")
        elif prev is False and ok:
            post(f"🟢 *[복구] dataviz-prod* · {ts}\nhealth 정상 (HTTP {status}, {ms}ms)")
        state["health_ok"] = ok

with open(STATE, "w") as f:
    json.dump(state, f)

# CSV append (+ 크기 제한)
newfile = not os.path.exists(LOG)
with open(LOG, "a", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    if newfile:
        w.writerow(["epoch", "ts", "endpoint", "status", "ms", "ok", "err"])
    w.writerows(rows)

try:
    with open(LOG, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > MAX_ROWS + 1:
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(lines[0])
            f.writelines(lines[-MAX_ROWS:])
except Exception:
    pass

print("logged", ts, rows)
