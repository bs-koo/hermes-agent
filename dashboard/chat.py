# -*- coding: utf-8 -*-
"""웹 채팅 Q&A — SQLite 캐시 데이터를 컨텍스트로 Gemini 한국어 답변.

기존 Google Chat 봇의 Q&A 를 웹으로 옮긴 것. AWS 를 호출하지 않고
이미 수집된 SQLite 스냅샷(storage)만 근거로 한다(api.py 와 동일 원칙).

의존성 추가 금지: google-generativeai 대신 urllib.request 로 Gemini REST 직접 호출
(기존 스크립트의 webhook 호출과 동일 패턴)."""
import json
import time
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
def _gemini_call(prompt, max_tokens=800, temperature=0.2):
    """Gemini REST 호출 공통 헬퍼. 성공: {"answer": text} / 실패: {"error": msg}.
    일시 오류(5xx/429/네트워크)는 점증 백오프로 최대 3회 재시도, 4xx 는 즉시 반환."""
    key = config.GEMINI_API_KEY
    if not key:
        return {"error": "GEMINI_API_KEY 미설정"}

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }, ensure_ascii=False).encode("utf-8")

    url = GEMINI_URL.format(model=config.GEMINI_MODEL, key=key)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"})
    data = None
    # 일시적 오류(5xx/429: Gemini 과부하, DNS/네트워크)는 최대 3회까지 재시도.
    # 4xx(키/요청 오류)는 재시도 무의미 → 즉시 반환.
    _RETRYABLE = (429, 500, 502, 503, 504)
    last_err = "알 수 없음"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code in _RETRYABLE and attempt < 2:
                time.sleep(1.5 * (attempt + 1))  # 점증 백오프
                continue
            return {"error": f"Gemini 호출 실패(HTTP {e.code})"}
        except Exception as e:  # noqa: BLE001 — URLError(DNS/네트워크) 등 일시 실패
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"error": f"Gemini 호출 실패: {last_err}"}
    if data is None:
        return {"error": "Gemini 호출 실패(재시도 소진)"}

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        return {"error": "Gemini 응답 파싱 실패"}
    if not text:
        return {"error": "Gemini 빈 응답"}
    return {"answer": text}


def answer_question(question):
    """질문을 현재 수집 데이터 컨텍스트와 함께 Gemini 에 보내 한국어 답변을 받는다.
    성공: {"answer": text}. 키 미설정/호출 실패: {"error": ...}."""
    context = _build_context()
    prompt = (f"{SYSTEM_PROMPT}\n\n[현재 수집 데이터]\n{context}\n\n"
              f"[질문]\n{question}")
    return _gemini_call(prompt, max_tokens=800)


# ── 인사이트 AI 설명(룰 findings → 우선순위·원인·조치) ────────────────
INSIGHT_PROMPT = (
    "너는 dataviz-prod 운영 모니터링 분석가다. 아래는 룰 엔진이 탐지한 '주목 신호' 목록이다. "
    "이 목록에 있는 사실만 근거로, 운영자가 지금 무엇을 먼저 봐야 하는지 한국어로 3~5문장으로 요약하라. "
    "우선순위(가장 급한 것 먼저), 가능한 원인, 권장 조치를 간단히 제시하라. "
    "목록에 없는 수치·리소스·원인을 지어내지 마라(환각 금지). "
    "불릿 없이 자연스러운 문단으로 작성하라.")

# 단일 워커 전제 모듈 캐시(findings 동일 시 Gemini 재호출 회피)
_INSIGHT_CACHE = {"key": None, "text": None}


def _findings_key(findings):
    return tuple((f.get("severity"), f.get("area"), f.get("title"),
                  f.get("evidence")) for f in findings)


def insight_comment(findings, summary=None):
    """룰 findings 를 Gemini 에 넘겨 한국어 종합 코멘트를 생성한다.
    findings 동일하면 캐시 반환. 키 미설정/호출 실패 시 None(프론트는 코멘트 생략)."""
    if not config.GEMINI_API_KEY:
        return None
    fkey = _findings_key(findings)
    if _INSIGHT_CACHE["key"] == fkey and _INSIGHT_CACHE["text"] is not None:
        return _INSIGHT_CACHE["text"]
    if not findings:
        text = "현재 특별히 주목할 이상 신호가 없습니다. 모든 지표가 정상 범위입니다."
        _INSIGHT_CACHE.update(key=fkey, text=text)
        return text
    lines = []
    for f in findings:
        lines.append(f"- [{f.get('severity')}] {f.get('area')} · "
                     f"{f.get('title')} ({f.get('evidence')})")
    prompt = f"{INSIGHT_PROMPT}\n\n[탐지된 신호]\n" + "\n".join(lines)
    res = _gemini_call(prompt, max_tokens=600)
    text = res.get("answer")
    if not text:
        return None  # 실패는 캐시하지 않음(다음 요청서 재시도)
    _INSIGHT_CACHE.update(key=fkey, text=text)
    return text
