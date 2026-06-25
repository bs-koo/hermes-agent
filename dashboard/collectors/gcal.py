# -*- coding: utf-8 -*-
"""Google Calendar(사업부 업무 일정) 수집기 — iCal 비밀 주소(.ics)를 받아
다가오는 N일 일정을 파싱해 gcal_snapshot 1행으로 저장.

다른 수집기와 동일 원칙: 여기서만 외부(구글) 호출, API/프론트는 SQLite 만 읽음.
의존성 추가 금지 — urllib 로 .ics 를 받아 직접 파싱(라이브러리 없이).
GCAL_ICS_URL 미설정 시 아무 것도 하지 않는다(빈 상태).

한계(v1): RRULE 은 FREQ=DAILY/WEEKLY/MONTHLY + INTERVAL/COUNT/UNTIL 만 근사 확장
(BYDAY 다중요일·EXDATE 미반영). 사내 단일 타임존(Asia/Seoul) 가정."""
import json
import datetime
import urllib.request

from dashboard import config, storage
from dashboard.collectors import base

_KST = datetime.timezone(datetime.timedelta(hours=9))


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-dash/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def _unfold(text):
    """iCal 라인 폴딩 해제(다음 줄이 공백/탭으로 시작하면 이어붙임)."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _parse_dt(val, params):
    """DTSTART/DTEND 값 → (epoch, all_day). 날짜만=종일, Z=UTC, 그 외=KST 간주."""
    val = (val or "").strip()
    if not val:
        return None, False
    if "VALUE=DATE" in (params or "") or (len(val) == 8 and val.isdigit()):
        try:
            d = datetime.datetime.strptime(val[:8], "%Y%m%d").replace(tzinfo=_KST)
            return int(d.timestamp()), True
        except ValueError:
            return None, False
    try:
        if val.endswith("Z"):
            dt = datetime.datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(
                tzinfo=datetime.timezone.utc)
        else:
            dt = datetime.datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=_KST)
        return int(dt.timestamp()), False
    except ValueError:
        return None, False


def _events(lines):
    """VEVENT 블록 파싱 → [{summary, location, start, end, all_day, rrule}]."""
    evs, cur = [], None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT":
            if cur is not None:
                evs.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            head, _, val = ln.partition(":")
            name, _, params = head.partition(";")
            name = name.upper()
            if name == "SUMMARY":
                cur["summary"] = val
            elif name == "LOCATION":
                cur["location"] = val
            elif name == "DTSTART":
                cur["start"], cur["all_day"] = _parse_dt(val, params)
            elif name == "DTEND":
                cur["end"], _ = _parse_dt(val, params)
            elif name == "RRULE":
                cur["rrule"] = val
    return evs


def _rrule_occurrences(start_epoch, rrule, win_start, win_end):
    """간단 RRULE 확장 — 윈도우 [win_start, win_end] 안의 발생 epoch 목록."""
    parts = {}
    for kv in (rrule or "").split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.upper()] = v
    freq = parts.get("FREQ")
    if not freq:
        return [start_epoch] if win_start <= start_epoch <= win_end else []
    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until = None
    if parts.get("UNTIL"):
        until, _ = _parse_dt(parts["UNTIL"], "")
    base_dt = datetime.datetime.fromtimestamp(start_epoch, _KST)
    occ = []
    for n in range(0, 800):  # 안전 상한
        if count is not None and n >= count:
            break
        if freq == "DAILY":
            cand = base_dt + datetime.timedelta(days=interval * n)
        elif freq == "WEEKLY":
            cand = base_dt + datetime.timedelta(weeks=interval * n)
        elif freq == "MONTHLY":
            m0 = base_dt.month - 1 + interval * n
            y, mo = base_dt.year + m0 // 12, m0 % 12 + 1
            try:
                cand = base_dt.replace(year=y, month=mo)
            except ValueError:
                continue
        else:  # YEARLY 등 미지원 → 원본만
            return [start_epoch] if win_start <= start_epoch <= win_end else []
        ce = int(cand.timestamp())
        if until and ce > until:
            break
        if ce > win_end:
            break
        if ce >= win_start:
            occ.append(ce)
    return occ


def _collect(now):
    # 두 캘린더: 업무(work) + 근태(leave). 설정된 것만 수집해 kind 로 태깅·병합.
    sources = []
    if config.GCAL_ICS_URL:
        sources.append((config.GCAL_ICS_URL, "work"))
    if config.GCAL_ATTEND_ICS_URL:
        sources.append((config.GCAL_ATTEND_ICS_URL, "leave"))
    if not sources:
        return  # 미설정 → no-op(빈 상태)
    nowdt = datetime.datetime.fromtimestamp(int(now), _KST)
    day0 = nowdt.replace(hour=0, minute=0, second=0, microsecond=0)
    win_start = int(day0.timestamp())                                   # 오늘 0시(KST)
    win_end = int(now) + config.GCAL_WINDOW_DAYS * 86400
    out = []
    for url, kind in sources:
        try:
            evs = _events(_unfold(_fetch(url)))
        except Exception:  # noqa: BLE001 — 한 캘린더 실패가 다른 캘린더를 막지 않음
            continue
        for e in evs:
            st = e.get("start")
            if st is None:
                continue
            title = (e.get("summary") or "").strip() or "(제목 없음)"
            dur = (e["end"] - st) if e.get("end") else 0
            starts = (_rrule_occurrences(st, e["rrule"], win_start, win_end)
                      if e.get("rrule") else
                      ([st] if win_start <= st <= win_end else []))
            for s in starts:
                out.append({
                    "title": title[:200],
                    "start": s,
                    "end": (s + dur) if dur > 0 else None,
                    "all_day": bool(e.get("all_day")),
                    "location": (e.get("location") or "").strip()[:200],
                    "kind": kind,
                })
    out.sort(key=lambda x: x["start"])
    payload = {"events": out[:120], "collected_at": int(now),
               "window_days": config.GCAL_WINDOW_DAYS}
    storage.replace_gcal_snapshot(json.dumps(payload, ensure_ascii=False), int(now))


def run(now):
    """Google Calendar 수집 1회(base.run_job 으로 예외 격리)."""
    return base.run_job("gcal", lambda: _collect(now))
