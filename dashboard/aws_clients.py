# -*- coding: utf-8 -*-
"""boto3 클라이언트 lazy 팩토리.
첫 호출 시 한 번만 생성해 재사용한다(프로세스 수명 동안 캐시).
자격증명 누락/클라이언트 생성 실패 시 예외를 그대로 호출측에 전파한다
(수집기 base.run_job 이 잡아 collect_meta 에 기록 → 절대 죽지 않음)."""
import boto3
from dashboard import config

_cw = None
_cwe = None
_ec2 = None
_logs = None


def cw():
    """CloudWatch 클라이언트(ap-northeast-2)를 반환한다."""
    global _cw
    if _cw is None:
        _cw = boto3.client("cloudwatch", region_name=config.REGION)
    return _cw


def ec2():
    """EC2 클라이언트(ap-northeast-2) — 인스턴스 메타(IP/이름/타입) 조회용.
    describe_instances 권한이 없을 수 있으므로 호출측에서 graceful 처리한다."""
    global _ec2
    if _ec2 is None:
        _ec2 = boto3.client("ec2", region_name=config.REGION)
    return _ec2


def cwe():
    """CloudWatch 클라이언트(us-east-1) — CloudFront 글로벌 메트릭 전용.
    CloudFront 메트릭은 AWS/CloudFront 네임스페이스로 us-east-1 에만 존재한다."""
    global _cwe
    if _cwe is None:
        _cwe = boto3.client("cloudwatch", region_name="us-east-1")
    return _cwe


def logs():
    """CloudWatch Logs 클라이언트(ap-northeast-2)를 반환한다."""
    global _logs
    if _logs is None:
        _logs = boto3.client("logs", region_name=config.REGION)
    return _logs
