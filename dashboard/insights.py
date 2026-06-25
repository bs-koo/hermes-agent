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
# CDN 5xx(서버에러율) — 재계층 + 절대량 floor + 히스테리시스(깜빡임 방지).
# 1% 단독은 일시적 블립이라 위험이 아니다: 2~5%=주의, 5%+=위험, 그리고 24h 5xx 절대
# 건수가 충분할 때만(저볼륨 % 노이즈 무시). 한 번 뜬 신호는 1.5% 미만으로 내려갈
# 때까지 유지해 경계선에서의 감지↔정상화 반복(깜빡임)을 막는다.
CDN_5XX_WARN = 2.0       # 주의: 5xx > 2% (floor 충족 시)
CDN_5XX_CRIT = 5.0       # 위험: 5xx > 5%
CDN_5XX_CLEAR = 1.5      # 히스테리시스 해제: 활성 알림은 5xx < 1.5% 가 될 때까지 유지
CDN_5XX_MIN = 30         # 절대량 floor: 최근 24h 5xx 절대 건수(이 미만이면 신호 없음)
CDN_4XX_WARN = 5.0       # CDN 4xx 에러율(%) warning(현재 인사이트 미사용)
CDN_TOTAL_WARN = 5.0     # CDN 전체 에러율(%) warning(현재 인사이트 미사용)
EB_HEALTH_WARN = 15.0    # ElasticBeanstalk EnvironmentHealth(0 OK ~ 25 Severe)
EB_HEALTH_CRIT = 20.0
GB = 1024 ** 3

# 트래픽 이상감지(최근 24h vs 7일 일평균) — 평상시 노이즈 방지용 '절대 하한' 포함.
# 트래픽이 적을 땐(예: 주 17건) 어떤 신호도 뜨지 않고, 비정상 규모일 때만 카드가 뜬다.
TRAFFIC_SURGE_MULT = 3.0       # 7일 일평균 대비 N배 이상이면 급증으로 판단
SCANNER_SURGE_MIN = 30         # 스캐너 탐침 급증: 최근 24h 최소 절대 건수(소량 변동 무시)
REQ_SURGE_MIN = 200            # 요청량 급증: 최근 24h 최소 절대 건수
REQ_DROP_BASELINE_MIN = 50     # 요청량 급감은 평소 일평균이 이 이상일 때만 의미 있음
REQ_DROP_FRAC = 0.2            # 평소의 20% 이하로 떨어지면 급감
ORIGIN_5XX_MIN = 10            # 원본 5xx 급증: 최근 24h 최소 절대 건수
ORIGIN_5XX_RATE = 1.0          # 그리고 전체 요청 대비 1% 이상일 때만

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}

# CloudFront 5xx 유형 설명(502/503/504 ErrorRate, 추가 지표 활성 시)
_CF_5XX_KIND = {
    "502": "502 Bad Gateway(원본이 에러로 응답)",
    "503": "503 Service Unavailable(원본 과부하·차단)",
    "504": "504 Gateway Timeout(원본 응답 지연·타임아웃)",
}


def _cf_dominant_5xx(d):
    """502/503/504 중 비율이 가장 큰 유형 설명. 추가 지표 미활성(전부 None)이면 None."""
    cands = [(k, d.get("err_" + k)) for k in ("502", "503", "504")]
    cands = [(k, v) for k, v in cands if isinstance(v, (int, float)) and v > 0]
    if not cands:
        return None
    cands.sort(key=lambda x: x[1], reverse=True)
    return _CF_5XX_KIND[cands[0][0]]


def _f(severity, area, title, evidence, meaning="", action=None, route=None, ts=None):
    """finding dict 생성.
    evidence=근거 수치 문자열 · meaning=쉬운 설명(무엇/왜) · action=권장 조치 리스트 ·
    route=드릴다운 영역(host/database/cdn/alarms 등)."""
    return {"severity": severity, "area": area, "title": title,
            "evidence": evidence, "meaning": meaning,
            "action": action or [], "route": route, "ts": ts}


