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

## 인증 설정

대시보드는 **단일 공용 계정**(사업부 공용 아이디/비밀번호)으로 보호됩니다. 미인증 접근은 모든 화면·API가 차단되고 `/login`으로 이동합니다. 자격증명은 `.env`에 환경변수로만 주입합니다(소스·응답에 평문 비노출).

### 필수 환경변수

| 변수 | 설명 |
|------|------|
| `AUTH_USERNAME` | 공용 계정 아이디 |
| `AUTH_PASSWORD_HASH` | 비밀번호의 PBKDF2 해시(아래 명령으로 생성) |
| `AUTH_SECRET` | JWT 서명용 시크릿(아래 명령으로 생성) |

> `AUTH_SECRET`이 비어 있으면 서버가 기동을 거부합니다("AUTH_SECRET이 설정되지 않았습니다. 서버를 시작할 수 없습니다").

### 선택 환경변수(기본값)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTH_TOKEN_TTL_HOURS` | `8` | 로그인 세션(JWT) 유효 시간 |
| `AUTH_MAX_ATTEMPTS` | `5` | 동일 IP 로그인 실패 허용 횟수 |
| `AUTH_LOCKOUT_MINUTES` | `10` | 실패 초과 시 차단 시간(분) |
| `AUTH_COOKIE_SECURE` | `false` | 인증 쿠키 `Secure` 속성. **기본 false — ⚠️ 운영(HTTPS) 시 `true` 필수** |
| `AUTH_TRUSTED_PROXY` | `false` | nginx/ALB 등 신뢰 프록시 뒤에 둘 때만 `true`. `true`면 레이트리밋 IP를 `X-Real-IP`(프록시가 덮어쓰는 단일 값) 우선, 없으면 `X-Forwarded-For`의 **rightmost**(프록시가 본 직전 피어 = 위조 불가)로 식별. 직접 노출 환경은 `false` 유지(클라가 위조한 XFF leftmost 무시, TCP 피어 IP 사용) |

### 비밀번호 해시·시크릿 생성

표준 라이브러리만으로 동작합니다(외부 패키지 불필요).

```bash
# 비밀번호 해시 생성(입력은 화면에 표시되지 않음) → AUTH_PASSWORD_HASH=... 출력
python -m dashboard.auth hash-password

# JWT 시크릿 생성 → AUTH_SECRET=... 출력
python -m dashboard.auth gen-secret
```

출력된 `AUTH_PASSWORD_HASH=...`, `AUTH_SECRET=...` 한 줄을 그대로 `.env`에 추가하고, `AUTH_USERNAME=공용아이디`도 함께 적습니다. 컨테이너 실행 시 `--env-file .env`로 주입됩니다(`run_dashboard.cmd`는 이미 `.env`를 마운트).

> **운영(HTTPS 종단) 배포 시 `.env`에 `AUTH_COOKIE_SECURE=true`를 반드시 추가하세요.** 그렇지 않으면 인증 쿠키가 HTTPS 연결에서 전송되지 않을 수 있습니다. 로컬 http 개발에서는 기본값 `false`로 둡니다.

---

자세한 구조·환경변수는 `dashboard/`, `Dockerfile.dashboard`, `.env.example` 참고.
