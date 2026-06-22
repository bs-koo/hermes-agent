# -*- coding: utf-8 -*-
"""웹 채팅 Q&A — SQLite 캐시 데이터를 컨텍스트로 Gemini 한국어 답변.

기존 Google Chat 봇의 Q&A 를 웹으로 옮긴 것. AWS 를 호출하지 않고
이미 수집된 SQLite 스냅샷(storage)만 근거로 한다(api.py 와 동일 원칙).

의존성 추가 금지: google-generativeai 대신 urllib.request 로 Gemini REST 직접 호출
(기존 스크립트의 webhook 호출과 동일 패턴)."""
import json
import datetime
import urllib.request
import urllib.error

from dashboard import config, storage


GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent?key={key}")

SYSTEM_PROMPT = (
    "너는 dataviz-prod 운영 모니터링 보조다. 아래 현재 수집 데이터만 근거로 "
    "한국어로 간결히 답하라. 데이터에 없는 건 모른다고 답하라.")


# ── KST 시각 ──────────────────────────────────────────────────────────
def _kst(epoch):
    """epoch(초, UTC) → KST(+9h) 'YYYY-MM-DD HH:MM' 문자열. None/0 은 '-'.
    프론트(dashboard.js)와 동일하게 +9h 후 UTC 게터로 KST 표현."""
    if not epoch:
        return "-"
    try:
        dt = datetime.datetime.fromtimestamp(int(epoch) + 9 * 3600,
                                             datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


# ── 컨텍스트 구성(빈 DB 에서도 예외 없이 '데이터 없음') ──────────────
def _alarms_ctx():
    rows = storage.get_alarms()
    if not rows:
        return "알람: 데이터 없음"
    total = len(rows)
    alarms = [r for r in rows if r["state"] == "ALARM"]
    lines = [f"알람: 총 {total}건, ALARM {len(alarms)}건"]
    for r in alarms:
        lines.append(f"  - {r['alarm_name']} (ALARM, {_kst(r['state_updated'])})")
    return "\n".join(lines)


def _uptime_ctx():
    rows = storage.get_uptime(1)
    if not rows:
        return "가동률(최근24h): 데이터 없음"
    agg = {}
    for r in rows:
        ep = r["endpoint"]
        s = agg.setdefault(ep, {"ok": 0, "tot": 0})
        s["ok"] += r["ok_count"] or 0
        s["tot"] += r["total_count"] or 0
    lines = ["가동률(최근24h):"]
    for ep, s in agg.items():
        pct = (s["ok"] / s["tot"] * 100) if s["tot"] else None
        pct_s = f"{pct:.2f}%" if pct is not None else "-"
        lines.append(f"  - {ep}: {pct_s} (성공 {s['ok']}/{s['tot']})")
    return "\n".join(lines)


def _traffic_ctx():
    snap = storage.get_traffic(7)
    if snap is None:
        return "트래픽(최근7일): 데이터 없음"
    p = snap["payload"]
    lines = [
        f"트래픽(최근7일, 수집 {_kst(snap.get('collected_at'))}):",
        f"  - 총 요청수: {p.get('total', 0)}",
        f"  - 사용자수: {p.get('n_users', 0)}",
    ]
    buckets = p.get("buckets") or {}
    if buckets:
        dist = ", ".join(f"{k} {v}" for k, v in buckets.items())
        lines.append(f"  - 상태분포: {dist}")
    top_ep = p.get("top_ep") or []
    for e in top_ep[:5]:
        lines.append(f"  - TopEP: {e.get('key')} ({e.get('hits')})")
    return "\n".join(lines)


def _db_ctx():
    snap = storage.get_db()
    if snap is None:
        return "DB(RDS): 데이터 없음"
    p = snap["payload"]

    def _fmt(v, suffix=""):
        if v is None:
            return "-"
        try:
            return f"{float(v):.1f}{suffix}"
        except (TypeError, ValueError):
            return "-"

    free = p.get("free_storage")
    free_s = f"{free / 1e9:.1f}GB" if isinstance(free, (int, float)) else "-"
    lines = [
        f"DB(RDS, 수집 {_kst(snap.get('collected_at'))}):",
        f"  - CPU 평균/최대: {_fmt(p.get('cpu_avg'), '%')}/{_fmt(p.get('cpu_max'), '%')}",
        f"  - 연결 평균/최대: {_fmt(p.get('conn_avg'))}/{_fmt(p.get('conn_max'))}",
        f"  - 여유공간: {free_s}",
    ]
    return "\n".join(lines)


def _build_context():
    """현재 수집 데이터 요약 컨텍스트(빈 DB 에서도 안전)."""
    now_kst = _kst(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
    parts = [
        f"[현재 시각(KST): {now_kst}]",
        _alarms_ctx(),
        _uptime_ctx(),
        _traffic_ctx(),
        _db_ctx(),
    ]
    return "\n\n".join(parts)


# ── Gemini REST 호출 ──────────────────────────────────────────────────
def answer_question(question):
    """질문을 현재 수집 데이터 컨텍스트와 함께 Gemini 에 보내 한국어 답변을 받는다.
    성공: {"answer": text}. 키 미설정: {"error": ...}. 호출 실패/예외: {"error": ...}."""
    key = config.GEMINI_API_KEY
    if not key:
        return {"error": "GEMINI_API_KEY 미설정"}

    context = _build_context()
    prompt = (f"{SYSTEM_PROMPT}\n\n[현재 수집 데이터]\n{context}\n\n"
              f"[질문]\n{question}")

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }, ensure_ascii=False).encode("utf-8")

    url = GEMINI_URL.format(model=config.GEMINI_MODEL, key=key)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"Gemini 호출 실패(HTTP {e.code})"}
    except Exception as e:  # noqa: BLE001 — 네트워크/파싱 등 모든 예외를 error 로
        return {"error": f"Gemini 호출 실패: {e}"}

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        return {"error": "Gemini 응답 파싱 실패"}
    if not text:
        return {"error": "Gemini 빈 응답"}
    return {"answer": text}
