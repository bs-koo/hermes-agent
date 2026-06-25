# -*- coding: utf-8 -*-
"""인증 코어 — 단일 공용 계정 JWT 로그인 게이트.

외부 패키지 없이 파이썬 표준 라이브러리(hmac, hashlib, base64)만으로
JWT(HS256) 인코드/디코드, PBKDF2 비밀번호 해시·검증, 자격증명 인증,
쿠키 헬퍼, IP 기반 로그인 레이트리미터를 제공한다.

설정은 `from dashboard import config` 로 참조한다(.env 주입 — 평문·로그 금지).
해시/시크릿 생성용 CLI: `python -m dashboard.auth hash-password|gen-secret`.
"""
import os
import sys
import time
import json
import hmac
import base64
import hashlib
import secrets
import getpass
import threading

from dashboard import config

# ── 상수 ──────────────────────────────────────────────────────────────
COOKIE_NAME = "auth"
_PBKDF2_ITERATIONS = 240000      # PBKDF2-HMAC-SHA256 반복 수(개방-폐쇄: 코드에 내장)
_PBKDF2_ALGO = "pbkdf2_sha256"   # 해시 문자열 식별자
_RATE_MAX_ENTRIES = 4096         # 레이트리미터 _state 메모리 상한(초과 시 정리)


# ── base64url(JWT 표준 — 패딩 제거/복원) ──────────────────────────────
def _b64url_encode(raw):
    """bytes → 패딩 없는 base64url 문자열."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s):
    """패딩 없는 base64url 문자열 → bytes(패딩 복원)."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── JWT HS256(hmac + hashlib 만 사용) ─────────────────────────────────
def jwt_encode(payload, secret):
    """payload(dict) 를 HS256 서명한 JWT 문자열로 인코드한다."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = (header_b64 + "." + payload_b64).encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return header_b64 + "." + payload_b64 + "." + _b64url_encode(sig)


def jwt_decode(token, secret):
    """JWT 를 검증·디코드한다. 서명/형식/만료 오류는 전부 None 을 반환한다
    (예외를 호출부로 누설하지 않는다 — 미인증과 동일하게 처리)."""
    if not token or not secret:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        # alg confusion 방어: 서명 재계산 전에 헤더 alg 가 HS256 인지 먼저 확인한다
        # (alg=none / RS256 등으로 위조한 토큰을 즉시 거부).
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if header.get("alg") != "HS256":
            return None
        signing_input = (header_b64 + "." + payload_b64).encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        got = _b64url_decode(sig_b64)
        # 상수 시간 비교(타이밍 공격 완화)
        if not hmac.compare_digest(expected, got):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (ValueError, TypeError, KeyError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp <= time.time():
        return None
    return payload


# ── PBKDF2 비밀번호 해시(hashlib 만 사용) ─────────────────────────────
def hash_password(password, iterations=_PBKDF2_ITERATIONS):
    """비밀번호를 `pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>` 포맷으로 해시한다.
    salt 는 16바이트 난수(secrets), 반복 수는 호출 시 지정(기본 내장값)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "%s$%d$%s$%s" % (
        _PBKDF2_ALGO, iterations,
        _b64url_encode(salt), _b64url_encode(dk),
    )


def verify_password(password, stored):
    """비밀번호가 저장된 해시 문자열과 일치하는지 상수 시간 비교한다.
    포맷 오류·파싱 실패는 False(인증 실패와 동일)."""
    if not stored:
        return False
    try:
        algo, iter_s, salt_b64, hash_b64 = stored.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        iterations = int(iter_s)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(expected, dk)


# ── 자격증명 인증 ─────────────────────────────────────────────────────
def authenticate(username, password):
    """아이디/비밀번호를 검증한다. 둘 다 일치하면 True.
    아이디가 틀리면 PBKDF2(고비용)를 실행하지 않고 즉시 실패시킨다 —
    단일 공용 계정이라 username 타이밍 노출이 무의미하고, 잘못된 아이디 스팸으로
    인한 CPU 고갈(DoS)을 막는 것이 더 중요하다."""
    if not hmac.compare_digest(username or "", config.AUTH_USERNAME):
        return False
    return verify_password(password or "", config.AUTH_PASSWORD_HASH)


def _ttl_seconds():
    """JWT 유효기간(초) = AUTH_TOKEN_TTL_HOURS × 3600."""
    return config.AUTH_TOKEN_TTL_HOURS * 3600


def make_token():
    """인증 성공 시 발급할 JWT 를 만든다(sub/iat/exp)."""
    now = int(time.time())
    payload = {
        # 단일 공용 계정이라 sub 로 식별 불필요 — 사용자명을 JWT(base64 가독)에 넣지 않음.
        "sub": "user",
        "iat": now,
        "exp": now + _ttl_seconds(),
    }
    return jwt_encode(payload, config.AUTH_SECRET)


# ── 쿠키 헬퍼(httponly + SameSite=Strict) ─────────────────────────────
def set_auth_cookie(response, token):
    """응답에 인증 쿠키를 설정한다(max_age=ttl, httponly, samesite=strict)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_ttl_seconds(),
        httponly=True,
        samesite="strict",
        secure=config.AUTH_COOKIE_SECURE,
        path="/",
    )


def clear_auth_cookie(response):
    """인증 쿠키를 즉시 만료시킨다(max_age=0, 동일 속성)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        max_age=0,
        httponly=True,
        samesite="strict",
        secure=config.AUTH_COOKIE_SECURE,
        path="/",
    )


