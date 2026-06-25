# -*- coding: utf-8 -*-
"""Dooray 프로젝트(업무) 구조 파악용 1회성 읽기 전용 프로브.

목적: "파트업무진행" 프로젝트의 업무가 태그/주차/담당자/일일 추가(댓글·로그)를
어떻게 담고 있는지 실제 응답으로 확인 → 주간보고 자동화 설계 확정.

개인 API 토큰은 환경변수로만 받는다(채팅/코드 노출 금지).
  두레이 개인 설정 > "API"(또는 개인 인증 토큰) 에서 발급.
  PowerShell:
    $env:DOORAY_TOKEN="발급받은토큰"; python dooray_probe.py
  특정 프로젝트명 지정(기본 "파트업무진행"):
    $env:DOORAY_TOKEN="..."; $env:DOORAY_PROJECT="파트업무진행"; python dooray_probe.py

모든 호출은 GET(읽기)만 한다. 토큰 값은 절대 출력하지 않는다.
"""
import os
import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

# 민간 https://api.dooray.com / 공공 https://api.gov-dooray.com / 금융 https://api.dooray.co.kr
BASE = os.environ.get("DOORAY_BASE", "https://api.dooray.com").rstrip("/")
TOKEN = os.environ.get("DOORAY_TOKEN", "").strip()
PROJECT_NAME = os.environ.get("DOORAY_PROJECT", "파트업무진행").strip()

if not TOKEN:
    sys.exit("환경변수 DOORAY_TOKEN 을 설정하세요(두레이 개인 API 토큰).")

S = requests.Session()
S.headers.update({"Authorization": "dooray-api " + TOKEN,
                  "Content-Type": "application/json"})


def get(path, **params):
    r = S.get(BASE + path, params=params, timeout=20)
    try:
        j = r.json()
    except Exception:
        return {"_http": r.status_code, "_raw": r.text[:300]}
    return j


def ok(j):
    return isinstance(j, dict) and (j.get("header") or {}).get("isSuccessful")


def short(v, n=80):
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[:n] + "…"


print("── 0) 토큰 검증: /common/v1/members/me ──")
me = get("/common/v1/members/me")
if not ok(me):
    print("   실패:", json.dumps(me, ensure_ascii=False)[:300]); sys.exit(0)
mr = me["result"]
print("   me:", mr.get("name"), "|", mr.get("userCode") or mr.get("emailAddress"), "| id:", mr.get("id"))

print(f"── 1) 프로젝트 검색: '{PROJECT_NAME}' ──")
projs = get("/project/v1/projects", page=0, size=200, member="me")
if not ok(projs):
    projs = get("/project/v1/projects", page=0, size=200)
cands = [p for p in (projs.get("result") or []) if PROJECT_NAME in (p.get("code", "") + " " + p.get("description", "") + " " + (p.get("name") or p.get("code") or ""))]
allp = projs.get("result") or []
print(f"   내 프로젝트 {len(allp)}개. '{PROJECT_NAME}' 매칭 {len(cands)}개")
for p in allp[:40]:
    name = p.get("code") or p.get("description") or p.get("id")
    mark = "  ★" if p in cands else ""
    print(f"     - id={p.get('id')} code={short(p.get('code'),30)} desc={short(p.get('description'),40)}{mark}")
if not cands:
    print("   ⚠ 이름 매칭 실패 — 위 목록에서 해당 프로젝트 id를 확인해 DOORAY_PROJECT로 다시 지정하거나 알려주세요.")
    sys.exit(0)
proj = cands[0]
PID = proj.get("id")
print(f"   => 대상 project-id = {PID}")

print("── 2) 태그(tag) 목록 ──")
tags = get(f"/project/v1/projects/{PID}/tags", page=0, size=200)
for t in (tags.get("result") or [])[:60]:
    print(f"     tag id={t.get('id')} name={short(t.get('name'),40)} group={short((t.get('tagPrefix') or {}).get('name') if isinstance(t.get('tagPrefix'),dict) else t.get('tagPrefix'),20)}")

print("── 3) 마일스톤(주차 후보) 목록 ──")
ms = get(f"/project/v1/projects/{PID}/milestones", page=0, size=200)
for m in (ms.get("result") or [])[:60]:
    print(f"     milestone id={m.get('id')} name={short(m.get('name'),40)} {short(m.get('startedAt'),10)}~{short(m.get('endedAt'),10)} status={m.get('status')}")

print("── 4) 워크플로(상태) 정의 ──")
wf = get(f"/project/v1/projects/{PID}/workflows")
for w in (wf.get("result") or [])[:30]:
    print(f"     workflow id={w.get('id')} name={short(w.get('name'),20)} class={w.get('class')}")

print("── 5) 최근 업무(post) 샘플 + 필드 구조 ──")
posts = get(f"/project/v1/projects/{PID}/posts", page=0, size=8, order="-createdAt")
plist = posts.get("result") or []
print(f"   총 {posts.get('totalCount')}건 중 최근 {len(plist)}건")
for p in plist:
    users = p.get("users") or {}
    to = ((users.get("to") or [{}])[0].get("member") or {}).get("name") if users.get("to") else None
    frm = ((users.get("from") or {}).get("member") or {}).get("name")
    tagids = [t.get("id") for t in (p.get("tags") or [])]
    print(f"     #{p.get('number')} [{short(p.get('subject'),34)}] 상태={p.get('workflowClass')} "
          f"등록={frm} 담당={to} 태그={tagids} 마일스톤={(p.get('milestone') or {}).get('id')} "
          f"생성={short(p.get('createdAt'),10)} 만기={short(p.get('dueDate') or p.get('dueAt'),10)}")

print("── 6) 한 업무의 댓글/로그(=사람들이 매일 추가하는 내용) 샘플 ──")
if plist:
    pid0 = plist[0].get("id")
    logs = get(f"/project/v1/projects/{PID}/posts/{pid0}/logs", page=0, size=5)
    print(f"   post {plist[0].get('number')} 의 로그 {len(logs.get('result') or [])}건:")
    for lg in (logs.get("result") or [])[:5]:
        author = ((lg.get("creator") or {}).get("member") or {}).get("name") or (lg.get("creator") or {}).get("name")
        body = (lg.get("body") or {})
        content = body.get("content") if isinstance(body, dict) else body
        print(f"     · {short(lg.get('createdAt'),16)} {author}: {short(content,90)}")

print("\n── 끝. 위 2·3·5·6 결과(태그/주차/업무필드/로그 형태)를 주시면 주간보고 자동화 설계를 확정합니다. ──")
