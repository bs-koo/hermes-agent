# -*- coding: utf-8 -*-
"""DB(RDS) 성능 수집기(10분 주기).
aws_clients.cw 로 계정 전체 RDS 인스턴스를 발견(list_rds_instances)하고 인스턴스별로
aggregations.rds_extended(최근 24h)를 호출해 기존 10항목 + 무료 심화 + 시계열을 집계해
db_snapshot(id=1) 단일행에 {"instances":[...], ...} payload 로 교체 저장한다(EC2 패턴 동일).
rds_extended 는 내부에서 rds_perf 를 재사용하므로 인스턴스별 요약 키는 회귀 없이 유지된다.
한 인스턴스 실패가 전체를 막지 않게 격리하고, 유효 데이터 0이면 캐시 보존(저장 skip)."""
import sys
import json
import datetime

from dashboard import aws_clients, storage, aggregations, config
from dashboard.collectors import base

# 유효성 판정에서 시계열로 취급하는 키(비어있지 않으면 유효).
_SERIES_KEYS = ("cpu_series", "mem_series", "dbload_series", "conn_series")
# 유효성 판정에서 제외할 비-메트릭 키(식별자/시계열/플래그).
_NON_METRIC_KEYS = frozenset(("db_id",) + _SERIES_KEYS + ("error",))


def _instance_has_data(inst):
    """한 RDS 인스턴스 dict 가 유효한 메트릭 수집값을 담았는지 판정한다.
    error 표시이면 False. 시계열 중 하나라도 비지 않거나, 요약값 하나라도 None 이
    아니면 True. 식별자/시계열/플래그는 판정에서 제외한다."""
    if inst.get("error"):
        return False
    for k in _SERIES_KEYS:
        if inst.get(k):
            return True
    for k, v in inst.items():
        if k in _NON_METRIC_KEYS:
            continue
        if v is not None:
            return True
    return False


def _collect(now):
    cw = aws_clients.cw()
    t1 = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)
    t0 = t1 - 24 * 3600

    dbids = aggregations.list_rds_instances(cw)
    # 첫 수집 등 일시 빈 결과에서도 최소 1개는 시도하도록 config 기본값 폴백.
    if not dbids and config.DBID:
        dbids = [config.DBID]

    instances = []
    for dbid in dbids:
        try:
            inst = aggregations.rds_extended(cw, t0, t1, dbid=dbid)
        except Exception as e:  # noqa: BLE001 — 한 인스턴스 실패가 전체를 막지 않게 격리
            sys.stderr.write(f"[collector:db] instance {dbid} 메트릭 실패: {e}\n")
            inst = {"error": True}
        inst["db_id"] = dbid
        instances.append(inst)

    # 유효 데이터가 0(인스턴스 없음 또는 전부 error/빈)이면 이전 캐시 보존(저장 skip).
    if not any(_instance_has_data(it) for it in instances):
        sys.stderr.write("[collector:db] 유효 데이터 0 — 이전 스냅샷 보존(저장 skip)\n")
        return

    payload = {
        "instances": instances,
        "primary_db_id": config.DBID,  # 프론트 강조용 대표 인스턴스
        "collected_at": t1,
    }
    storage.replace_db_snapshot(json.dumps(payload, ensure_ascii=False), t1)


def run(now):
    """DB 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("db", lambda: _collect(now))