def _host_name(it):
    return it.get("instance_name") or it.get("instance_id") or "EC2"


def _collected(snap):
    """스냅샷의 수집 시각(= 이상이 감지된 시각) epoch. payload 우선, 없으면 snap."""
    if not snap:
        return None
    p = snap.get("payload") or {}
    return p.get("collected_at") or snap.get("collected_at")


def _stamp(out, ts):
    """ts 없는 finding 에 감지 시각을 채운다(알람은 이미 state_updated 보유)."""
    for fi in out:
        if fi.get("ts") is None:
            fi["ts"] = ts
    return out


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
                          f"StatusCheckFailed={int(sf)}",
                          meaning="인스턴스 상태 검사가 실패했어요. 서버가 시스템·네트워크 수준에서 정상 응답하지 못하는 상태예요.",
                          action=["호스트(EC2) 탭에서 해당 인스턴스 상태를 확인하세요",
                                  "필요하면 인스턴스를 재시작하거나 상태 검사를 다시 실행하세요",
                                  "부팅 문제로 보이면 시스템 로그를 확인하세요"],
                          route="host"))
        if cpu_avg is not None and cpu_avg >= CPU_CRIT:
            out.append(_f("critical", "EC2", f"{name} CPU 지속 고부하",
                          f"평균 {cpu_avg:.1f}% ≥ {CPU_CRIT:.0f}%",
                          meaning="CPU가 오랫동안 높게 유지되고 있어요. 요청 처리가 느려지거나 타임아웃이 날 수 있어요.",
                          action=["호스트(EC2) 탭에서 CPU 추세와 부하가 몰리는 시간대를 확인하세요",
                                  "트래픽 급증인지, 특정 프로세스 때문인지 구분하세요",
                                  "계속되면 스케일업이나 오토스케일을 검토하세요"],
                          route="host"))
        elif cpu_max is not None and cpu_max >= CPU_PEAK:
            out.append(_f("warning", "EC2", f"{name} CPU 순간 피크",
                          f"최대 {cpu_max:.1f}% ≥ {CPU_PEAK:.0f}%",
                          meaning="CPU가 순간적으로 크게 튀었어요. 일시적일 수 있지만 반복되면 점검이 필요해요.",
                          action=["호스트(EC2) 탭에서 피크가 반복되는지 확인하세요",
                                  "배치·크론 작업이나 트래픽 스파이크와 겹치는지 보세요"],
                          route="host"))
        if cr is not None and cr < CREDIT_LOW:
            out.append(_f("warning", "EC2", f"{name} CPU 크레딧 소진 위험",
                          f"크레딧 잔량 {cr:.0f} < {CREDIT_LOW:.0f}",
                          meaning="버스트형(t계열) 인스턴스의 CPU 크레딧이 거의 떨어졌어요. 0이 되면 성능이 강제로 제한돼요.",
                          action=["호스트(EC2) 탭에서 크레딧 추세를 확인하세요",
                                  "부하가 꾸준하면 고정 성능 인스턴스로 변경을 검토하세요",
                                  "또는 Unlimited 모드를 검토하세요"],
                          route="host"))
    return _stamp(out, _collected(snap))


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
                          f"평균 {cpu_avg:.1f}% ≥ {CPU_CRIT:.0f}%",
                          meaning="DB CPU가 오랫동안 높아요. 쿼리 지연이나 연결 적체로 이어질 수 있어요.",
                          action=["DB 성능 탭에서 CPU 추세를 확인하세요",
                                  "느린 쿼리나 풀스캔이 있는지 점검하세요",
                                  "지속되면 인스턴스 등급 상향을 검토하세요"],
                          route="database"))
        elif cpu_max is not None and cpu_max >= CPU_PEAK:
            out.append(_f("warning", "RDS", f"{name} CPU 순간 피크",
                          f"최대 {cpu_max:.1f}% ≥ {CPU_PEAK:.0f}%",
                          meaning="DB CPU가 순간적으로 크게 튀었어요. 반복되면 원인 점검이 필요해요.",
                          action=["DB 성능 탭에서 피크 시점을 확인하세요",
                                  "배치·집계 쿼리나 트래픽과 겹치는지 보세요"],
                          route="database"))
        if fs is not None:
            gb = fs / GB
            if gb < RDS_STORAGE_CRIT_GB:
                out.append(_f("critical", "RDS", f"{name} 여유공간 부족",
                              f"{gb:.1f}GB < {RDS_STORAGE_CRIT_GB:.0f}GB",
                              meaning="DB 여유 저장공간이 거의 없어요. 공간이 차면 쓰기 실패나 DB 중단으로 이어질 수 있어요.",
                              action=["DB 성능 탭에서 저장공간 추세를 확인하세요",
                                      "불필요한 로그·임시 데이터를 정리하거나 스토리지를 확장하세요",
                                      "자동 확장(Storage Autoscaling)을 검토하세요"],
                              route="database"))
            elif gb < RDS_STORAGE_WARN_GB:
                out.append(_f("warning", "RDS", f"{name} 여유공간 주의",
                              f"{gb:.1f}GB < {RDS_STORAGE_WARN_GB:.0f}GB",
                              meaning="DB 여유 저장공간이 줄고 있어요. 미리 확보해 두면 안전해요.",
                              action=["DB 성능 탭에서 저장공간 추세를 확인하세요",
                                      "증가 속도를 보고 스토리지 확장 시점을 잡으세요"],
                              route="database"))
        if conn_max is not None and conn_max >= RDS_CONN_WARN:
            out.append(_f("warning", "RDS", f"{name} 연결수 포화 임박",
                          f"최대 연결 {conn_max:.0f} ≥ {RDS_CONN_WARN:.0f}",
                          meaning="DB 동시 연결 수가 한계에 가까워요. 더 늘면 새 연결이 거부될 수 있어요.",
                          action=["DB 성능 탭에서 연결 추세를 확인하세요",
                                  "커넥션 풀 설정이나 연결 누수를 점검하세요",
                                  "필요하면 최대 연결 수나 인스턴스 등급을 올리세요"],
                          route="database"))
    return _stamp(out, _collected(snap))


