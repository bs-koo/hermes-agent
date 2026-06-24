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

# 답변 말투·형식 강제 규칙(모든 답변 공통). 제공자(Gemini/기타) 무관하게 적용.
ANSWER_RULES = (
    "[답변 규칙 — 반드시 준수]\n"
    "1) 반드시 한국어로만 답한다.\n"
    "2) 항상 정중한 존댓말(~입니다/~합니다체)을 쓴다.\n"
    "3) 운영 엔지니어처럼 전문적이고 명확한 어조로 답한다. 군더더기·감탄사·이모지·과장은 쓰지 않는다.\n"
    "4) 제공된 '현재 수집 데이터'의 사실·수치만 근거로 답한다. 데이터에 없으면 지어내지 말고 "
    "'수집된 데이터에 없습니다'라고 답한다(추측 금지).\n"
    "5) 수치는 값·단위·기준 시점을 구체적으로 밝히고, 가능하면 근거가 된 지표를 함께 제시한다.\n"
    "6) 핵심부터 간결하게 답한다.")

SYSTEM_PROMPT = (
    "너는 dataviz-prod AWS 운영 모니터링과 데이터플랫폼파트 Dooray 주간업무, 사업부 업무 일정(Google Calendar)을 "
    "함께 보는 전문 보조다. "
    "아래 현재 수집 데이터(AWS 운영: 알람·가동률·DB 등 + Dooray 업무: 이번 주 과제·담당자·진행상태 + "
    "업무 일정: 다가오는 회의·일정)만 근거로 답한다. "
    "AWS 인프라든 업무 진행 현황이든 다가오는 일정이든 데이터에 있으면 답한다.\n" + ANSWER_RULES)


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


def _dooray_ctx():
    """Dooray '파트업무진행' 이번 주 업무 요약(프로젝트/담당자/상태/AI요약)."""
    snap = storage.get_dooray()
    if snap is None:
        return "Dooray 주간업무: 데이터 없음"
    p = snap.get("payload") or {}
    tasks = p.get("tasks") or []
    if not tasks:
        return "Dooray 주간업무: 데이터 없음"
    week = (p.get("current_week") or {}).get("name") or ""
    proj = p.get("project_name") or "파트업무진행"
    cnt = {"working": 0, "closed": 0, "registered": 0}
    for t in tasks:
        c = t.get("workflowClass") or "registered"
        cnt[c] = cnt.get(c, 0) + 1
    head = (f"Dooray 주간업무({proj} · {week} · 수집 "
            f"{_kst(snap.get('collected_at') or p.get('collected_at'))}):")
    lines = [head,
             f"  - 업무 {len(tasks)}건 (진행 {cnt['working']} · 완료 {cnt['closed']} · 할일 {cnt['registered']})"]
    by_tag = {}
    for t in tasks:
        for tag in (t.get("tags") or ["기타"]):
            by_tag.setdefault(tag, []).append(t)
    for tag, ts in by_tag.items():
        lines.append(f"  [{tag}]")
        for t in ts:
            subj = (t.get("subject") or "").strip()
            who = t.get("assignee") or "-"
            st = t.get("status") or ""
            lines.append(f"    - {subj} (담당 {who}, {st})")
            summ = (t.get("ai_summary") or "").strip()
            if summ:
                lines.append(f"      요약: {summ}")
    return "\n".join(lines)


def _cal_cat(title, kind):
    """일정 카테고리 분류(근태 vs 업무) — 채팅이 '오늘 휴가 누구' 등에 답하도록."""
    t = (title or "").replace(" ", "")
    if "오전반차" in t:
        return "오전반차"
    if "오후반차" in t:
        return "오후반차"
    if any(k in t for k in ("연차", "휴가", "반차", "월차", "경조")):
        return "연차/휴가"
    if any(k in t for k in ("외근", "출장", "파견")):
        return "외근/출장"
    return "근태" if kind == "leave" else "업무"


