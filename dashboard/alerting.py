# -*- coding: utf-8 -*-
"""인사이트 → Google Chat 푸시(주의 신호만, 평소 조용).

대시보드가 보는 운영 신호(EC2·RDS·CDN·트래픽·CloudWatch 알람)를 룰 엔진(insights)으로
평가해, critical(위험)·warning(주의)가 **새로 뜰 때만** Google Chat 으로 알리고, **해소되면**
정상화 알림을 1회 보낸다. 같은 신호가 계속 떠 있는 동안은 다시 보내지 않는다(스팸 방지).

상태는 storage.alert_state 에 보관(단일 워커 전제). 발송 성공 시에만 상태를 갱신해
일시적 발송 실패가 알림 누락으로 이어지지 않게 한다(다음 주기 재시도).

의존성 추가 금지 — urllib 로 webhook POST(기존 alarm_to_gchat.py 와 동일 패턴).
GCHAT_WEBHOOK 미설정이면 아무것도 하지 않는다(빈 상태)."""
import json
import urllib.request
import urllib.error

from dashboard import config, storage, insights
from dashboard.collectors import base

# 푸시 대상 심각도(info 는 제외 — 데이터부족 등 노이즈).
_PUSH_SEVERITIES = ("critical", "warning")
_SEV_KR = {"critical": "🔴 위험", "warning": "🟠 주의"}


def _alert_key(f):
    """신호 동일성 키 — 심각도·영역·제목(근거 수치는 미세 변동하므로 제외)."""
    return "|".join([f.get("severity") or "", f.get("area") or "", f.get("title") or ""])


def _send_gchat(text):
    """Google Chat 웹훅으로 텍스트 발송. 성공(2xx) True, 실패 False."""
    if not config.GCHAT_WEBHOOK:
        return False
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        config.GCHAT_WEBHOOK, data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001 — 네트워크/HTTP 실패는 다음 주기 재시도
        return False


def _fmt_new(findings):
    lines = ["🔔 *dataviz-prod 운영 신호* — 새 주의가 감지됐어요"]
    for f in findings:
        lines.append(f"{_SEV_KR.get(f.get('severity'), '')} [{f.get('area')}] {f.get('title')}")
        ev = f.get("evidence")
        if ev:
            lines.append(f"    └ {ev}")
    lines.append("\n자세한 원인·조치는 운영 대시보드 인사이트에서 확인하세요.")
    return "\n".join(lines)


def _fmt_gone(alerts):
    lines = ["✅ *정상화* — 주의 신호가 해소됐어요"]
    for a in alerts:
        lines.append(f"  [{a.get('area')}] {a.get('title')}")
    return "\n".join(lines)


def _push(now):
    """현재 인사이트를 평가해 신규/해소 신호를 Chat 으로 알린다."""
    if not config.GCHAT_WEBHOOK:
        return  # 웹훅 미설정 → 조용히 통과(빈 상태)

    findings = [f for f in insights.build_findings()
                if f.get("severity") in _PUSH_SEVERITIES]
    cur = {}
    for f in findings:
        cur.setdefault(_alert_key(f), f)  # 중복 키는 첫 항목 유지
    prev = {a["alert_key"]: a for a in storage.get_active_alerts()}

    new_keys = [k for k in cur if k not in prev]
    gone_keys = [k for k in prev if k not in cur]

    # 신규 발생 — 발송 성공 시에만 활성 상태로 기록.
    if new_keys:
        if _send_gchat(_fmt_new([cur[k] for k in new_keys])):
            for k in new_keys:
                f = cur[k]
                storage.upsert_active_alert(
                    k, f.get("severity"), f.get("area"), f.get("title"),
                    f.get("evidence"), int(now))

    # 해소 — 발송 성공 시에만 활성 목록에서 제거.
    if gone_keys:
        if _send_gchat(_fmt_gone([prev[k] for k in gone_keys])):
            storage.delete_active_alerts(gone_keys)


def run(now):
    """알림 푸시 1회(base.run_job 으로 예외 격리)."""
    return base.run_job("alerts", lambda: _push(now))