def _cdn_alert_active(did):
    """이 배포의 CDN 5xx 신호가 현재 '발송됨(활성)' 상태인지 — 히스테리시스용.
    alert_state(Chat 발송 추적)에 area=CDN 이고 제목에 did 가 든 항목이 있으면 True.
    조회 실패는 비활성(False)으로 간주해 인사이트가 깨지지 않게 한다."""
    try:
        for a in storage.get_active_alerts():
            if a.get("area") == "CDN" and did and did in (a.get("title") or ""):
                return True
    except Exception:  # noqa: BLE001 — 상태 조회 실패는 비활성 처리
        return False
    return False


def cdn_findings():
    """CloudFront 5xx 서버에러율 상승. 재계층(주의 2~5% / 위험 5%+) + 절대량 floor
    (24h 5xx ≥ CDN_5XX_MIN) + 히스테리시스(활성 알림은 1.5% 미만으로 내려갈 때까지 유지).
    1% 경계 깜빡임을 없애고, 일시적 저볼륨 블립을 위험으로 올리지 않는다."""
    snap = storage.get_cdn()
    if not snap:
        return []
    out = []
    for d in (snap["payload"].get("distributions") or []):
        did = d.get("dist_id") or "CDN"
        e5 = d.get("err_5xx")
        if e5 is None:
            continue
        # 절대량 floor: 24h 5xx 절대 건수(요청수 × 비율)가 충분할 때만 — 저볼륨 % 노이즈 무시.
        req = d.get("requests")
        abs5 = (req * e5 / 100.0) if req is not None else None
        if abs5 is None or abs5 < CDN_5XX_MIN:
            continue
        # 히스테리시스: 이미 떠 있는 신호는 해제 임계(1.5%)까지 유지, 새 신호는 주의 임계(2%) 초과부터.
        fire_at = CDN_5XX_CLEAR if _cdn_alert_active(did) else CDN_5XX_WARN
        if e5 <= fire_at:
            continue
        sev = "critical" if e5 > CDN_5XX_CRIT else "warning"
        kind = _cf_dominant_5xx(d)
        mean = ("CDN이 사용자에게 서버 오류(5xx)를 평소보다 많이 응답하고 있어요. "
                "일부 사용자가 페이지·API 오류를 겪고 있다는 뜻이에요.")
        ev = f"5xx {e5:.2f}% (24h {int(abs5)}건)"
        if kind:
            mean += f" 주로 {kind} 유형이에요."
            ev += f" · 주 유형 {kind.split('(')[0].strip()}"
        act = ["CDN 탭에서 어떤 배포·시간대에 5xx가 몰리는지 확인하세요",
               "원본 서버(EB/오리진) 상태와 최근 배포를 점검하세요",
               "원본이 원인이면 서버 로그와 헬스체크를 확인하세요"]
        if not kind:
            act.append("5xx 유형(502/503/504)이 안 보이면 CloudFront 추가 지표를 활성화하세요")
        out.append(_f(sev, "CDN", f"{did} 5xx 서버에러율 상승",
                      ev, meaning=mean, action=act, route="cdn"))
        # 4xx(클라이언트 오류)는 흔한 노이즈 — 운영 판단에 불필요해 인사이트에서 제외(5xx만 신호화)
    return _stamp(out, _collected(snap))


