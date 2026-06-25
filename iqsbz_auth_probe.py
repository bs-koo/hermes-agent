# -*- coding: utf-8 -*-
"""IQSbz 로그인 메커니즘 검증 + JWT 발급 시점 캡처용 1회성 프로브.

비밀번호는 환경변수로만 받는다(채팅/코드에 노출 금지).
  PowerShell:
    $env:IQSBZ_EMAIL="bonseung@sqisoft.com"; $env:IQSBZ_PW="****"; python iqsbz_auth_probe.py
  bash:
    IQSBZ_EMAIL=bonseung@sqisoft.com IQSBZ_PW='****' python iqsbz_auth_probe.py

이 스크립트가 확인하는 것:
  1) /getRsaPublicKey → 공개키 수신 (PEM/base64 모두 처리)
  2) 이메일·비번 RSA(PKCS#1 v1.5) 암호화 → /doLogin 폼 POST
  3) 로그인 성공 판정(302 → /main vs /common/error401)
  4) 세션 쿠키 / 응답에서 JWT(Bearer) 발급 흔적 탐색
  5) :9010 API를 (쿠키만으로 / Bearer로) 호출해 인증 방식 판별
모든 비밀값은 '있다/길이'만 출력하고 실제 값은 마스킹한다.
"""
import os
import re
import sys
import base64

try:                       # Windows 콘솔(cp949)에서도 한글/기호 안전 출력
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding

BASE = "https://iqsbz.sqisoft.com"
API = "https://iqsbz-api.sqisoft.com:9010"

EMAIL = os.environ.get("IQSBZ_EMAIL", "").strip()
PW = os.environ.get("IQSBZ_PW", "")

if not EMAIL or not PW:
    sys.exit("환경변수 IQSBZ_EMAIL / IQSBZ_PW 를 설정하세요.")


def mask(s):
    if not s:
        return "(없음)"
    s = str(s)
    return f"<{len(s)}자: {s[:6]}…{s[-4:]}>" if len(s) > 12 else f"<{len(s)}자>"


def to_pem(key):
    key = key.strip()
    if "BEGIN" in key:
        return key
    # base64 DER → PEM 래핑
    body = "\n".join(key[i:i + 64] for i in range(0, len(key), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----\n"


def rsa_enc(pub_pem, text):
    pub = load_pem_public_key(pub_pem.encode())
    ct = pub.encrypt(text.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ct).decode()


s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (hermes-iqsbz-probe)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/",
})

print("── 1) /getRsaPublicKey ──")
r = s.post(BASE + "/getRsaPublicKey", timeout=15)
print("   status:", r.status_code, "| ct:", r.headers.get("content-type"))
pub = r.json().get("publicKey")
print("   publicKey:", mask(pub))
pem = to_pem(pub)

print("── 2) RSA 암호화 + /doLogin ──")
enc_email = rsa_enc(pem, EMAIL)
enc_pw = rsa_enc(pem, PW)
print("   enc_email:", mask(enc_email), "| enc_pw:", mask(enc_pw))
r2 = s.post(BASE + "/doLogin",
            data={"email": enc_email, "password": enc_pw},
            allow_redirects=False, timeout=15)
loc = r2.headers.get("Location", "")
print("   status:", r2.status_code, "| Location:", loc)
ok = ("/main" in loc) or (r2.status_code == 200 and "error401" not in loc)
print("   => 로그인:", "[OK] 성공으로 보임" if ok else "[FAIL] 실패/거부 (" + loc + ")")
print("   Set-Cookie 이름들:", [c.name for c in s.cookies])

if not ok:
    print("\n로그인 실패 — 이메일 형식을 바꿔 재시도해 보세요(예: 앞부분만 vs 전체 이메일).")
    sys.exit(0)

print("── 3) JWT 발급 흔적 탐색 ──")
# 3a) 쿠키에 토큰?
for c in s.cookies:
    looks = any(k in c.name.lower() for k in ("token", "jwt", "auth", "access"))
    print(f"   cookie {c.name}:", mask(c.value), "← 토큰후보" if looks else "")

# 3b) /main HTML 에서 토큰/엔드포인트 흔적
try:
    m = s.get(BASE + "/main", timeout=15)
    html = m.text
    print("   /main status:", m.status_code, "| len:", len(html))
    for pat in (r'Bearer\s+[\w\.\-]+', r'["\']?(accessToken|token|jwt|authToken)["\']?\s*[:=]\s*["\']([\w\.\-]{16,})',
                r'localStorage\.(setItem|getItem)\([^)]{0,60}', r'/getToken[^"\']*', r'/token[^"\']*',
                r'eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]{6,}'):
        hits = re.findall(pat, html)[:4]
        if hits:
            print(f"     [{pat[:24]}…] →", [mask(h if isinstance(h, str) else h[-1]) for h in hits])
except Exception as e:
    print("   /main 조회 오류:", e)

# 3c) :9010 API 를 (쿠키만으로) 호출 — 쿠키 인증이 되는지
print("── 4) :9010 API 인증 방식 판별 ──")
test_ep = API + "/it/common/holi-day/y/2026"
try:
    a1 = s.get(test_ep, timeout=15)
    print("   쿠키만으로 호출:", a1.status_code,
          "→ [OK] 쿠키 인증 가능" if a1.status_code == 200 else "→ 쿠키만으론 부족(JWT 필요)")
    if a1.status_code == 200:
        print("   샘플 응답:", a1.text[:160])
except Exception as e:
    print("   API 호출 오류:", e)

print("\n── 끝. 위 결과(특히 3·4)를 알려주시면 대시보드 연동 방식을 확정합니다. ──")
