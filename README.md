# 🛰️ hermes_agent — dataviz-prod 운영 비서

사내 운영팀을 위한 **AWS 인프라 모니터링 · Dooray 업무 · 본부 일정**을 한 화면에 모으고,
**Gemini AI로 질문·요약**하며, 이상 징후가 생길 때만 **Google Chat으로 알리는** 통합 운영 대시보드입니다.

> 대상 서비스: `dataviz-prod` (Elastic Beanstalk + RDS + CloudFront)

---

## ✨ 주요 기능

### AWS 운영 모니터링
- **알람** — CloudWatch 알람 상태/전환 이력
- **가동률·응답시간** — health(`/actuator/health`)·home(`/`) 5분 핑(가용성 능동 측정, AWS 비용 0)
- **EC2 인스턴스** — CPU(평균/최고)·네트워크·EBS·크레딧, 24h/30일 시계열
- **DB(RDS)** — CPU·연결 수·여유 저장공간·메모리·부하, 시계열
- **CDN(CloudFront)** — 요청수·5xx 에러율
- **트래픽** — nginx 접근 로그(Logs Insights) 기반 요청량·사용자·스캐너 탐지

### 운영 인사이트 (하이브리드 룰 + AI)
- 룰 엔진이 **이상 징후만** 신호화 — 평상시엔 0건(노이즈 차단), 임계 초과 시에만 카드 노출
- 신호 목록을 Gemini가 **우선순위·원인·조치**로 종합(자연어 코멘트)
- **critical/warning 발생 시 Google Chat 자동 푸시** — 새로 뜰 때만 알리고 해소되면 정상화 알림(스팸 방지)

### Dooray 업무
- **업무 현황** — 이번 주 파트 업무(진행/완료/할 일), 업무별 AI 요약
- **주간 보고** — 도전·개선·생존 분류로 파트장 메일 형식 자동 생성(진입 시 자동, 비용 0)
- **월간 리포트** — 완료된 달의 업무 실적(진행 중인 달은 토큰 낭비 방지로 제외)

### Google Calendar · AI 채팅
- **본부 일정** — 근태(휴가/반차)·업무 일정(iCal 연동 시)
- **AI 채팅** — Gemini 2.0-flash, SSE 스트리밍. AWS를 직접 호출하지 않고 **수집된 SQLite 스냅샷만** 근거로 답변(환각 방지·빠른 응답)

---

## 🏗️ 아키텍처

```
[수집]  Scheduler(데몬, 60초 tick)  ── 외부 API 호출은 여기서만 ──┐
          alarms·uptime·traffic·db·host·cdn·dooray·gcal·alerts     │
                                                                   ▼
[저장]  SQLite(WAL) ── 스냅샷은 교체식 1행 / 시계열은 30일 purge ──┤
                                                                   │ 읽기 전용
[조회]  FastAPI(api.py) ── AWS 미호출, SQLite만 ── 정적 프론트 서빙 ┤
        프론트(dashboard.js) ── 60초 폴링(숨김 뷰 생략)            │
        Gemini(chat.py) ── 스냅샷 텍스트를 컨텍스트로 주입         ┘
```

**설계 원칙**
- 비싼 외부 호출(AWS·Gemini)은 **백그라운드 수집층 1곳에서만** — 화면/API는 SQLite만 읽어 빠르고 저렴
- 각 수집기는 try/except로 격리(한 잡 실패가 전체를 막지 않음)
- 트래픽 Logs Insights는 기간별 차등 수집(1일 10분 / 7일 1시간 / 30일 6시간)으로 스캔 비용 절감
- UI는 **StyleSeed 디자인 시스템**(단일 액센트, 플랫 표면, 상태색은 점+텍스트) 준수 — `docs/styleseed/`

---

## 🚀 빠른 시작 (Docker)