def alarm_findings():
    """CloudWatch 알람: ALARM(경보) / INSUFFICIENT_DATA(데이터부족) 표면화."""
    out = []
    for a in storage.get_alarms():
        st = a.get("state")
        if st == "ALARM":
            out.append(_f("critical", "알람", f"{a['alarm_name']} 경보",
                          a.get("state_reason") or "ALARM 상태",
                          meaning="CloudWatch 알람이 경보 상태예요. 설정한 임계 조건을 넘었어요.",
                          action=["알람 탭에서 조건과 사유를 확인하세요",
                                  "관련된 지표 영역(EC2·DB·CDN 등) 탭에서 원인을 추적하세요"],
                          route="alarms", ts=a.get("state_updated")))
        elif st == "INSUFFICIENT_DATA":
            out.append(_f("info", "알람", f"{a['alarm_name']} 데이터 부족",
                          "INSUFFICIENT_DATA — 메트릭 미수신",
                          meaning="알람이 평가할 데이터를 받지 못했어요. 리소스에 트래픽이 없거나 메트릭 전송이 끊겼을 때 생겨요. 꼭 장애는 아니에요.",
                          action=["알람 탭에서 어떤 지표인지 확인하세요",
                                  "해당 리소스가 사용 중인지, 메트릭이 들어오는지 점검하세요",
                                  "정상이면 무시하거나 알람 조건을 조정하세요"],
                          route="alarms", ts=a.get("state_updated")))
    return out


