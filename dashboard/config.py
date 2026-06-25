# -*- coding: utf-8 -*-
"""대시보드 공통 상수·환경설정.
AWS 리전/로그그룹/DB식별자, 수집 주기, 보관일수, 경로(env 흡수)를 한 곳에 모은다.
경로는 컨테이너 이관을 위해 env 로 주입한다(DASH_DB, UPTIME_CSV).
import 시점에 load_env() 를 호출해 .env 를 환경에 흡수한다(기존 스크립트와 동일 패턴)."""
import os
from _env import load_env

load_env()

# ── AWS 상수(기존 스크립트와 동일 값 재사용) ──────────────────────────
REGION = "ap-northeast-2"
LG = "/aws/elasticbeanstalk/dataviz-prod/var/log/nginx/access.log"
DBID = "gseed-db"
# EC2 호스트 메트릭용 인스턴스 id(service_probe.py 참조). DescribeInstances 권한이
# 없을 수 있어 수집기는 list_metrics 로 자동 발견하고, 실패 시 이 기본값을 쓴다.
EC2_INSTANCE_ID = os.environ.get("EC2_INSTANCE_ID", "i-0e3e7120bc0b07a2d")

# ── 수집 주기(초) ─────────────────────────────────────────────────────
ALARM_INTERVAL = 300
UPTIME_INTERVAL = 300
TRAFFIC_INTERVAL = 600
DB_INTERVAL = 600
HOST_INTERVAL = 600
CDN_INTERVAL = 600

# ── 보관(purge) 정책 ──────────────────────────────────────────────────
RETENTION_DAYS = 30

# ── 경로(env 로 주입, 컨테이너/호스트 모두 흡수) ──────────────────────
# 기본 UPTIME_CSV 는 프로젝트 루트의 uptime_log.csv(dashboard/ 의 상위 디렉토리).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_UPTIME_CSV = os.path.join(_PROJECT_ROOT, "uptime_log.csv")

DASH_DB = os.environ.get("DASH_DB", "/db/dashboard.db")
UPTIME_CSV = os.environ.get("UPTIME_CSV", _DEFAULT_UPTIME_CSV)

# ── Gemini(웹 채팅 Q&A) ───────────────────────────────────────────────
# GEMINI_API_KEY 우선, 없으면 GOOGLE_API_KEY 흡수(.env 로 주입).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# ── Google Chat 푸시(주의 신호 알림) ──────────────────────────────────
# 인사이트(critical/warning)가 새로 뜨거나 해소될 때만 이 웹훅으로 알린다(평소 조용).
# 미설정이면 푸시를 건너뛴다(빈 상태). 발송 주기는 ALERT_INTERVAL(초).
GCHAT_WEBHOOK = os.environ.get("GCHAT_WEBHOOK", "").strip()
ALERT_INTERVAL = int(os.environ.get("ALERT_INTERVAL", "300"))  # 5분

# ── 인증(단일 공용 계정 JWT 로그인 게이트) ────────────────────────────
# 자격증명·시크릿은 .env 로만 주입(평문·로그 금지). AUTH_SECRET 미설정 시 기동 거부.
# AUTH_PASSWORD_HASH 는 PBKDF2 해시 문자열(`python -m dashboard.auth hash-password` 로 생성).
AUTH_USERNAME      = os.environ.get("AUTH_USERNAME", "").strip()
AUTH_PASSWORD_HASH = os.environ.get("AUTH_PASSWORD_HASH", "").strip()
AUTH_SECRET        = os.environ.get("AUTH_SECRET", "").strip()
AUTH_TOKEN_TTL_HOURS = int(os.environ.get("AUTH_TOKEN_TTL_HOURS", "8"))
AUTH_MAX_ATTEMPTS    = int(os.environ.get("AUTH_MAX_ATTEMPTS", "5"))
AUTH_LOCKOUT_MINUTES = int(os.environ.get("AUTH_LOCKOUT_MINUTES", "10"))
# 운영(HTTPS 종단) 배포 시 .env 에 AUTH_COOKIE_SECURE=true 필수(로컬 http 는 기본 false).
AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
# 신뢰 프록시(nginx/ALB 등) 뒤에서만 X-Forwarded-For 를 신뢰한다. 기본 false(직접 노출 가정).
AUTH_TRUSTED_PROXY = os.environ.get("AUTH_TRUSTED_PROXY", "false").strip().lower() in ("1", "true", "yes")

# ── Dooray(업무 보고/인사이트) ────────────────────────────────────────
# 민간 https://api.dooray.com / 공공 https://api.gov-dooray.com / 금융 https://api.dooray.co.kr
# DOORAY_TOKEN 은 개인 액세스 토큰 "id:secret"(.env/컨테이너 env 주입, 평문·로그 금지).
DOORAY_BASE = os.environ.get("DOORAY_BASE", "https://api.dooray.com").rstrip("/")
DOORAY_TOKEN = os.environ.get("DOORAY_TOKEN")
# 기본값 = "파트업무진행" 프로젝트 id(데이터플랫폼 파트 공유업무일지).
DOORAY_PROJECT_ID = os.environ.get("DOORAY_PROJECT_ID", "3964593156097643853")
DOORAY_INTERVAL = int(os.environ.get("DOORAY_INTERVAL", "3600"))  # 1시간(업무는 자주 안 변함)

# ── Google Calendar(사업부 업무 일정, iCal 비밀 주소) ─────────────────
# GCAL_ICS_URL = 구글 캘린더 설정 > "iCal 형식의 비공개 주소"(자격증명 — .env/env 주입,
# 평문·로그 금지). 미설정이면 일정 수집을 건너뛴다(빈 상태).
GCAL_ICS_URL = os.environ.get("GCAL_ICS_URL", "").strip()          # 업무 일정 캘린더
GCAL_ATTEND_ICS_URL = os.environ.get("GCAL_ATTEND_ICS_URL", "").strip()  # 근태(연차·반차·외근) 캘린더
GCAL_INTERVAL = int(os.environ.get("GCAL_INTERVAL", "1800"))     # 30분
GCAL_WINDOW_DAYS = int(os.environ.get("GCAL_WINDOW_DAYS", "45"))  # 다가오는 N일(월간 달력 커버)
