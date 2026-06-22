# -*- coding: utf-8 -*-
"""운영 인사이트 룰 엔진 — SQLite 스냅샷을 스캔해 '주목할 신호(finding)'를 만든다.

결정적 룰(임계/상태/추세)만 담당한다. 자연어 설명(우선순위·원인·조치)은
chat.insight_comment 가 findings 를 받아 별도로 붙인다(하이브리드).
모든 수치는 storage 가 적재한 스냅샷에서만 읽는다(AWS 미호출 — api 와 동일 원칙)."""
import time

from dashboard import storage

# ── 임계 상수(한 곳에서 관리) ─────────────────────────────────────────
CPU_CRIT = 80.0          # 지속(평균) 고부하 → critical
CPU_PEAK = 90.0          # 순간(최대) 피크 → warning
CREDIT_LOW = 20.0        # t계열 CPU 크레딧 소진 경고
RDS_STORAGE_CRIT_GB = 5.0
RDS_STORAGE_WARN_GB = 15.0
RDS_CONN_WARN = 80.0     # 연결수(절대) 경고 임계(클래스별 max 미상 → 보수적 절대값)
CDN_5XX_CRIT = 1.0       # CDN 5xx 에러율(%) critical
CDN_4XX_WARN = 5.0       # CDN 4xx 에러율(%) warning
CDN_TOTAL_WARN = 5.0     # CDN 전체 에러율(%) warning
EB_HEALTH_WARN = 15.0    # ElasticBeanstalk EnvironmentHealth(0 OK ~ 25 Severe)
EB_HEALTH_CRIT = 20.0
GB = 1024 ** 3

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _f(severity, area, title, evidence, ts=None):
    """finding dict 생성. evidence 는 근거 수치 문자열(예: '최대 99.2% ≥ 90%')."""
    return {"severity": severity, "area": area, "title": title,
            "evidence": evidence, "ts": ts}


def _host_name(it):
    return it.get("instance_name") or it.get("instance_id") or "EC2"


# ── 영역별 룰 ─────────────────────────────────────────────────────────
def host_findings():
    """EC2: 상태검사 실패 / CPU 지속·피크 / CPU 크레딧 소진."""
    snap = storage.get_host()
    if not snap:
        return []
    out = []
    for it in (snap["payload"].get("instances") or []):
        name = _host_name(it)
        cpu_avg, cpu_max = it.get("cpu_avg"), it.get("cpu_max")
        sf, cr = it.get("status_failed"), it.get("credit_min")
        if sf is not None and sf > 0:
            out.append(_f("critical", "EC2", f"{name} 상태검사 실패",
                          f"StatusCheckFailed={int(sf)}"))
        if cpu_avg is not None and cpu_avg >= CPU_CRIT:
            out.append(_f("critical", "EC2", f"{name} CPU 지속 고부하",
                          f"평균 {cpu_avg:.1f}% ≥ {CPU_CRIT:.0f}%"))
        elif cpu_max is not None and cpu_max >= CPU_PEAK:
            out.append(_f("warning", "EC2", f"{name} CPU 순간 피크",
                          f"최대 {cpu_max:.1f}% ≥ {CPU_PEAK:.0f}%"))
        if cr is not None and cr < CREDIT_LOW:
            out.append(_f("warning", "EC2", f"{name} CPU 크레딧 소진 위험",
                          f"크레딧 잔량 {cr:.0f} < {CREDIT_LOW:.0f}"))
    return out