def traffic_findings():
    """트래픽(nginx 접근 로그) 이상감지 — 메뉴 대신 인사이트로만 표면화.
    최근 24h(period=1) 값을 7일 일평균(period=7÷7)과 비교하되, 절대 하한을 둬
    평상시(저트래픽)엔 아무 신호도 만들지 않는다. 급증/급감/스캐너/원본5xx."""
    d1 = storage.get_traffic(1)
    if not d1:
        return []
    p1 = d1["payload"] or {}
    total1 = p1.get("total") or 0
    scan1 = p1.get("scanner_hits") or 0
    b5_1 = (p1.get("buckets") or {}).get("5xx") or 0

    # 7일 일평균 기준선(없으면 비교형 신호는 건너뜀)
    daily_total = daily_scan = None
    d7 = storage.get_traffic(7)
    if d7:
        p7 = d7["payload"] or {}
        daily_total = (p7.get("total") or 0) / 7.0
        daily_scan = (p7.get("scanner_hits") or 0) / 7.0

    out = []

    # ── 원본 서버 5xx 급증(앱이 직접 에러) — 가장 심각 ──
    if b5_1 >= ORIGIN_5XX_MIN and total1 > 0 and (b5_1 / total1 * 100) >= ORIGIN_5XX_RATE:
        rate = b5_1 / total1 * 100
        out.append(_f("critical", "트래픽", "원본 서버 5xx 급증",
                      f"최근 24h 5xx {b5_1}건 ({rate:.1f}%)",
                      meaning="앱 서버(원본 nginx)가 직접 5xx 서버 오류를 내고 있어요. 앱 크래시·예외·과부하 정황이에요(CDN 엣지 5xx와 별개로 원본에서 발생).",
                      action=["호스트(EC2) 탭에서 같은 시간대 CPU·상태검사를 확인하세요",
                              "앱 로그에서 예외·OOM·재시작 흔적을 점검하세요",
                              "최근 배포 직후라면 롤백을 검토하세요"],
                      route="host"))

    # ── 스캐너·봇 탐침 급증(보안) ──
    if (scan1 >= SCANNER_SURGE_MIN and daily_scan
            and scan1 >= TRAFFIC_SURGE_MULT * daily_scan):
        mult = scan1 / daily_scan if daily_scan else 0
        out.append(_f("warning", "트래픽", "스캐너·봇 탐침 급증",
                      f"최근 24h {scan1}건 · 7일 일평균 {daily_scan:.0f}건의 {mult:.1f}배",
                      meaning="자동화된 스캐너·봇이 평소보다 훨씬 많이 취약점을 탐침하고 있어요. 대량 스캔이나 공격 정황일 수 있어요.",
                      action=["단일 출처(같은 IP)에서 반복되는지 확인하세요",
                              "WAF·보안그룹에서 해당 출처 차단을 검토하세요",
                              "민감 경로(.env·.git·actuator)가 200으로 응답하지 않는지 점검하세요"],
                      route=None))

    # ── 요청량 급증 ──
    if (total1 >= REQ_SURGE_MIN and daily_total
            and total1 >= TRAFFIC_SURGE_MULT * daily_total):
        mult = total1 / daily_total if daily_total else 0
        out.append(_f("warning", "트래픽", "요청량 급증",
                      f"최근 24h {total1}건 · 7일 일평균 {daily_total:.0f}건의 {mult:.1f}배",
                      meaning="실사용자 요청이 평소보다 급격히 늘었어요. 이벤트·홍보 효과일 수도, 비정상 트래픽일 수도 있어요.",
                      action=["호스트(EC2)·DB CPU가 함께 올랐는지 확인하세요",
                              "특정 출처/경로에 몰렸는지(스크래핑 가능성) 점검하세요"],
                      route="host"))

    # ── 요청량 급감(평소 트래픽이 있는 서비스에서만 의미) ──
    if (daily_total and daily_total >= REQ_DROP_BASELINE_MIN
            and total1 <= REQ_DROP_FRAC * daily_total):
        out.append(_f("warning", "트래픽", "요청량 급감",
                      f"최근 24h {total1}건 · 7일 일평균 {daily_total:.0f}건 대비 급감",
                      meaning="평소 들어오던 요청이 갑자기 거의 끊겼어요. 접속 장애나 상단(CDN·로드밸런서) 문제일 수 있어요.",
                      action=["가동률 탭에서 접속 경로가 정상인지 확인하세요",
                              "최근 배포·DNS·인증서 변경이 있었는지 점검하세요"],
                      route="uptime"))

    return _stamp(out, _collected(d1))


# ── 집계 ──────────────────────────────────────────────────────────────
def build_findings():
    """모든 영역 룰을 실행해 severity 순으로 정렬한 findings 리스트를 반환.
    영역별 예외는 격리(한 영역 실패가 전체를 막지 않음)."""
    out = []
    for fn in (alarm_findings, host_findings, db_findings, cdn_findings, traffic_findings):
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
