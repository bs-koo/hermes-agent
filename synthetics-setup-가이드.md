# dataviz-prod Synthetics 캐너리 셋업 가이드

> 목적: 앱(서버) 무수정으로 **dataviz-prod 가동률(uptime %) + 사용자 체감 응답시간**을 AWS가 능동 측정.
> 역할 분담: **권한/역할/생성 = 손님(콘솔 관리자)**, **캐너리 스크립트·집계·리포트 = 봇(이미 준비됨)**.
> 스크립트 파일: `D:\SQ\hermes_agent\canary-dataviz.js`

---

## A. 콘솔로 생성 (가장 쉬움, 약 5분)

1. AWS 콘솔 → **CloudWatch** → 좌측 **Application Signals → Synthetics Canaries**(구: Synthetics) → **캐너리 생성**
2. **블루프린트**: "직접 작성(Upload your own script)" 또는 "API canary" 선택
3. **이름**: `dataviz-prod-uptime`  (← 집계 코드가 이 이름을 찾으니 그대로)
4. **런타임 버전**: `syn-nodejs-puppeteer-7.0` (이상)
5. **스크립트**: `canary-dataviz.js` 내용을 인라인 편집기에 붙여넣기
   - 핸들러: `index.handler`
6. **일정**: 주기적 실행 → **rate(5 minutes)** (5분마다)
7. **데이터 보존/아티팩트**: S3 버킷 — "새로 생성" 선택하면 자동 생성됨 (예: `cw-syn-results-062364668115-ap-northeast-2`)
8. **권한(실행 역할)**: "새 역할 생성" 선택하면 콘솔이 필요한 권한으로 자동 생성 (아래 B의 권한 포함됨)
9. (VPC 안에서만 접근되는 앱이면 VPC 설정 추가 — dataviz는 공개망이라 **불필요**)
10. **생성** 클릭 → 5분 내 첫 실행, 메트릭 발행 시작

> 생성 직후부터 `CloudWatchSynthetics` 네임스페이스에 **Duration(응답시간)·SuccessPercent(성공률)** 메트릭이 쌓이고, **읽기 권한만 있는 봇이 자동으로 집계**한다(별도 권한 추가 불필요 — `cloudwatch:GetMetricData` 이미 보유).

---

## B. 캐너리 실행 역할 권한 (콘솔이 자동 생성하지만, 수동 생성 시 참고)

캐너리가 결과를 저장·발행하려면 실행 역할에 다음이 필요:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::cw-syn-results-*", "arn:aws:s3:::cw-syn-results-*/*"] },
    { "Effect": "Allow", "Action": ["s3:ListAllMyBuckets"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:ap-northeast-2:062364668115:log-group:/aws/lambda/cwsyn-*" },
    { "Effect": "Allow", "Action": ["cloudwatch:PutMetricData"],
      "Condition": { "StringEquals": { "cloudwatch:namespace": "CloudWatchSynthetics" } },
      "Resource": "*" }
  ]
}
```
(AWS 관리형 정책 `CloudWatchSyntheticsExecutionRolePolicy` 를 붙여도 됨)

---

## C. CLI로 생성 (관리자 자격증명 있을 때, 대안)

```bash
# 1) 아티팩트 버킷
aws s3 mb s3://cw-syn-results-062364668115-ap-northeast-2 --region ap-northeast-2

# 2) 스크립트 zip (nodejs/node_modules/index.js 구조)
mkdir -p nodejs/node_modules && cp canary-dataviz.js nodejs/node_modules/index.js
zip -r canary.zip nodejs

# 3) 실행 역할(B의 정책 + lambda 신뢰관계)을 만든 뒤:
aws synthetics create-canary --name dataviz-prod-uptime \
  --runtime-version syn-nodejs-puppeteer-7.0 \
  --artifact-s3-location s3://cw-syn-results-062364668115-ap-northeast-2/dataviz-prod-uptime \
  --execution-role-arn arn:aws:iam::062364668115:role/<캐너리실행역할> \
  --schedule Expression="rate(5 minutes)" \
  --code Handler=index.handler,ZipFile=fileb://canary.zip \
  --region ap-northeast-2
```

---

## D. 생성 후 — 자동으로 되는 것

- **주간 리포트**에 `📈 가동률·응답시간` 섹션이 자동 표시 (`weekly_insight.py`가 캐너리 메트릭 감지).
- **봇 온디맨드**: "dataviz 지금 살아있어?", "응답속도 어때?", "이번주 가동률?" 질문에 답변.
- 캐너리가 5xx/타임아웃 감지 시 → 기존 알람 체계에 `SuccessPercent < 100` 알람을 추가하면 **다운 즉시 알림**도 가능(선택).

## E. 비용
- 캐너리 실행당 약 **$0.0012**. 5분 주기 = 월 ~8,640회 ≈ **$10/월**.
  - 비용 줄이려면 주기를 **rate(15 minutes)**(월 ~$3.5) 또는 **rate(30 minutes)** 로.
- S3 아티팩트 저장은 소액. 스크린샷/HAR 미저장 설정이라 더 적음.

## F. 2단계(선택) — 인증 API per-endpoint 지연
- `/api/charts` 등 **인증 필요 API**의 응답시간까지 측정하려면, 캐너리가 **Google OAuth 로그인 후 토큰으로 호출**해야 함(복잡, 토큰 만료 처리 필요).
- 봇 전용 계정/서비스 토큰이 확보되면 `canary-dataviz.js`에 로그인 step + 인증 헤더 추가로 확장.
