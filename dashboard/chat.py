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
# 스트리밍 엔드포인트(SSE) — 답변 토큰을 흘려보내 체감 지연을 줄인다.
GEMINI_STREAM_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                     "{model}:streamGenerateContent?alt=sse&key={key}")

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


def _fmt1(v, suffix=""):
    """숫자 → 소수1자리+단위, None/비숫자는 '-'."""
    if v is None:
        return "-"
    try:
        return f"{float(v):.1f}{suffix}"
    except (TypeError, ValueError):
        return "-"


def _db_ctx():
    snap = storage.get_db()
    if snap is None:
        return "DB(RDS): 데이터 없음"
    p = snap["payload"]
    insts = p.get("instances") or []
    if not insts:
        return "DB(RDS): 데이터 없음"
    primary = p.get("primary_db_id")
    lines = [f"DB(RDS {len(insts)}대, 수집 {_kst(snap.get('collected_at'))}):"]
    for it in insts:
        free = it.get("free_storage")
        free_s = f"{free / 1e9:.1f}GB" if isinstance(free, (int, float)) else "-"
        star = " (주 DB)" if it.get("db_id") == primary else ""
        lines.append(
            f"  - {it.get('db_id') or '?'}{star}: CPU 평균 {_fmt1(it.get('cpu_avg'), '%')}/"
            f"최대 {_fmt1(it.get('cpu_max'), '%')}, 연결 평균 {_fmt1(it.get('conn_avg'))}/"
            f"최대 {_fmt1(it.get('conn_max'))}, 여유공간 {free_s}")
    return "\n".join(lines)


def _host_ctx():
    """EC2 인스턴스(계정 전체) — 이름·IP·타입·CPU 평균/최대."""
    snap = storage.get_host()
    if snap is None:
        return "EC2 인스턴스: 데이터 없음"
    p = snap["payload"]
    insts = p.get("instances") or []
    if not insts:
        return "EC2 인스턴스: 데이터 없음"
    lines = [f"EC2 인스턴스({len(insts)}대, 수집 {_kst(snap.get('collected_at'))}):"]
    for it in insts:
        nm = it.get("instance_name") or it.get("instance_id") or "?"
        meta = " · ".join([x for x in [it.get("private_ip"), it.get("instance_type")] if x])
        lines.append(
            f"  - {nm}" + (f" ({meta})" if meta else "")
            + f": CPU 평균 {_fmt1(it.get('cpu_avg'), '%')}/최대 {_fmt1(it.get('cpu_max'), '%')}")
    return "\n".join(lines)


def _cdn_ctx():
    """CloudFront 배포 — 요청수·5xx 에러율."""
    snap = storage.get_cdn()
    if snap is None:
        return "CDN(CloudFront): 데이터 없음"
    p = snap["payload"]
    dists = p.get("distributions") or []
    if not dists:
        return "CDN(CloudFront): 데이터 없음"
    lines = [f"CDN(CloudFront {len(dists)}개 배포, 수집 {_kst(snap.get('collected_at'))}):"]
    for d in dists:
        req = d.get("requests")
        lines.append(
            f"  - {d.get('dist_id')}: 요청수 {req if req is not None else '-'}, "
            f"5xx 에러율 {_fmt1(d.get('err_5xx'), '%')}")
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
        if not (config.GCAL_ICS_URL or config.GCAL_ATTEND_ICS_URL):
            return ("본부 일정(Google Calendar): 아직 연동되지 않음(iCal 주소 미설정). "
                    "일정·근태(휴가/반차)를 확인할 수 없음 — 관리자가 연동해야 함.")
        return "본부 일정(Google Calendar): 데이터 없음(아직 수집 전)"
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