def db_findings():
    """RDS: CPU 지속·피크 / 여유공간 부족 / 연결수 포화."""
    snap = storage.get_db()
    if not snap:
        return []
    out = []
    for it in (snap["payload"].get("instances") or []):
        name = it.get("db_id") or "RDS"
        cpu_avg, cpu_max = it.get("cpu_avg"), it.get("cpu_max")
        fs, conn_max = it.get("free_storage"), it.get("conn_max")
        if cpu_avg is not None and cpu_avg >= CPU_CRIT:
            out.append(_f("critical", "RDS", f"{name} CPU 지속 고부하",
                          f"평균 {cpu_avg:.1f}% ≥ {CPU_CRIT:.0f}%"))
        elif cpu_max is not None and cpu_max >= CPU_PEAK:
            out.append(_f("warning", "RDS", f"{name} CPU 순간 피크",
                          f"최대 {cpu_max:.1f}% ≥ {CPU_PEAK:.0f}%"))
        if fs is not None:
            gb = fs / GB
            if gb < RDS_STORAGE_CRIT_GB:
                out.append(_f("critical", "RDS", f"{name} 여유공간 부족",
                              f"{gb:.1f}GB < {RDS_STORAGE_CRIT_GB:.0f}GB"))
            elif gb < RDS_STORAGE_WARN_GB:
                out.append(_f("warning", "RDS", f"{name} 여유공간 주의",
                              f"{gb:.1f}GB < {RDS_STORAGE_WARN_GB:.0f}GB"))
        if conn_max is not None and conn_max >= RDS_CONN_WARN:
            out.append(_f("warning", "RDS", f"{name} 연결수 포화 임박",
                          f"최대 연결 {conn_max:.0f} ≥ {RDS_CONN_WARN:.0f}"))
    return out


def cdn_findings():
    """CloudFront: 5xx 서버에러율 / 4xx 클라이언트에러율 상승."""
    snap = storage.get_cdn()
    if not snap:
        return []
    out = []
    for d in (snap["payload"].get("distributions") or []):
        did = d.get("dist_id") or "CDN"
        e5, e4 = d.get("err_5xx"), d.get("err_4xx")
        if e5 is not None and e5 > CDN_5XX_CRIT:
            out.append(_f("critical", "CDN", f"{did} 5xx 서버에러율 상승",
                          f"5xx {e5:.2f}% > {CDN_5XX_CRIT:.0f}%"))
        if e4 is not None and e4 > CDN_4XX_WARN:
            out.append(_f("warning", "CDN", f"{did} 4xx 클라이언트에러율 상승",
                          f"4xx {e4:.2f}% > {CDN_4XX_WARN:.0f}%"))
    return out


def alarm_findings():
    """CloudWatch 알람: ALARM(경보) / INSUFFICIENT_DATA(데이터부족) 표면화."""
    out = []
    for a in storage.get_alarms():
        st = a.get("state")
        if st == "ALARM":
            out.append(_f("critical", "알람", f"{a['alarm_name']} 경보",
                          a.get("state_reason") or "ALARM 상태", ts=a.get("state_updated")))
        elif st == "INSUFFICIENT_DATA":
            out.append(_f("info", "알람", f"{a['alarm_name']} 데이터 부족",
                          "INSUFFICIENT_DATA — 메트릭 미수신", ts=a.get("state_updated")))
    return out


# ── 집계 ──────────────────────────────────────────────────────────────
def build_findings():
    """모든 영역 룰을 실행해 severity 순으로 정렬한 findings 리스트를 반환.
    영역별 예외는 격리(한 영역 실패가 전체를 막지 않음)."""
    out = []
    for fn in (alarm_findings, host_findings, db_findings, cdn_findings):
        try:
            out.extend(fn())
        except Exception:  # noqa: BLE001 — 한 영역 스냅샷 손상이 전체 인사이트를 막지 않음
            continue
    out.sort(key=lambda x: SEV_ORDER.get(x["severity"], 9))
    return out


def summarize(findings):
    """severity별 카운트 + 총계."""
    s = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        s[f["severity"]] = s.get(f["severity"], 0) + 1
    s["total"] = len(findings)
    return s


def build_insights():
    """프론트/AI 가 소비할 통합 구조: {findings, summary, generated_at}."""
    findings = build_findings()
    return {
        "findings": findings,
        "summary": summarize(findings),
        "generated_at": int(time.time()),
    }