def _calendar_ctx():
    """본부 일정(Google Calendar) — 다가오는 일정 목록(근태·업무 카테고리 포함)."""
    snap = storage.get_gcal()
    if snap is None:
        return "본부 일정(Google Calendar): 데이터 없음"
    p = snap.get("payload") or {}
    evs = p.get("events") or []
    if not evs:
        return "본부 일정(Google Calendar): 다가오는 일정 없음"
    lines = [f"본부 일정(Google Calendar, 다가오는 {p.get('window_days', 14)}일 · 근태/업무 포함):"]
    for e in evs[:40]:
        loc = (" @" + e["location"]) if e.get("location") else ""
        when = _kst(e.get("start"))
        if e.get("all_day"):
            when = when[:10] + " (종일)"
        cat = _cal_cat(e.get("title"), e.get("kind"))
        lines.append(f"  - [{cat}] {when} {e.get('title') or ''}{loc}")
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
        _dooray_ctx(),
        _calendar_ctx(),
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
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                             # Gemini 2.5 Flash는 thinking 모델 — thinking 토큰이 출력 예산을
                             # 먹어 답변이 중간에 잘린다. 요약 작업엔 thinking 불필요 → 끔.
                             "thinkingConfig": {"thinkingBudget": 0}},
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


# ── 업무 요약(Dooray 업무 본문+코멘트 → 운영 보고용 2~3문장) ──────────
TASK_SUMMARY_PROMPT = (
    "너는 데이터플랫폼파트 업무 보조다. 아래 한 업무의 '업무 내용'과 '코멘트'를 "
    "운영자가 빠르게 파악하도록 2~3문장으로 요약하라. "
    "무엇을 했고/진행 중인지, 핵심 결과나 다음 단계가 있으면 포함하라. "
    "한국어 존댓말(~입니다체)·전문적이고 명확한 어조로 작성하라. "
    "내용에 없는 사실을 지어내지 마라(환각 금지). 감탄사·이모지·과장·불릿은 쓰지 마라.")


def summarize_task(subject, body, comments):
    """업무 본문+코멘트를 운영 보고용으로 요약. 키 미설정/내용 없음/실패 시 None."""
    if not config.GEMINI_API_KEY:
        return None
    parts = []
    b = (body or "").strip()
    if b:
        parts.append("[업무 내용]\n" + b)
    for c in (comments or [])[:12]:
        txt = (c.get("text") or "").strip()
        if txt:
            parts.append("[코멘트] " + txt)
    content = "\n\n".join(parts).strip()
    if not content:
        return None  # 본문·코멘트 둘 다 없으면 요약하지 않음
    prompt = (TASK_SUMMARY_PROMPT + "\n\n[업무 제목]\n" + (subject or "") +
              "\n\n[업무 내용·코멘트]\n" + content[:6000])
    res = _gemini_call(prompt, max_tokens=400)
    return res.get("answer")


# ── 인사이트 AI 설명(룰 findings → 우선순위·원인·조치) ────────────────
INSIGHT_PROMPT = (
    "너는 dataviz-prod 운영 모니터링 분석가다. 아래는 룰 엔진이 탐지한 '주목 신호' 목록이다. "
    "이 목록에 있는 사실만 근거로, 운영자가 지금 무엇을 먼저 봐야 하는지 "
    "한국어 존댓말(~입니다체)로, 전문적이고 명확한 어조로 3~5문장으로 요약하라. "
    "우선순위(가장 급한 것 먼저), 가능한 원인, 권장 조치를 간단히 제시하라. "
    "목록에 없는 수치·리소스·원인을 지어내지 마라(환각 금지). 감탄사·이모지·과장은 쓰지 마라. "
    "불릿 없이 자연스러운 문단으로 작성하라.")

# 단일 워커 전제 모듈 캐시(findings 동일 시 Gemini 재호출 회피)
_INSIGHT_CACHE = {"key": None, "text": None}


def _findings_key(findings):
    # 근거 수치(evidence)는 매 수집마다 미세 변동(예: 5xx 1.39→1.40%)하므로 키에서 제외한다.
    # '신호 집합'(심각도·영역·제목)이 바뀔 때만 재생성 → 메뉴 방문·수치 변동마다 Gemini 재호출 방지.
    return tuple(sorted(
        (f.get("severity") or "", f.get("area") or "", f.get("title") or "")
        for f in findings))


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
    res = _gemini_call(prompt, max_tokens=1024)
    text = res.get("answer")
    if not text:
        return None  # 실패는 캐시하지 않음(다음 요청서 재시도)
    _INSIGHT_CACHE.update(key=fkey, text=text)
    return text
