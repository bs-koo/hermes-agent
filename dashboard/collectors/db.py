# -*- coding: utf-8 -*-
"""DB(RDS) 성능 수집기(10분 주기).
aws_clients.cw 로 aggregations.rds_perf(최근 24h)를 호출해 FR-13 10항목을 집계하고
db_snapshot(id=1) 단일행으로 교체 저장한다(payload JSON)."""
import json
import datetime

from dashboard import aws_clients, storage, aggregations, config
from dashboard.collectors import base


def _collect(now):
    cw = aws_clients.cw()
    t1 = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)
    t0 = t1 - 24 * 3600

    payload = aggregations.rds_perf(cw, t0, t1, dbid=config.DBID)
    storage.replace_db_snapshot(json.dumps(payload, ensure_ascii=False), t1)


def run(now):
    """DB 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("db", lambda: _collect(now))
