# -*- coding: utf-8 -*-
"""FastAPI 애플리케이션 — 패널 데이터 API + 정적 프론트 서빙.

엔드포인트는 전부 SQLite(storage)만 읽고 AWS 를 호출하지 않는다(QE-1: 3초 내 응답).
AWS 수집은 백그라운드 Scheduler 데몬이 전담한다.

# --workers 1 전제: 멀티워커 시 lifespan 이 워커마다 실행 → 스케줄러 중복 기동
#   → Logs Insights 중복 발사(BR-1 위반)·SQLite 락. 반드시 uvicorn --workers 1.

실행:
  uvicorn dashboard.api:app --host 0.0.0.0 --port 8080 --workers 1
"""
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard import config, storage, chat
from dashboard.scheduler import Scheduler

_scheduler = Scheduler()

# 허용 기간(일). 쿼리스트링은 문자열로 오므로 int 로 받아 화이트리스트 클램프한다
# (Literal[int] 는 쿼리 문자열 "7" 을 coerce 하지 못해 422 가 난다 — 실기동에서 확인).
_PERIODS = (1, 7, 30)


def _clamp_period(period):
    return period if period in _PERIODS else 7

# 잡 주기(초) — /api/meta stale 판정에 사용(주기 × 2 초과 시 stale)
_JOB_INTERVALS = {
    "alarms": config.ALARM_INTERVAL,
    "uptime": config.UPTIME_INTERVAL,
    "traffic": config.TRAFFIC_INTERVAL,
    "db": config.DB_INTERVAL,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 기동: 스키마 보장 후 스케줄러 데몬 시작
    storage.init_db()
    _scheduler.start()
    yield
    # 종료: 스케줄러 중단 신호
    _scheduler.stop()


app = FastAPI(title="dataviz-prod 운영 대시보드", lifespan=lifespan)


# ── 패널 1: 알람 ──────────────────────────────────────────────────────
@app.get("/api/alarms")
def api_alarms():
    rows = storage.get_alarms()
    if not rows:
        return {"empty": True}
    items = [{
        "name": r["alarm_name"],
        "state": r["state"],
        "state_updated": r["state_updated"],
        "last_transition": r["state_updated"],
        "reason": r["state_reason"],
    } for r in rows]
    n_alarm = sum(1 for r in rows if r["state"] == "ALARM")
    return {
        "empty": False,
        "items": items,
        "summary": {"total": len(rows), "alarm": n_alarm},
    }


@app.get("/api/alarms/{name}/history")
def api_alarm_history(name: str):
    rows = storage.get_alarm_history(name)
    if not rows:
        return {"empty": True, "items": []}
    items = [{"state": r["state"], "state_updated": r["state_updated"]} for r in rows]
    return {"empty": False, "items": items}


# ── 패널 2: 가동률 ────────────────────────────────────────────────────
@app.get("/api/uptime")
def api_uptime(period: int = Query(7)):
    period = _clamp_period(period)
    rows = storage.get_uptime(period)
    if not rows:
        return {"empty": True}
    series = {}
    for r in rows:
        ep = r["endpoint"]
        tot = r["total_count"] or 0
        ok = r["ok_count"] or 0
        pct = (ok / tot * 100) if tot else None
        series.setdefault(ep, []).append({
            "t": r["bucket_start"],
            "pct": pct,
            "avg": r["ms_avg"],
            "p95": r["ms_p95"],
        })
    # 최근 24h 요약(엔드포인트별 ok/tot 합산)
    cutoff = int(time.time()) - 24 * 3600
    summary = {}
    for r in rows:
        if r["bucket_start"] < cutoff:
            continue
        ep = r["endpoint"]
        s = summary.setdefault(ep, {"ok": 0, "tot": 0})
        s["ok"] += r["ok_count"] or 0
        s["tot"] += r["total_count"] or 0
    summary24h = {ep: {
        "ok": s["ok"], "tot": s["tot"],
        "pct": (s["ok"] / s["tot"] * 100) if s["tot"] else None,
    } for ep, s in summary.items()}
    return {"empty": False, "series": series, "summary24h": summary24h}


# ── 패널 3: 트래픽 ────────────────────────────────────────────────────
@app.get("/api/traffic")
def api_traffic(period: int = Query(7)):
    period = _clamp_period(period)
    snap = storage.get_traffic(period)
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    return {
        "empty": False,
        "top_ep": p.get("top_ep", []),
        "n_users": p.get("n_users", 0),
        "total": p.get("total", 0),
        "scanner_hits": p.get("scanner_hits", 0),
        "hourly": p.get("hourly", []),
    }


# ── 패널 4: 품질(상태코드/에러) — traffic_snapshot 에서 추출 ──────────
@app.get("/api/quality")
def api_quality(period: int = Query(7)):
    period = _clamp_period(period)
    snap = storage.get_traffic(period)
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    return {
        "empty": False,
        "buckets": p.get("buckets", {}),
        "top_err": p.get("top_err", []),
    }


# ── 패널 4: DB ────────────────────────────────────────────────────────
@app.get("/api/db")
def api_db():
    snap = storage.get_db()
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    out = {"empty": False}
    out.update(p)
    return out


# ── 상단: 수집 메타(stale 배너) ──────────────────────────────────────
@app.get("/api/meta")
def api_meta():
    metas = storage.get_collect_meta()
    now = int(time.time())
    jobs = []
    stale = False
    last_ok_at = None
    for m in metas:
        job = m["job"]
        ok_at = m["last_ok_at"]
        interval = _JOB_INTERVALS.get(job)
        job_stale = False
        if interval is not None:
            # 최근 last_ok_at 이 주기×2 를 초과(없으면 stale)면 true
            if ok_at is None or (now - ok_at) > interval * 2:
                job_stale = True
        if job_stale:
            stale = True
        if ok_at is not None and (last_ok_at is None or ok_at > last_ok_at):
            last_ok_at = ok_at
        jobs.append({
            "job": job,
            "last_ok_at": ok_at,
            "last_run_at": m["last_run_at"],
            "last_status": m["last_status"],
            "last_error": ("수집 오류" if m["last_error"] else None),
            "stale": job_stale,
        })
    return {"last_ok_at": last_ok_at, "jobs": jobs, "stale": stale}


# ── 채팅 Q&A: SQLite 캐시 컨텍스트 + Gemini ─────────────────────────
# 단일 워커 전제라 동기 호출 허용(Gemini 블로킹이나 워커 1개).
_MAX_QUESTION_LEN = 1000


class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
def api_chat(payload: ChatRequest):
    q = (payload.question or "").strip()
    if not q:
        return {"error": "질문이 비었습니다"}
    if len(q) > _MAX_QUESTION_LEN:
        return {"error": f"질문이 너무 깁니다(최대 {_MAX_QUESTION_LEN}자)"}
    return chat.answer_question(q)


# ── 정적 프론트(/): 디렉토리가 있을 때만 마운트 ──────────────────────
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
