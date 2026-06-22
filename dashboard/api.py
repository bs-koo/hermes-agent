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
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard import config, storage, chat, insights
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
    "host": config.HOST_INTERVAL,
    "cdn": config.CDN_INTERVAL,
}


def _series(raw):
    """시계열 [(epoch, val)] 또는 [[epoch, val]] 를 [{t, v}] 로 변환.
    JSON 직렬화/역직렬화를 거치면 튜플이 리스트가 되므로 둘 다 수용한다."""
    out = []
    for item in (raw or []):
        try:
            t, v = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        out.append({"t": t, "v": v})
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 기동: 스키마 보장 후 스케줄러 데몬 시작
    storage.init_db()
    _scheduler.start()
    yield
    # 종료: 스케줄러 중단 신호
    _scheduler.stop()


app = FastAPI(title="dataviz-prod 운영 대시보드", lifespan=lifespan)


@app.middleware("http")
async def _no_cache_static(request, call_next):
    # 앱 자산(html/dashboard.js/styles.css)은 no-store 로 캐시 자체를 차단해
    # 변경 후 항상 최신을 받게 한다. 벤더 번들(chart.umd.min.js)은 캐시 허용(불변).
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or (p.endswith((".js", ".css", ".html")) and not p.endswith("chart.umd.min.js")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 패널 1: 알람 ──────────────────────────────────────────────────────
# CloudWatch ComparisonOperator → 사람친화 기호
_CMP_SYMBOL = {
    "GreaterThanThreshold": ">",
    "GreaterThanOrEqualToThreshold": ">=",
    "LessThanThreshold": "<",
    "LessThanOrEqualToThreshold": "<=",
    "LessThanLowerOrGreaterThanUpperThreshold": "<lower or >upper",
    "GreaterThanUpperThreshold": "> upper",
    "LessThanLowerThreshold": "< lower",
}


def _build_alarm_item(r):
    """alarm_state 행을 API 아이템으로 변환한다. detail_json 을 파싱해 평탄화하고
    comparison 기호와 condition 조립 문자열을 만든다. detail 없으면 추가 필드 None."""
    item = {
        "name": r["alarm_name"],
        "state": r["state"],
        "state_updated": r["state_updated"],
        "last_transition": r["state_updated"],
        "reason": r["state_reason"],
        "description": None,
        "metric": None,
        "statistic": None,
        "period": None,
        "comparison": None,
        "threshold": None,
        "condition": None,
    }
    detail = None
    raw = r.get("detail_json")
    if raw:
        try:
            detail = json.loads(raw)
        except (TypeError, ValueError):
            detail = None
    if not detail:
        return item

    metric = detail.get("metric")
    statistic = detail.get("statistic")
    period = detail.get("period")
    comparison_raw = detail.get("comparison")
    comparison = _CMP_SYMBOL.get(comparison_raw, comparison_raw)
    threshold = detail.get("threshold")

    item.update({
        "description": detail.get("description"),
        "metric": metric,
        "statistic": statistic,
        "period": period,
        "comparison": comparison,
        "threshold": threshold,
        "reason": detail.get("reason") or item["reason"],
    })

    # condition 조립: "metric cmp threshold (statistic, period s)"
    if metric and comparison is not None and threshold is not None:
        cond = f"{metric} {comparison} {threshold}"
        extras = []
        if statistic:
            extras.append(str(statistic))
        if period:
            extras.append(f"{period}s")
        if extras:
            cond += f" ({', '.join(extras)})"
        item["condition"] = cond
    return item


@app.get("/api/alarms")
def api_alarms():
    rows = storage.get_alarms()
    if not rows:
        return {"empty": True}
    items = [_build_alarm_item(r) for r in rows]
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
        "health_hits": p.get("health_hits", 0),
        "total_all": p.get("total_all", p.get("total", 0)),
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


# ── 패널 4: DB(계정 전체 RDS 인스턴스) ───────────────────────────────
_DB_SERIES_KEYS = ("cpu_series", "mem_series", "dbload_series", "conn_series")


@app.get("/api/db")
def api_db():
    snap = storage.get_db()
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    raw_instances = p.get("instances") or []
    if not raw_instances:
        return {"empty": True}
    instances = []
    for it in raw_instances:
        # 요약(기존 10항목 + 추가 mem_free/swap/iops/dbload_cpu 등 + db_id)은 그대로,
        # 시계열 4종만 [{t,v}] 로 변환(프론트 차트 일관).
        d = {k: v for k, v in it.items() if k not in _DB_SERIES_KEYS}
        for sk in _DB_SERIES_KEYS:
            d[sk] = _series(it.get(sk))
        instances.append(d)
    return {
        "empty": False,
        "instances": instances,
        "primary_db_id": p.get("primary_db_id"),
        "collected_at": p.get("collected_at", snap.get("collected_at")),
    }


# ── 패널: EC2 호스트(계정 전체 인스턴스) ─────────────────────────────
@app.get("/api/host")
def api_host():
    snap = storage.get_host()
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    raw_instances = p.get("instances") or []
    if not raw_instances:
        return {"empty": True}
    instances = [{
        "instance_id": it.get("instance_id"),
        # 메타(describe_instances 권한 없으면 None — instance_id 만 유지, 회귀 없음)
        "private_ip": it.get("private_ip"),
        "instance_name": it.get("instance_name"),
        "instance_type": it.get("instance_type"),
        "state": it.get("state"),
        "cpu_avg": it.get("cpu_avg"),
        "cpu_max": it.get("cpu_max"),
        "net_in": it.get("net_in"),
        "net_out": it.get("net_out"),
        "ebs_read": it.get("ebs_read"),
        "ebs_write": it.get("ebs_write"),
        "credit_min": it.get("credit_min"),
        "status_failed": it.get("status_failed"),
        "cpu_series": _series(it.get("cpu_series")),        # 클릭 상세용 시계열 3종
        "net_in_series": _series(it.get("net_in_series")),
        "net_out_series": _series(it.get("net_out_series")),
    } for it in raw_instances]
    return {
        "empty": False,
        "instances": instances,
        "primary_instance_id": p.get("primary_instance_id"),
        "collected_at": p.get("collected_at", snap.get("collected_at")),
    }


# ── 패널: CloudFront CDN(계정 전체 배포) ─────────────────────────────
@app.get("/api/cdn")
def api_cdn():
    snap = storage.get_cdn()
    if snap is None:
        return {"empty": True}
    p = snap["payload"]
    raw_dists = p.get("distributions") or []
    if not raw_dists:
        return {"empty": True}
    distributions = [{
        "dist_id": d.get("dist_id"),
        "requests": d.get("requests"),
        "bytes_down": d.get("bytes_down"),  # bytes 원단위(프론트 변환)
        "bytes_up": d.get("bytes_up"),
        "err_4xx": d.get("err_4xx"),        # % 값
        "err_5xx": d.get("err_5xx"),
        "err_total": d.get("err_total"),
        "requests_series": _series(d.get("requests_series")),
        "err_total_series": _series(d.get("err_total_series")),
    } for d in raw_dists]
    return {
        "empty": False,
        "distributions": distributions,
        "collected_at": p.get("collected_at", snap.get("collected_at")),
    }


# ── 인사이트: 룰 탐지 findings + AI 종합 코멘트 ───────────────────────
@app.get("/api/insights")
def api_insights():
    data = insights.build_insights()
    try:
        data["ai_comment"] = chat.insight_comment(data["findings"], data["summary"])
    except Exception:  # noqa: BLE001 — AI 실패는 인사이트(룰) 표시를 막지 않음
        data["ai_comment"] = None
    return data


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