```bash
# 1) 환경변수 준비
cp .env.example .env   # GCHAT_WEBHOOK / GEMINI_API_KEY / DOORAY_TOKEN 등 채우기

# 2) 대시보드 빌드 + 실행 (단일 컨테이너: FastAPI + 백그라운드 스케줄러)
run_dashboard.cmd      # 또는 아래 docker 명령

docker build -f Dockerfile.dashboard -t dataviz-dashboard:latest .
docker run -d --name dataviz-dashboard --restart unless-stopped \
  --env-file .env \
  -v "C:/Users/SQI/.aws:/aws:ro" -e AWS_CONFIG_FILE=/aws/config \
  -e AWS_SHARED_CREDENTIALS_FILE=/aws/credentials -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 \
  -v "$(pwd)/uptime_log.csv:/data/uptime_log.csv:ro" -e UPTIME_CSV=/data/uptime_log.csv \
  -v dataviz_dash_db:/db -e DASH_DB=/db/dashboard.db \
  -p 8090:8080 dataviz-dashboard:latest
```

접속: **http://localhost:8090**

> ⚠️ `uvicorn --workers 1` 고정 — 멀티워커 시 스케줄러가 워커마다 떠 Logs Insights 중복 발사·SQLite 락 발생.

---

## 🔧 환경변수 (`.env`)

| 변수 | 용도 |
|------|------|
| `GCHAT_WEBHOOK` | Google Chat 웹훅(주의 신호 알림·배치 메시지) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini API 키(채팅·요약·인사이트) |
| `GEMINI_MODEL` | 기본 `gemini-2.0-flash`(무료 일일 한도 넉넉) |
| `DOORAY_TOKEN` | Dooray 개인 액세스 토큰 `id:secret` |
| `DOORAY_PROJECT_ID` | 수집 대상 프로젝트(기본: 파트업무진행) |
| `GCAL_ICS_URL` / `GCAL_ATTEND_ICS_URL` | Google Calendar iCal 비공개 주소(업무/근태) |
| `AWS_PROFILE` / `AWS_REGION` | AWS 자격증명(또는 EC2 IAM 역할) |

> `.env`는 `.gitignore` 처리되어 커밋되지 않습니다(시크릿 보호).

---

## 🤖 자동화 배치 (Windows 작업 스케줄러)

| 스크립트 | 주기 | 역할 |
|---|---|---|
| `uptime_ping.py` | 5분 | health/home 능동 핑 → `uptime_log.csv`(대시보드 가동률 원천), 다운/복구 시 Chat |
| `daily_digest.py` | (옵션) | 일일 점검 + AI 종합분석, 금요일 Dooray 주간업무 |
| `weekly_insight.py` | (옵션) | 주간 트래픽·품질·DB 인사이트 |

> 운영 알림은 대시보드 인사이트 푸시(`alerting.py`)로 일원화 — 정기 발송 대신 **주의 신호 발생 시에만** 알립니다.

---

## 📁 디렉터리 구조

```
dashboard/
  api.py            FastAPI 라우트(SQLite만 읽음)
  scheduler.py      백그라운드 수집 데몬
  collectors/       알람·가동률·트래픽·db·ec2·cdn·dooray·gcal 수집기
  aggregations.py   CloudWatch 메트릭/로그 집계
  insights.py       이상 징후 룰 엔진
  alerting.py       인사이트 → Google Chat 푸시
  chat.py           Gemini 채팅·요약(SSE 스트리밍)
  storage.py        SQLite 스토리지
  static/           프론트엔드(index.html·dashboard.js·styles.css)
docs/styleseed/     UI 디자인 시스템(StyleSeed)
Dockerfile.dashboard  웹 대시보드 이미지
uptime_ping.py      가동률 능동 측정 배치
```

---

## 🧱 기술 스택

- **백엔드** FastAPI · uvicorn · boto3 · SQLite(WAL)
- **수집** CloudWatch(메트릭/알람/Logs Insights) · Dooray REST · Google Calendar(iCal) — 모두 표준 라이브러리 urllib(의존성 최소화)
- **AI** Gemini(`gemini-2.0-flash`, REST 직접 호출)
- **프론트** Vanilla JS · Chart.js · Pretendard · StyleSeed 디자인 시스템
- **배포** Docker(단일 컨테이너)
