# 대시보드 IAM 확장(Tier B, read-only) — 적용 가이드

> 대상 IAM 사용자: `hermes-cloudwatch-ro` (계정 062364668115)
> 목적: 인사이트를 더 깊게 뽑기 위한 **읽기 전용·무료** 권한 추가. 쓰기/삭제 없음.
> Cost Explorer(과금)·CloudWatch Agent(재배포)는 의도적으로 제외.

## 추가 권한이 주는 인사이트

| 권한 | 새로 가능한 것 |
|---|---|
| `rds:DescribeDBInstances` | DB 엔진/버전/인스턴스클래스/스토리지타입/멀티AZ/PI활성 여부 메타 |
| `pi:GetResourceMetrics` 외 PI 3종 | **Performance Insights — Top SQL·대기이벤트(어떤 쿼리가 DB를 잡아먹나)**. PI가 활성화된 인스턴스만 |
| `cloudfront:ListDistributions`, `GetDistribution` | 배포 ID→**이름/도메인/오리진** (지금은 암호 같은 ID만 표시) |
| `elasticloadbalancing:Describe*` | (현재 LB 없음, 미래 대비) 타겟 헬스·LB 메타 |
| `s3:ListAllMyBuckets` | S3 버킷 목록 발견 → 스토리지 추세(BucketSizeBytes는 CloudWatch로 이미 읽힘) |
| `ec2:DescribeVolumes`, `DescribeAddresses` | EBS 볼륨·미사용 EIP 인벤토리(낭비 탐지) |

> 참고: ALB/CLB는 이 계정에 **존재하지 않음**(검증 완료 — 단일 인스턴스 EB). 트래픽/응답시간의 실데이터 대안이 AWS 메트릭에 없어, 대시보드는 CloudFront와 인사이트 룰로 보완한다.

## 적용 방법 (둘 중 하나)

### A. AWS 콘솔
1. IAM → 사용자 → `hermes-cloudwatch-ro` → 권한 추가 → 인라인 정책 생성
2. JSON 탭에 아래 정책 붙여넣기 → 정책 이름 `dashboard-tierB-readonly` → 생성

### B. AWS CLI (관리자 자격증명으로)
```bash
aws iam put-user-policy \
  --user-name hermes-cloudwatch-ro \
  --policy-name dashboard-tierB-readonly \
  --policy-document file://iam-dashboard-tierB-readonly.json
```

## 정책 JSON

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DashboardTierBReadOnly",
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "pi:GetResourceMetrics",
        "pi:DescribeDimensionKeys",
        "pi:GetDimensionKeyDetails",
        "pi:ListAvailableResourceMetrics",
        "cloudfront:ListDistributions",
        "cloudfront:GetDistribution",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DescribeTargetHealth",
        "s3:ListAllMyBuckets",
        "ec2:DescribeVolumes",
        "ec2:DescribeAddresses"
      ],
      "Resource": "*"
    }
  ]
}
```

## 적용 후 활성화되는 대시보드 기능

- **DB 성능**: 인스턴스 메타(엔진/클래스/멀티AZ) 표시 + (PI 활성 시) Top SQL 패널
- **CDN**: 배포 ID 대신 사람이 읽는 이름/도메인 표시
- 위 권한이 없어도 대시보드는 **graceful** 동작(해당 항목만 비표시, 기존 기능 회귀 없음)

> 적용은 선택입니다. 권한을 추가하지 않아도 현재 인사이트/패널은 정상 작동합니다.
> 권한 추가 후에는 코드 측에서 PI·메타 수집을 연결하는 후속 작업(별도)이 필요합니다.
