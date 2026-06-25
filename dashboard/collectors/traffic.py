# -*- coding: utf-8 -*-
"""트래픽/품질/시간대 수집기(스케줄러는 10분마다 호출).
기간 1/7/30일 각각 Logs Insights 쿼리(weekly_insight.py 와 동일 쿼리 셋)를 실행하고
aggregations.aggregate_traffic + aggregate_traffic_hourly 로 집계해
period 별 traffic_snapshot 1행으로 교체 저장한다(payload JSON).
쿼리 셋은 weekly_insight.py 의 즉시실행 영역(L188-242)을 그대로 따른다.

비용 최적화: Logs Insights 는 스캔한 로그량(GB)당 과금이라, 긴 기간을 10분마다
풀스캔하면 낭비다(7/30일 통계는 10분 사이 의미 있게 변하지 않음). 따라서 기간별
재수집 주기를 차등화한다 — 1일은 매 호출(실시간성), 7일은 1시간, 30일은 6시간.
스케줄러는 여전히 10분마다 run 을 부르지만, 각 기간은 자기 주기가 됐을 때만
재수집하고 그 외엔 기존 traffic_snapshot 을 그대로 둔다."""
import json
import datetime

from dashboard import aws_clients, storage, aggregations
from dashboard.collectors import base

PERIODS = (1, 7, 30)

# 기간별 재수집 최소 간격(초). 1일=10분 / 7일=1시간 / 30일=6시간.
PERIOD_INTERVALS = {1: 600, 7: 3600, 30: 6 * 3600}
# 단일 워커 전제 모듈 상태(insight 캐시와 동일 패턴) — 기간별 마지막 수집 epoch.
# 컨테이너 재기동 시 0 으로 리셋 → 첫 호출에 3기간 모두 수집(초기 데이터 확보).
_last_collect = {1: 0, 7: 0, 30: 0}


def _collect(now):
    logs = aws_clients.logs()
    t1 = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)

    for days in PERIODS:
        # 이 기간의 재수집 주기가 아직 안 됐으면 건너뛴다(기존 스냅샷 유지 → Logs 스캔 절감).
        if t1 - _last_collect[days] < PERIOD_INTERVALS[days]:
            continue
        t0 = t1 - days * 24 * 3600

        # weekly_insight.py 와 동일한 쿼리 셋(5종)
        raw = {
            "count": aggregations.query_logs_insights(
                logs, "filter ispresent(url) | stats count(*) as c", t0, t1),
            "urls": aggregations.query_logs_insights(
                logs, "filter ispresent(url) | stats count(*) as hits by method, url "
                      "| sort hits desc | limit 300", t0, t1),
            "status": aggregations.query_logs_insights(
                logs, "filter ispresent(status) | stats count(*) as hits by status "
                      "| sort hits desc", t0, t1),
            "err": aggregations.query_logs_insights(
                logs, "filter status >= 400 | stats count(*) as hits by status, url "
                      "| sort hits desc | limit 40", t0, t1),
            "users": aggregations.query_logs_insights(
                logs, "filter ispresent(xff) | stats count(*) as hits by xff "
                      "| sort hits desc | limit 1000", t0, t1),
        }
        agg = aggregations.aggregate_traffic(raw)
        hourly = aggregations.aggregate_traffic_hourly(logs, t0, t1)
        agg["hourly"] = [{"t": h, "count": c} for h, c in hourly]

        payload = json.dumps(agg, ensure_ascii=False)
        storage.replace_traffic_snapshot(days, payload, t1)
        _last_collect[days] = t1  # 이 기간 수집 성공 → 다음 주기까지 재스캔 생략


def run(now):
    """트래픽 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("traffic", lambda: _collect(now))
