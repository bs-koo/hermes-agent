# -*- coding: utf-8 -*-
"""Dooray 업무 수집기 — "파트업무진행" 프로젝트의 주간 업무를 수집해
주간보고 데이터 + 업무 인사이트 롤업으로 가공, dooray_snapshot 1행으로 교체 저장.

다른 수집기(AWS)와 동일 원칙: 여기서만 외부(Dooray) 호출, API/프론트는 SQLite 만 읽음.
의존성 추가 금지 — urllib 로 REST 직접 호출(chat.py 의 Gemini 호출과 동일 패턴).
토큰(config.DOORAY_TOKEN) 미설정 시 아무 것도 하지 않는다(빈 상태)."""
import re
import json
import html
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error

from dashboard import config, storage, chat
from dashboard.collectors import base

# 워크플로 class → 한국어 라벨(개별 workflow 이름이 없을 때의 기본)
_CLASS_KR = {"registered": "할 일", "working": "진행", "closed": "완료"}
_DETAIL_CAP = 200       # 주간 본문 상세 조회 상한(현재 주 업무 수만큼 — 보통 수십 건)
_PAGE_CAP = 12          # 페이지네이션 안전 상한


# ── Dooray REST(읽기 전용) ────────────────────────────────────────────
def _get(path, **params):
    url = config.DOORAY_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "dooray-api " + config.DOORAY_TOKEN,
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def _paginate(path, **params):
    """size=200 으로 result 를 끝까지(또는 _PAGE_CAP 까지) 모은다."""
    out = []
    page = 0
    while page < _PAGE_CAP:
        j = _get(path, page=page, size=200, **params)
        res = j.get("result") or []
        out.extend(res)
        if len(res) < 200:
            break
        page += 1
    return out


