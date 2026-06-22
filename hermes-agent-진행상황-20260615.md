# Hermes Agent 사내 도입 검토 — 진행사항

> 작성일: 2026-06-15 | 상태: **설치·MCP(Docker) 빌드/등록 완료 → 모델 인증·AWS 권한만 남음** | 담당: bonseung

Hermes Agent(Nous Research)로 **운영 모니터링 자동화**를 시도하는 과정의 진행 기록 겸 핸드오프 노트. 같은 폴더의 `nous-research-hermes-20260615.md`(배경 리서치), `Dockerfile`(MCP 이미지)과 함께 본다.

---

## 1. 목표

- **1차 범위**: 운영 모니터링 자동화. 현재 그라파나로 **수동** 확인하는 것을, 에이전트가 **알람 감시 → 발생 시 자동 트리아지·요약**.
- 대상: `dataviz-prod` (AWS, `ap-northeast-2`). 스택 = **Elastic Beanstalk + CloudFront + RDS**. 모니터링 = **CloudWatch**.
- 역할 정의: LLM은 **탐지 대체가 아니라 그 위 "분석·요약·Q&A" 레이어**. 실시간 탐지는 CloudWatch Alarm 유지.

## 2. 확정 아키텍처

```
Hermes Agent (로컬 설치)  ─ 모델: Nous Portal(무료) 또는 Anthropic/Bedrock
   └─(MCP)→ CloudWatch MCP 서버  ── Docker 컨테이너(cloudwatch-mcp:0.1.4)
                 └─(read-only AWS)→ CloudWatch (ap-northeast-2)
                         · Alarms / Metrics / Logs
```
- **실행은 Docker** → 개발 서버 이관 시 같은 이미지를 ECR에 push해 동일 실행(락인 없음).
- 모델은 기존 Claude 구독으로는 직접 안 되고 **API 키/OAuth 필요**(아래 5). 로컬 모델(Ollama)은 폐기.

## 3. 완료된 사전 작업 (2026-06-15)

| 작업 | 결과 |
|------|------|
| Hermes Agent CLI 설치 | `C:\Users\SQI\AppData\Local\hermes\` (스킬 73개, uv/uvx 번들) |
| CloudWatch MCP 패키지 | `awslabs.cloudwatch-mcp-server==0.1.4` 검증 |
| 이식 가능 Dockerfile | `D:\SQ\hermes_agent\Dockerfile` (버전 고정, 실행법 주석) |
| Docker 이미지 빌드 | **`cloudwatch-mcp:0.1.4` (777MB)** — 스모크 테스트 통과(Logs/Metrics/Alarms 도구 등록 확인) |
| Hermes에 MCP 등록 | `config.yaml`의 `mcp_servers.cloudwatch` = **docker run** 방식 (백업: `config.yaml.bak.before-mcp`) |

> 공식 Docker Hub 이미지가 없어 **직접 빌드**했다. 이게 배포 이관에 오히려 유리.

## 4. 남은 단계 (대화형 — 본인 수행 필요)

1. **PATH 반영**: `hermes` 명령은 **새 터미널**에서 인식됨.
2. **모델 인증**: `hermes` 첫 실행 → setup 마법사
   - **Nous Portal 무료 OAuth**(추천, 키 불필요) 또는 **Anthropic API 키** 또는 **AWS Bedrock**
   - 현재 기본 모델 `anthropic/claude-opus-4.6`, provider `auto` → 인증 전엔 채팅 불가
3. **AWS 권한 확인**: 지금 `default` 프로파일 사용. read-only 여부 확인하거나 **전용 read-only 프로파일** 생성(정책 6).
4. **첫 검증**: `hermes` 에서 "ap-northeast-2 알람 상태 요약해줘" → cloudwatch MCP 도구 호출 확인.

## 5. 모델 백엔드 선택지 (참고)

| 경로 | 과금 | 비고 |
|------|------|------|
| Nous Portal OAuth | 무료 OAuth | 가장 빠른 시작(키 불필요) |
| AWS Bedrock + Haiku | AWS 청구 합산 | 데이터가 AWS 내 머묾, 이미 AWS 사용 → 자연스러움 |
| Anthropic API + Haiku | 토큰당 별도 | 셋업 간단, Haiku 저렴 |

→ 모니터링 요약 용도면 **Haiku급으로 충분**.

## 6. 전용 read-only IAM 정책 (권장)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory",
      "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
      "logs:DescribeLogGroups", "logs:DescribeQueryDefinitions",
      "logs:ListLogAnomalyDetectors", "logs:ListAnomalies",
      "logs:StartQuery", "logs:GetQueryResults", "logs:StopQuery"
    ],
    "Resource": "*"
  }]
}
```

## 7. 동작/비용 설계

- **평상시 알람 상태만 조회**(`DescribeAlarms`, 무료 API 한도), **알람 뜰 때만** 메트릭(`GetMetricData`, 과금)·로그(`StartQuery`, 스캔 GB당 과금).
- 기본 5분 메트릭·알람 10개까지 무료. 상시 폴링·대규모 로그 스캔만 피하면 월 푼돈.
- 실제 청구: Billing → Cost Explorer, 서비스=CloudWatch.

## 8. 개발 서버 이관 메모

- 같은 이미지 `cloudwatch-mcp:0.1.4`를 **ECR에 push** 후 개발 서버에서 pull.
- 서버에선 `-v ~/.aws` 마운트 **빼고** EC2 **인스턴스 IAM 역할**(정책 6) 사용:
  ```
  docker run --rm -i -e AWS_REGION=ap-northeast-2 -e FASTMCP_LOG_LEVEL=ERROR cloudwatch-mcp:0.1.4
  ```
- Hermes도 서버 배포 시 `config.yaml`의 `mcp_servers.cloudwatch.args`에서 마운트/AWS_PROFILE만 조정.

## 9. 파일 위치

- `D:\SQ\hermes_agent\Dockerfile` — MCP 이미지 정의
- `C:\Users\SQI\AppData\Local\hermes\config.yaml` — Hermes 설정(MCP 등록됨)
- `C:\Users\SQI\AppData\Local\hermes\.env` — API 키(인증 시 채워짐)
- `nous-research-hermes-20260615.md` — 배경 리서치

## 10. 참고 자료

- Hermes Agent: https://hermes-agent.nousresearch.com/docs/ · https://github.com/nousresearch/hermes-agent
- AWS CloudWatch MCP: https://awslabs.github.io/mcp/servers/cloudwatch-mcp-server · https://github.com/awslabs/mcp
- CloudWatch 비용: https://aws.amazon.com/cloudwatch/pricing/
