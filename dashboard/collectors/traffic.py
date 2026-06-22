# -*- coding: utf-8 -*-
"""트래픽/품질/시간대 수집기(10분 주기).
기간 1/7/30일 각각 Logs Insights 쿼리(weekly_insight.py 와 동일 쿼리 셋)를 실행하고
aggregations.aggregate_traffic + aggregate_traffic_hourly 로 집계해
period 별 traffic_snapshot 1행으로 교체 저장한다(payload JSON).
쿼리 셋은 weekly_insight.py 의 즉시실행 영역(L188-242)을 그대로 따른다."""
import json
import datetime

from dashboard import aws_clients, storage, aggregations
from dashboard.collectors import base

PERIODS = (1, 7, 30)


def _collect(now):
    logs = aws_clients.logs()
    t1 = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)

    for days in PERIODS:
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


def run(now):
    """트래픽 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("traffic", lambda: _collect(now))