# ── 유틸 ──────────────────────────────────────────────────────────────
def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(div|p|li|tr|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _date(iso):
    """ISO8601 → date. 실패 시 None."""
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        try:
            return datetime.date.fromisoformat(iso[:10])
        except (ValueError, TypeError):
            return None


def _assignee(users):
    """담당자 이름(to 우선, 없으면 from=등록자)."""
    users = users or {}
    to = users.get("to") or []
    if to:
        m = (to[0] or {}).get("member") or {}
        if m.get("name"):
            return m["name"]
    frm = (users.get("from") or {}).get("member") or {}
    return frm.get("name") or "-"


def _registrant(users):
    return ((users.get("from") or {}).get("member") or {}).get("name") or "-"


def _pick_week(milestones, target):
    """target(date)이 [startedAt, endedAt] 에 드는 마일스톤. 없으면 started<=target 중 최신."""
    best_in = None
    best_recent = None
    for m in milestones:
        s, e = _date(m.get("startedAt")), _date(m.get("endedAt"))
        if s and e and s <= target <= e:
            best_in = m
        if s and s <= target:
            if best_recent is None or s > _date(best_recent.get("startedAt")):
                best_recent = m
    return best_in or best_recent


# ── 수집 본체 ─────────────────────────────────────────────────────────
# 로그 creator 는 organizationMemberId 만 담겨 있어(이름 없음) /common/v1/members/{id}
# 로 1회 조회해 이름을 해석한다. 수집 1회 동안 동일 ID 재조회를 막는 캐시.
_MEMBER_NAMES = {}


def _member_name(mid):
    """조직멤버 ID → 표시 이름(예: '김준오'). 실패/미존재 시 None. 결과 캐시."""
    if not mid:
        return None
    if mid in _MEMBER_NAMES:
        return _MEMBER_NAMES[mid]
    name = None
    try:
        r = _get(f"/common/v1/members/{mid}")
        name = (r.get("result") or {}).get("name")
    except Exception:  # noqa: BLE001 — 멤버 조회 실패는 작성자 '-' 로 폴백
        name = None
    _MEMBER_NAMES[mid] = name
    return name


def _comments(pid, post_id):
    """업무의 댓글/로그(사람들이 매일 적는 코멘트) → [{author, at, text}]."""
    out = []
    try:
        rows = _get(f"/project/v1/projects/{pid}/posts/{post_id}/logs", page=0, size=50).get("result") or []
    except Exception:  # noqa: BLE001
        return out
    for lg in rows:
        body = lg.get("body") or {}
        txt = _strip_html(body.get("content") if isinstance(body, dict) else "")
        if not txt:
            continue
        cr = lg.get("creator") or {}
        mem = cr.get("member") or {}
        mid = mem.get("organizationMemberId") or mem.get("id")
        author = (_member_name(mid)
                  or mem.get("name")
                  or (cr.get("organizationMember") or {}).get("name")
                  or cr.get("name") or "-")
        out.append({"author": author, "at": lg.get("createdAt"), "text": txt[:1500]})
    return out


def _today_kst(now):
    dt = datetime.datetime.fromtimestamp(int(now) + 9 * 3600, datetime.timezone.utc)
    return dt, dt.date()


def _collect(now, force=False):
    if not config.DOORAY_TOKEN:
        return  # 토큰 미설정 → 조용히 통과(빈 상태)
    now_dt, today = _today_kst(now)
    prev = storage.get_dooray()

    # 매일 아침(해당 주간만) 갱신 — 오늘 이미 수집했으면 skip, 아침(6시) 전엔 대기.
    if not force:
        if prev:
            pd = _today_kst(prev.get("collected_at") or 0)[1]
            if pd == today:
                return  # 오늘 이미 수집됨
        if now_dt.hour < 6:
            return  # 아침 전 — 대기

    # 이전 요약 캐시(업무별) — 내용(본문/코멘트) 변경 없으면 재사용 → Gemini 토큰 절감.
    prev_sum = {}
    if prev:
        for pt in ((prev.get("payload") or {}).get("tasks") or []):
            prev_sum[pt.get("id")] = (pt.get("_sumkey"), pt.get("ai_summary"))

    pid = config.DOORAY_PROJECT_ID

    # 프로젝트명
    try:
        proj = (_get(f"/project/v1/projects/{pid}").get("result") or {})
        pname = proj.get("code") or proj.get("description") or "파트업무진행"
    except Exception:  # noqa: BLE001
        pname = "파트업무진행"

    # 태그 id→이름
    tag_map = {t.get("id"): t.get("name") for t in _paginate(f"/project/v1/projects/{pid}/tags")}
    # 워크플로 id→이름
    wf_map = {}
    try:
        for w in (_get(f"/project/v1/projects/{pid}/workflows").get("result") or []):
            wf_map[w.get("id")] = w.get("name")
    except Exception:  # noqa: BLE001
        pass

    # 주차(마일스톤) — 이번 주(해당 주간)만
    milestones = _paginate(f"/project/v1/projects/{pid}/milestones")
    cur = _pick_week(milestones, today)

    def posts_of(m):
        if not m:
            return []
        rows = _paginate(f"/project/v1/projects/{pid}/posts", milestoneIds=m.get("id"))
        return [p for p in rows if (p.get("milestone") or {}).get("id") == m.get("id")]

    # 이번 주 업무 + 본문/댓글(상한 내)
    tasks = []
    for i, p in enumerate(posts_of(cur)):
        tids = [t.get("id") for t in (p.get("tags") or [])]
        tnames = [tag_map.get(x) for x in tids if tag_map.get(x)]
        wf = p.get("workflow") or {}
        status = wf_map.get(wf.get("id")) or _CLASS_KR.get(p.get("workflowClass")) or p.get("workflowClass")
        t = {
            "id": p.get("id"),
            "number": p.get("number"),
            "subject": p.get("subject"),
            "tags": tnames,
            "assignee": _assignee(p.get("users")),
            "registrant": _registrant(p.get("users")),
            "status": status,
            "workflowClass": p.get("workflowClass"),
            "createdAt": p.get("createdAt"),
            "body": "",
            "comments": [],
            "ai_summary": None,
        }
        if i < _DETAIL_CAP:
            try:
                dr = (_get(f"/project/v1/projects/{pid}/posts/{p['id']}").get("result") or {})
                b = (dr.get("body") or {})
                t["body"] = _strip_html(b.get("content") if isinstance(b, dict) else "")[:2000]
            except Exception:  # noqa: BLE001
                pass
            t["comments"] = _comments(pid, p.get("id"))
            # AI 요약 — 내용(본문/코멘트) 변경 없으면 이전 요약 재사용(토큰 절감), 바뀐 업무만 재요약.
            last_at = t["comments"][-1].get("at") if t["comments"] else ""
            sumkey = "%d|%d|%s" % (len(t["body"]), len(t["comments"]), last_at or "")
            pk, ps = prev_sum.get(t["id"], (None, None))
            if pk == sumkey and ps:
                t["ai_summary"] = ps
            elif t["body"] or t["comments"]:
                try:
                    t["ai_summary"] = chat.summarize_task(t["subject"], t["body"], t["comments"])
                except Exception:  # noqa: BLE001 — 요약 실패는 수집을 막지 않음
                    t["ai_summary"] = None
            t["_sumkey"] = sumkey
        tasks.append(t)

    payload = {
        "project_id": pid,
        "project_name": pname,
        "collected_at": int(now),
        "current_week": ({"id": cur.get("id"), "name": cur.get("name"),
                          "startedAt": cur.get("startedAt"), "endedAt": cur.get("endedAt")} if cur else None),
        "tasks": tasks,
    }
    storage.replace_dooray_snapshot(json.dumps(payload, ensure_ascii=False), int(now))

    # ── 월간 누적(주차 시작일이 속한 월에 귀속) — (month, tag, subject) upsert ──
    # 매일 수집할 때마다 이번 주 업무를 월간 히스토리에 쌓아, 달마다 프로젝트별 실적이 누적된다.
    month = ((cur.get("startedAt") or "")[:7] if (cur and cur.get("startedAt"))
             else datetime.datetime.fromtimestamp(int(now) + 9 * 3600,
                                                  datetime.timezone.utc).strftime("%Y-%m"))
    if month:
        week_name = (cur or {}).get("name")
        hist = []
        for t in tasks:
            subj = (t.get("subject") or "").strip()
            if not subj:
                continue
            for tag in (t.get("tags") or ["기타"]):
                hist.append({
                    "month": month, "tag": tag, "subject": subj,
                    "status": t.get("status"), "wfclass": t.get("workflowClass"),
                    "assignee": t.get("assignee"), "week": week_name,
                    "body": t.get("body") or "",
                    # 이번 주 요약은 이미 만든 것(주간 보고용)을 재사용 → 월간 저장에 추가 토큰 0
                    "ai_summary": t.get("ai_summary"),
                    "last_at": int(now),
                })
        storage.upsert_dooray_history(hist)


def run(now):
    """Dooray 수집 1회(base.run_job 으로 예외 격리). 스케줄러는 force 없이 호출(일일 가드)."""
    return base.run_job("dooray", lambda: _collect(now))


def backfill_month(year_month, now=None):
    """완료된 과거 달(YYYY-MM)의 모든 주차 마일스톤 업무를 한 번 수집·요약해
    dooray_task_history 에 채운다(일일 수집과 별개의 1회성 백필).
    이미 요약된 업무는 재요약하지 않는다(토큰 절약). 반환: 처리 통계 dict."""
    if not config.DOORAY_TOKEN:
        return {"error": "DOORAY_TOKEN 미설정"}
    pid = config.DOORAY_PROJECT_ID
    ts = int(now or time.time())

    tag_map = {t.get("id"): t.get("name")
               for t in _paginate(f"/project/v1/projects/{pid}/tags")}
    wf_map = {}
    try:
        for w in (_get(f"/project/v1/projects/{pid}/workflows").get("result") or []):
            wf_map[w.get("id")] = w.get("name")
    except Exception:  # noqa: BLE001
        pass

    # 해당 월에 시작한 주차(마일스톤)만 대상.
    milestones = _paginate(f"/project/v1/projects/{pid}/milestones")
    target = [m for m in milestones
              if (_date(m.get("startedAt"))
                  and _date(m.get("startedAt")).strftime("%Y-%m") == year_month)]

    # 이미 백필된 요약은 재사용(재요약 회피).
    prev = {}
    for r in storage.get_dooray_history(year_month):
        if r.get("ai_summary"):
            prev[(r["tag"], r["subject"])] = r["ai_summary"]

    hist = []
    stat = {"month": year_month, "milestones": len(target),
            "tasks": 0, "summarized": 0, "reused": 0}
    for m in target:
        week_name = m.get("name")
        rows = _paginate(f"/project/v1/projects/{pid}/posts", milestoneIds=m.get("id"))
        posts = [p for p in rows if (p.get("milestone") or {}).get("id") == m.get("id")]
        for p in posts[:_DETAIL_CAP]:
            subj = (p.get("subject") or "").strip()
            if not subj:
                continue
            stat["tasks"] += 1
            tids = [t.get("id") for t in (p.get("tags") or [])]
            tnames = [tag_map.get(x) for x in tids if tag_map.get(x)] or ["기타"]
            wf = p.get("workflow") or {}
            status = (wf_map.get(wf.get("id")) or _CLASS_KR.get(p.get("workflowClass"))
                      or p.get("workflowClass"))
            body = ""
            try:
                dr = (_get(f"/project/v1/projects/{pid}/posts/{p['id']}").get("result") or {})
                b = (dr.get("body") or {})
                body = _strip_html(b.get("content") if isinstance(b, dict) else "")[:2000]
            except Exception:  # noqa: BLE001
                pass
            comments = _comments(pid, p.get("id"))

            ai = None
            for tag in tnames:                      # 이미 요약했으면 재사용
                if (tag, subj) in prev:
                    ai = prev[(tag, subj)]
                    stat["reused"] += 1
                    break
            if ai is None and (body or comments):
                try:
                    ai = chat.summarize_task(subj, body, comments)
                    if ai:
                        stat["summarized"] += 1
                except Exception:  # noqa: BLE001 — 요약 실패는 본문만 저장
                    ai = None

            for tag in tnames:
                hist.append({
                    "month": year_month, "tag": tag, "subject": subj,
                    "status": status, "wfclass": p.get("workflowClass"),
                    "assignee": _assignee(p.get("users")), "week": week_name,
                    "body": body, "ai_summary": ai, "last_at": ts,
                })
    storage.upsert_dooray_history(hist)
    return stat
