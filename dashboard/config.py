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

# ── 수집 주기(초) ─────────────────────────────────────────────────────
ALARM_INTERVAL = 300
UPTIME_INTERVAL = 300
TRAFFIC_INTERVAL = 600
DB_INTERVAL = 600

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
