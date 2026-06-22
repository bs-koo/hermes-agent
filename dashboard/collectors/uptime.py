# -*- coding: utf-8 -*-
"""가동률 수집기(5분 주기).
config.UPTIME_CSV(uptime_ping.py 가 쌓은 CSV)를 직접 읽어 보유 전 구간을
1시간 버킷(bucket_start = epoch - epoch % 3600)으로 endpoint 별 집계한다.
집계 항목: ok_count, total_count, ms_avg, ms_p95.
CSV 파싱은 daily_digest.uptime_24h 의 컬럼/예외 무시 패턴을 참조한다.
p95 는 numpy 없이 정렬 인덱스로 계산(의존성 최소화)."""
import csv

from dashboard import config, storage
from dashboard.collectors import base

BUCKET = 3600


def _p95(values):
    """정렬 인덱스 기반 p95. weekly_insight.py 의 p95 와 동일 규칙."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def _collect(now):
    path = config.UPTIME_CSV
    # (endpoint, bucket_start) → {ok, tot, ms:[...]}
    agg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    epoch = int(r["epoch"])
                    ep = r["endpoint"]
                    bucket_start = epoch - epoch % BUCKET
                    d = agg.setdefault((ep, bucket_start), {"ok": 0, "tot": 0, "ms": []})
                    d["tot"] += 1
                    d["ok"] += 1 if r["ok"] == "1" else 0
                    try:
                        d["ms"].append(float(r["ms"]))
                    except (TypeError, ValueError):
                        pass
                except Exception:
                    continue
    except FileNotFoundError:
        # CSV 가 아직 없으면 빈 집계로 정상 처리(빈 데이터 정상 취급)
        return

    rows = []
    for (ep, bucket_start), d in agg.items():
        ms = d["ms"]
        rows.append({
            "endpoint": ep,
            "bucket_start": bucket_start,
            "ok_count": d["ok"],
            "total_count": d["tot"],
            "ms_avg": (sum(ms) / len(ms)) if ms else None,
            "ms_p95": _p95(ms),
        })
    storage.append_uptime_buckets(rows)


def run(now):
    """가동률 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("uptime", lambda: _collect(now))
