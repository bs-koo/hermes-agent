# 운영 대시보드 개편 — 토스증권 다크 + 인사이트 메뉴 + 예외 중심 개요

> 작성일: 2026-06-22 | 대상: dataviz-prod 운영 대시보드 | 승인: 사용자("모두 진행해")
> 배경: 현재 대시보드가 (1) 데이터 과잉/무의미, (2) 인사이트 부재, (3) "AI스러운" 라이트 디자인.

## 0. 목표

- **정보 과잉 제거**: 정상은 숨기고 이상 신호만 노출(예외 중심).
- **자동 인사이트**: 룰 탐지(결정적) + AI 설명(Gemini) 하이브리드 전용 메뉴.
- **토스증권 다크 디자인**: 검정 배경, 강한 숫자 위계, 행 기반 고밀도, 형광 차트.
- **데이터 소스 정비**: nginx 로그(7일 15건, 무의미) → ALB CloudWatch 메트릭(실데이터).

## A. 토스증권 다크 디자인 시스템 (styles.css 전면)

| 토큰 | 값 | 용도 |
|---|---|---|
| `--bg` | `#0E0F11` | 앱 배경(딥다크) |
| `--bg-elev` | `#16171C` | 섹션 배경 |
| `--card` | `#1B1C22` | 카드(+ 1px `#2A2C33` 보더, 그림자 대신 보더) |
| `--text` | `#FFFFFF` | 본문 |
| `--text-sub` | `#8B8E96` | 보조 |
| `--text-dim` | `#5A5D66` | 흐림 |
| `--accent` | `#3B82F6` | 토스블루(다크 보정) |
| `--danger` | `#FF5C5C` | 위험/이상 |
| `--ok` | `#3DD68C` | 양호 |
| `--warn` | `#FFB020` | 경고 |

- 숫자: Pretendard **tabular-nums**, 큰 굵은 위계.
- 차트(Chart.js): 다크 배경 + 형광 라인 + 하단 그라데이션 fill, 그리드 `#2A2C33`.
- 레이아웃: 카드 그리드 축소 → **리스트 행(종목 리스트 감성)** + 우측 미니 스파크라인.

## B. 인사이트 메뉴 `#/insights` (룰 + AI)

**1) 룰 엔진** `dashboard/insights.py` — SQLite 스냅샷을 스캔해 finding 생성.
finding = `{severity: critical|warning|info, area, title, evidence(근거수치), metric, value, threshold, ts}`

룰 목록(초기):
- EC2: 최대 CPU>90% / t계열 CPU크레딧<임계 / StatusCheckFailed>0
- RDS: 연결수>max의 80% / 여유공간 하락추세 D-day(선형외삽) / DBLoad>vCPU수
- CDN: 4xx·5xx·전체 에러율>임계
- ALB: HTTPCode_*_5XX 비율>1% / TargetResponseTime p99>SLO
- 알람: ALARM / INSUFFICIENT_DATA 상태 표면화

신호 없으면 "현재 주목할 신호 없음 ✓".

**2) AI 설명** `dashboard/chat.py` 확장 — findings**만** Gemini에 전달, 우선순위·원인추정·권장조치를 한국어로.
환각 방지: findings 밖 수치 생성 금지 프롬프트. 수집 주기 맞춰 생성·SQLite 캐시(페이지뷰마다 호출 X).

**3) 프론트**: severity 색상 카드 리스트 + 근거 수치 + AI 코멘트 접기/펴기.

## C. 개요 `#/dashboard` 예외 중심 재편

- 상단: 한 줄 종합 상태(정상/주의 N/경고 N).
- "주목 필요" 섹션: insights의 critical/warning만 카드. 없으면 섹션 자체 제거.
- "전체 정상": 접힌 요약 한 줄(펼치면 기존 미니카드).

## D. 데이터 소스 + IAM

- **ALB 메트릭 추가**(권한 0, CloudWatch): RequestCount / TargetResponseTime(p50/p90/p99) / HTTPCode_Target_*_XX / HealthyHostCount. nginx는 엔드포인트·사용자 보조로 격하.
- **채택 IAM(Tier B, read-only 무료)**: `pi:GetResourceMetrics`,`pi:DescribeDimensionKeys`,`pi:ListAvailableResourceMetrics`(RDS Top SQL) · `elasticloadbalancing:Describe*` · `cloudfront:ListDistributions` · `rds:DescribeDBInstances`. → IAM 정책 JSON 제공, 사용자 적용.
- **제외**: Cost Explorer(과금), CWAgent 메모리(재배포).
- **구현 전 ALB 존재 검증**: list_metrics(AWS/ApplicationELB). 없으면 nginx 유지.

## E. 작업 순서

1. 토스증권 다크 디자인 시스템(styles.css 전면 + index.html/dashboard.js 차트 옵션)
2. 인사이트 룰 엔진(insights.py) + storage 캐시 + scheduler 잡 + api 엔드포인트
3. AI 설명(chat.py 확장)
4. 인사이트 메뉴 + 개요 예외중심(프론트)
5. ALB 데이터 소스 추가(aggregations + collectors + 패널 정비)
6. IAM 정책 JSON 제공

## F. 제약/원칙

- 기존 루트 스크립트(weekly_insight.py 등) 불변(BR-5). aggregations.py 정책 동기화 주석 유지.
- 단일 워커, SQLite 캐시 우선, AWS read-only, 비용 $0 유지.