# ── 컨텍스트 섹션 라우팅(질문 키워드 → 관련 섹션만 선택) ───────────────
# 질문에 맞는 섹션만 Gemini 에 넣어 입력 토큰·추론 시간을 줄인다. 매칭이 하나도 없거나
# '전체/현황' 류 광역 질문이면 전체를 넣어 데이터 누락을 막는다(품질 우선).
_CTX_FUNCS = {
    "alarms": _alarms_ctx, "uptime": _uptime_ctx, "host": _host_ctx,
    "cdn": _cdn_ctx, "db": _db_ctx, "traffic": _traffic_ctx,
    "dooray": _dooray_ctx, "calendar": _calendar_ctx,
}
# 전체 섹션 순서(매칭 0 / 광역 질문 시 그대로 사용).
_CTX_ALL = ("alarms", "uptime", "host", "cdn", "db", "traffic", "dooray", "calendar")
# 무슨 질문이든 항상 포함(짧고 운영 필수 — 전반 상태 인지).
_CTX_ALWAYS = ("alarms", "uptime")
# 질문 키워드(소문자 비교) → 섹션. 한글은 lower 영향 없음, 영문 약어는 소문자로.
_CTX_KEYWORDS = {
    "alarms": ["알람", "경보", "alarm"],
    "uptime": ["가동", "가용", "uptime", "응답", "지연", "다운", "장애", "느리"],
    "host": ["ec2", "호스트", "서버", "인스턴스", "cpu", "크레딧", "메모리"],
    "cdn": ["cdn", "cloudfront", "캐시", "엣지", "5xx", "4xx", "에러율"],
    "db": ["db", "디비", "rds", "데이터베이스", "디스크", "연결", "스토리지", "저장공간"],
    "traffic": ["트래픽", "요청", "사용자", "방문", "접속", "traffic", "스캐너", "봇"],
    "dooray": ["업무", "두레이", "dooray", "과제", "진행", "주간", "담당", "작업"],
    "calendar": ["일정", "캘린더", "휴가", "반차", "연차", "회의", "근태", "외근", "출장"],
}
# 전체를 강제하는 광역 질문 신호.
_CTX_BROAD = ["전체", "전반", "요약", "현황", "상황", "모두", "전부", "overview", "summary"]


def _select_ctx_keys(question):
    """질문에서 포함할 컨텍스트 섹션 키 목록을 고른다(원래 순서 유지)."""
    q = (question or "").lower()
    if q and not any(b in q for b in _CTX_BROAD):
        matched = {k for k, kws in _CTX_KEYWORDS.items() if any(w in q for w in kws)}
        if matched:
            sel = matched | set(_CTX_ALWAYS)
            return [k for k in _CTX_ALL if k in sel]
    return list(_CTX_ALL)  # 광역 질문·키워드 미매칭·질문 없음 → 전체


