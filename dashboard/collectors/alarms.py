# -*- coding: utf-8 -*-
"""알람 수집기(5분 주기).
daily_digest.fetch_alarms 를 import 재사용해 CloudWatch 알람을 조회하고,
alarm_state(스냅샷) upsert + alarm_history(전환 이력) 멱등 기록한다.
boto3 클라이언트는 daily_digest 가 자체 생성하므로 AWS_PROFILE/AWS_REGION env 에 의존한다."""
import datetime

from daily_digest import fetch_alarms
from dashboard import storage
from dashboard.collectors import base


def _to_epoch(v):
    """StateUpdatedTimestamp 를 epoch(int)로 변환. boto3 는 보통 aware datetime 을 준다."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return int(v.timestamp())
    # 혹시 숫자/문자열로 오면 관용 처리
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _collect(now):
    alarms = fetch_alarms()
    collected_at = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)

    state_rows = []
    for a in alarms:
        name = a.get("AlarmName")
        if not name:
            continue
        state = a.get("StateValue", "")
        reason = a.get("StateReason")
        su = _to_epoch(a.get("StateUpdatedTimestamp"))
        state_rows.append({
            "alarm_name": name,
            "state": state,
            "state_reason": reason,
            "state_updated": su,
            "collected_at": collected_at,
        })

    storage.upsert_alarm_state(state_rows)

    # 전환 이력: (name, state, state_updated) 멱등 기록
    for row in state_rows:
        if row["state_updated"] is not None and row["state"]:
            storage.insert_alarm_history_if_changed(
                row["alarm_name"], row["state"], row["state_updated"])


def run(now):
    """알람 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("alarms", lambda: _collect(now))
