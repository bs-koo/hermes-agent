# -*- coding: utf-8 -*-
"""boto3 클라이언트 lazy 팩토리.
첫 호출 시 한 번만 생성해 재사용한다(프로세스 수명 동안 캐시).
자격증명 누락/클라이언트 생성 실패 시 예외를 그대로 호출측에 전파한다
(수집기 base.run_job 이 잡아 collect_meta 에 기록 → 절대 죽지 않음)."""
import boto3
from dashboard import config

_cw = None
_logs = None


def cw():
    """CloudWatch 클라이언트(ap-northeast-2)를 반환한다."""
    global _cw
    if _cw is None:
        _cw = boto3.client("cloudwatch", region_name=config.REGION)
    return _cw


def logs():
    """CloudWatch Logs 클라이언트(ap-northeast-2)를 반환한다."""
    global _logs
    if _logs is None:
        _logs = boto3.client("logs", region_name=config.REGION)
    return _logs