def _build_context(question=None):
    """현재 수집 데이터 요약 컨텍스트(빈 DB 에서도 안전).
    question 이 주어지면 관련 섹션만 추려 토큰을 절감한다(없으면 전체)."""
    now_kst = _kst(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
    parts = [f"[현재 시각(KST): {now_kst}]"]
    for k in _select_ctx_keys(question):
        parts.append(_CTX_FUNCS[k]())
    return "\n\n".join(parts)


# ── Gemini REST 호출 ──────────────────────────────────────────────────
# 무료 사용량 한도(429)는 기술 코드 대신 사용자 친화 안내로 바꾼다. 응답 본문으로
# 일일(PerDay) 한도와 분당(PerMinute) 한도를 구분해 회복 시점을 정확히 안내한다.
_RATE_LIMIT_DAY = ("오늘 무료 사용량(일일 한도)을 모두 썼어요. 한국시각 자정 무렵 리셋되며, "
                   "더 자주 쓰려면 모델 변경이나 유료 전환이 필요해요.")
_RATE_LIMIT_MIN = "요청이 잠시 몰렸어요. 30초쯤 후 다시 시도해 주세요(분당 한도)."


def _http_err_msg(e):
    """HTTPError → 사용자 친화 메시지. 429 는 응답 본문으로 일일/분당 한도를 구분한다.
    주의: 429 응답은 PerDay·PerMinute violation 을 함께 나열하므로 단순 'PerDay' 문자열
    매칭은 분당 한도를 일일로 오인한다(2.0-flash 는 분당만 걸려도 PerDay 가 목록에 뜬다).
    PerDay 에 한도값(quotaValue)이 실제로 명시될 때만 '오늘 소진'으로 판단한다."""
    code = getattr(e, "code", None)
    if code == 429:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — 본문 못 읽으면 분당 안내로 폴백
            body = ""
        day_hit = False
        try:
            err = json.loads(body).get("error", {})
            for d in err.get("details", []):
                if "QuotaFailure" in d.get("@type", ""):
                    for v in d.get("violations", []):
                        if "PerDay" in (v.get("quotaId") or "") and v.get("quotaValue"):
                            day_hit = True
        except Exception:  # noqa: BLE001 — 파싱 실패 시 보수적 폴백
            day_hit = "PerDay" in body
        return _RATE_LIMIT_DAY if day_hit else _RATE_LIMIT_MIN
    return f"Gemini 호출 실패(HTTP {code})"


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
    _RETRYABLE = (500, 502, 503, 504)  # 429 제외: 일일 한도는 재시도해도 안 풀리고 한도만 더 소진
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
            return {"error": _http_err_msg(e)}
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
    context = _build_context(question)
    prompt = (f"{SYSTEM_PROMPT}\n\n[현재 수집 데이터]\n{context}\n\n"
              f"[질문]\n{question}")
    return _gemini_call(prompt, max_tokens=800)


# ── Gemini SSE 스트리밍(답변 조각을 순서대로 흘려보냄) ────────────────
def _gemini_stream(prompt, max_tokens=800, temperature=0.2):
    """Gemini streamGenerateContent(SSE)를 호출해 텍스트 조각을 순서대로 yield 한다.
    각 yield 는 {"text": 조각} 또는 {"error": 메시지}. 예외를 밖으로 던지지 않는다
    (라우트가 SSE 한가운데서 끊기지 않도록). 단일 워커 전제 동기 호출."""
    key = config.GEMINI_API_KEY
    if not key:
        yield {"error": "GEMINI_API_KEY 미설정"}
        return
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                             # 비스트리밍과 동일하게 thinking 끔(출력 예산 절약·지연 단축).
                             "thinkingConfig": {"thinkingBudget": 0}},
    }, ensure_ascii=False).encode("utf-8")
    url = GEMINI_STREAM_URL.format(model=config.GEMINI_MODEL, key=key)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"})

    # 연결(urlopen)까지는 _gemini_call 과 동일하게 재시도한다 — Docker DNS 일시 실패
    # (Temporary failure in name resolution)·5xx·네트워크 흔들림을 흡수. 한 번 열린
    # 뒤 스트림 도중 끊김은 받은 만큼만 내보내고 종료(중간 재시도는 안 함).
    _RETRYABLE = (500, 502, 503, 504)  # 429 제외: 일일 한도는 재시도해도 안 풀리고 한도만 더 소진
    resp = None
    last_err = "알 수 없음"
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            break
        except urllib.error.HTTPError as e:
            if e.code in _RETRYABLE and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            yield {"error": _http_err_msg(e)}
            return
        except Exception as e:  # noqa: BLE001 — URLError(DNS/네트워크) 등 일시 실패
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            yield {"error": f"Gemini 호출 실패: {last_err}"}
            return
    if resp is None:
        yield {"error": "Gemini 호출 실패(재시도 소진)"}
        return

    try:
        with resp as r:
            # SSE 는 줄 단위 "data: {json}". urllib 응답은 줄 단위로 순회 가능.
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    j = json.loads(payload)
                    txt = j["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError, ValueError):
                    continue  # 메타 프레임(끝맺음·안전등급 등)은 건너뜀
                if txt:
                    yield {"text": txt}
    except Exception as e:  # noqa: BLE001 — 스트림 도중 끊김: 받은 만큼만 종료
        yield {"error": f"Gemini 스트림 중단: {e}"}


def answer_question_stream(question):
    """질문+컨텍스트로 Gemini 스트리밍 답변 조각을 순서대로 yield.
    각 항목은 {"text": 조각} 또는 {"error": 메시지}."""
    context = _build_context(question)
    prompt = (f"{SYSTEM_PROMPT}\n\n[현재 수집 데이터]\n{context}\n\n"
              f"[질문]\n{question}")
    yield from _gemini_stream(prompt, max_tokens=800)


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
