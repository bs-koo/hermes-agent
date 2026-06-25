# hermes_agent — dataviz-prod 운영 비서

AWS 운영 모니터링 · Dooray 업무 · 본부 일정을 한 화면에서 보고, **Gemini AI로 질문**하는 사내 운영 대시보드입니다.
이상 징후가 생기면 **Google Chat으로 알려줍니다.**

## 주요 기능

- **AWS 모니터링** — 알람 · 가동률/응답시간 · EC2 · DB(RDS) · CDN · 트래픽
- **운영 인사이트** — 이상 징후만 자동 감지 + AI 종합 코멘트, 주의 신호 발생 시 Google Chat 알림
- **Dooray 업무** — 이번 주 업무 현황 · 주간 보고(자동 생성) · 월간 리포트
- **본부 일정** — 근태·회의 (Google Calendar 연동 시)
- **AI 채팅** — 수집된 운영 데이터를 근거로 질문에 답변 (Gemini)

## 사용법

1. `.env.example`을 복사해 `.env`에 키 입력 (`GCHAT_WEBHOOK` · `GEMINI_API_KEY` · `DOORAY_TOKEN` 등)
2. `run_dashboard.cmd` 실행 (Docker로 빌드 + 기동)
3. 브라우저에서 **http://localhost:8090** 접속

## 이렇게 써보세요

- 우측 **채팅**에 물어보기 — *"경보 있어?"*, *"DB 상태는?"*, *"이번주 업무 현황"*
- 좌측 메뉴로 둘러보기 — 알람 · 가동률 · EC2 · DB · CDN · 인사이트 · Dooray 업무

---

자세한 구조·환경변수는 `dashboard/`, `Dockerfile.dashboard`, `.env.example` 참고.
