# -*- coding: utf-8 -*-
"""EC2 호스트 메트릭 수집기(10분 주기).
aws_clients.cw 로 list_metrics 에서 계정 전체 InstanceId 를 발견(list_ec2_instances)하고
인스턴스별로 aggregations.ec2_metrics(최근 24h)를 호출해 요약 + cpu_series 를 모은 뒤
host_snapshot(id=1) 단일행에 {"instances":[...], ...} payload 로 교체 저장한다.
get_metric_data/list_metrics(read-only)만 사용한다(DescribeInstances 권한 불필요).
한 인스턴스의 메트릭 실패가 전체 수집을 막지 않도록 인스턴스별 try/except 로 격리한다."""
import sys
import json
import datetime

from dashboard import aws_clients, storage, aggregations, config
from dashboard.collectors import base


# 유효성 판정에서 제외할 비-메트릭 키(식별자/메타/시계열/플래그).
# 메타(IP/이름/타입/상태)는 메트릭 수집 성공과 무관하므로 빈 결과 판정에서 빼야
# "메트릭은 일시 빈데 메타만 있는" 경우 캐시를 덮어쓰지 않는다.
_NON_METRIC_KEYS = frozenset((
    "instance_id", "cpu_series", "net_in_series", "net_out_series", "error",
    "private_ip", "instance_name", "instance_type", "state",
))


def _instance_has_data(inst):
    """한 인스턴스 dict 가 유효한 '메트릭' 수집값을 담았는지 판정한다.
    error 표시이면 False. 시계열(cpu/net)이 있거나 요약 메트릭이 하나라도
    None 이 아니면 True. 메타 필드(IP/이름 등)는 판정에서 제외한다."""
    if inst.get("error"):
        return False
    if inst.get("cpu_series") or inst.get("net_in_series") or inst.get("net_out_series"):
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

    iids = aggregations.list_ec2_instances(cw)
    # 첫 수집 등 일시 빈 결과에서도 최소 1개는 시도하도록 config 기본값 폴백.
    if not iids and config.EC2_INSTANCE_ID:
        iids = [config.EC2_INSTANCE_ID]

    # 인스턴스 메타(IP/이름/타입/상태) — describe_instances 권한 없으면 {}(graceful).
    # 메타 조회 실패는 메트릭 수집/저장을 막지 않는다(instance_id 만 유지, 회귀 없음).
    try:
        meta = aggregations.ec2_instance_meta(aws_clients.ec2(), iids)
    except Exception as e:  # noqa: BLE001 — 클라이언트 생성 실패 등도 graceful
        sys.stderr.write(f"[collector:host] 인스턴스 메타 조회 실패(무시): {e}\n")
        meta = {}

    instances = []
    for iid in iids:
        try:
            inst = aggregations.ec2_metrics(cw, t0, t1, iid)
        except Exception as e:  # noqa: BLE001 — 한 인스턴스 실패가 전체를 막지 않게 격리
            sys.stderr.write(f"[collector:host] instance {iid} 메트릭 실패: {e}\n")
            inst = {
                "instance_id": iid,
                "cpu_avg": None, "cpu_max": None,
                "net_in": None, "net_out": None,
                "ebs_read": None, "ebs_write": None,
                "credit_min": None, "status_failed": None,
                "cpu_series": [],
                "error": True,
            }
        # 메타 병합(없으면 None — 권한 없거나 응답 누락 시 instance_id 만 유지).
        m = meta.get(iid) or {}
        inst["private_ip"] = m.get("private_ip")
        inst["instance_name"] = m.get("name")
        inst["instance_type"] = m.get("instance_type")
        inst["state"] = m.get("state")
        instances.append(inst)

    # 유효 데이터가 0(인스턴스 없음 또는 전부 error/빈)이면 이전 캐시 보존(저장 skip).
    if not any(_instance_has_data(it) for it in instances):
        sys.stderr.write("[collector:host] 유효 데이터 0 — 이전 스냅샷 보존(저장 skip)\n")
        return

    payload = {
        "instances": instances,
        "primary_instance_id": config.EC2_INSTANCE_ID,  # 프론트 강조용 대표 인스턴스
        "collected_at": t1,
    }
    storage.replace_host_snapshot(json.dumps(payload, ensure_ascii=False), t1)


def run(now):
    """EC2 호스트 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("host", lambda: _collect(now))
