# -*- coding: utf-8 -*-
"""수집기 공통 헬퍼.
run_job 은 fn 실행을 try/except 로 감싸 collect_meta 에 성공/실패를 기록한다.
어떤 예외가 나도 절대 raise 하지 않는다(스케줄러 루프 보호 — FR-17/19)."""
import sys
import time
import traceback

from dashboard import storage


def run_job(job_name, fn):
    """fn() 을 실행하고 결과를 collect_meta 에 기록한다.
    성공: set_collect_meta(job, ok=True)
    실패: set_collect_meta(job, ok=False, error=str(e)) + stderr 로그.
    반환: 성공 True / 실패 False. 절대 예외를 전파하지 않는다."""
    at = int(time.time())
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — 어떤 예외든 잡아 메타에만 기록
        sys.stderr.write(f"[collector:{job_name}] FAILED: {e}\n")
        traceback.print_exc(file=sys.stderr)
        try:
            storage.set_collect_meta(job_name, ok=False, error=str(e), at=at)
        except Exception as meta_e:  # noqa: BLE001
            sys.stderr.write(f"[collector:{job_name}] meta write failed: {meta_e}\n")
        return False
    try:
        storage.set_collect_meta(job_name, ok=True, at=at)
    except Exception as meta_e:  # noqa: BLE001
        sys.stderr.write(f"[collector:{job_name}] meta write failed: {meta_e}\n")
    return True