def read_token(request):
    """요청 쿠키에서 인증 토큰을 읽는다(없으면 빈 문자열)."""
    return request.cookies.get(COOKIE_NAME, "")


# ── 로그인 레이트리미터(IP 기준, 메모리 내 — 재시작 시 초기화) ────────
class LoginRateLimiter:
    """동일 IP 의 로그인 실패를 카운트해 임계 초과 시 일정 시간 차단한다.
    프로세스 메모리에만 보관(재시작 초기화). 스레드 안전(Lock)."""

    def __init__(self):
        self._state = {}            # {ip: {"count": int, "lock_until": epoch}}
        self._lock = threading.Lock()

    def is_locked(self, ip):
        """차단 중이면 남은 초(>0), 아니면 0 을 반환한다(만료된 잠금은 정리)."""
        now = time.time()
        with self._lock:
            entry = self._state.get(ip)
            if not entry:
                return 0
            lock_until = entry.get("lock_until", 0)
            if lock_until <= 0:
                # 아직 잠금 전(실패 카운트만 누적 중) — 엔트리를 보존한다.
                # (여기서 pop 하면 다음 record_failure 가 count 를 0 부터 다시 세어 영영 안 잠긴다.)
                return 0
            remain = lock_until - now
            if remain <= 0:
                # 잠금 만료 → 카운터 초기화
                self._state.pop(ip, None)
                return 0
            return int(remain) + 1

    def record_failure(self, ip):
        """실패 1회를 기록한다. 임계 초과 시 잠금을 건다."""
        now = time.time()
        with self._lock:
            if ip not in self._state and len(self._state) >= _RATE_MAX_ENTRIES:
                self._prune(now)   # 메모리 상한 보호(무작위 IP 스팸으로 무한 증식 방지)
            entry = self._state.setdefault(ip, {"count": 0, "lock_until": 0})
            entry["count"] += 1
            if entry["count"] >= config.AUTH_MAX_ATTEMPTS:
                entry["lock_until"] = now + config.AUTH_LOCKOUT_MINUTES * 60

    def _prune(self, now):
        """메모리 상한 초과 시 활성 잠금(lock_until>now)만 남기고
        만료 잠금·미잠금 카운트 엔트리를 제거한다(_lock 보유 상태에서 호출)."""
        drop = [k for k, e in self._state.items() if e.get("lock_until", 0) <= now]
        for k in drop:
            self._state.pop(k, None)

    def reset(self, ip):
        """성공 시 해당 IP 카운터를 비운다."""
        with self._lock:
            self._state.pop(ip, None)


# 모듈 싱글턴(미들웨어·라우트가 공유).
_rate_limiter = LoginRateLimiter()


# ── 기동 검증(설정 누락 시 기동 거부) ─────────────────────────────────
def ensure_configured():
    """필수 인증 설정 검증. 누락 시 stderr 로그 후 기동 거부."""
    if not config.AUTH_SECRET:
        msg = "AUTH_SECRET이 설정되지 않았습니다. 서버를 시작할 수 없습니다"
        print("[auth] FATAL: " + msg, file=sys.stderr)
        raise RuntimeError(msg)
    if not config.AUTH_USERNAME or not config.AUTH_PASSWORD_HASH:
        msg = "AUTH_USERNAME 또는 AUTH_PASSWORD_HASH가 설정되지 않았습니다. 서버를 시작할 수 없습니다"
        print("[auth] FATAL: " + msg, file=sys.stderr)
        raise RuntimeError(msg)
    if len(config.AUTH_SECRET) < 32:
        print("[auth] WARNING: AUTH_SECRET가 32자 미만입니다. "
              "'python -m dashboard.auth gen-secret'(64자)으로 재생성을 권장합니다.", file=sys.stderr)
    if not config.AUTH_COOKIE_SECURE:
        print("[auth] WARNING: AUTH_COOKIE_SECURE=false — 운영(HTTPS) 배포 시 .env 에 "
              "AUTH_COOKIE_SECURE=true 를 설정하세요(쿠키 평문 전송 위험).", file=sys.stderr)


# ── CLI: 해시/시크릿 생성(검증 없이 출력 — .env 작성 보조) ─────────────
def _cli_hash_password():
    """getpass 로 비밀번호를 입력받아 AUTH_PASSWORD_HASH 한 줄을 출력한다."""
    pw = getpass.getpass("비밀번호: ")
    pw2 = getpass.getpass("비밀번호 확인: ")
    if pw != pw2:
        print("비밀번호가 일치하지 않습니다.")
        return 1
    if not pw:
        print("빈 비밀번호는 사용할 수 없습니다.")
        return 1
    print("AUTH_PASSWORD_HASH=" + hash_password(pw))
    return 0


def _cli_gen_secret():
    """JWT 서명용 랜덤 시크릿(AUTH_SECRET) 한 줄을 출력한다."""
    print("AUTH_SECRET=" + secrets.token_urlsafe(48))
    return 0


def _main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hash-password":
        return _cli_hash_password()
    if cmd == "gen-secret":
        return _cli_gen_secret()
    print("사용법: python -m dashboard.auth {hash-password|gen-secret}")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
