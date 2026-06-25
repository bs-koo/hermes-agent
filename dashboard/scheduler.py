# -*- coding: utf-8 -*-
"""백그라운드 수집 스케줄러.
단일 데몬 스레드가 60초 tick 으로 due 잡을 실행한다.
- 기동 즉시 1회 전 잡 실행 → 이후 각 잡의 주기(초)에 맞춰 due 면 실행
- 각 잡 호출을 try/except 로 격리(한 잡 예외가 루프를 죽이지 않음)
- 하루 1회 storage.purge_older_than(RETENTION_DAYS)
주의: uvicorn --workers 1 전제. 멀티워커면 스케줄러가 워커마다 떠
Logs Insights 중복 발사·SQLite 락 발생(BR-1 위반)."""
import sys
import time
import threading

from dashboard import config, storage, alerting
from dashboard.collectors import alarms, uptime, traffic, db, ec2, cloudfront, dooray, gcal

TICK = 60          # 루프 점검 주기(초)
PURGE_INTERVAL = 24 * 3600  # 하루 1회 purge
STARTUP_DELAY = 5  # 기동 직후 네트워크/자격증명 초기화 대기(첫 사이클 일시 실패 방지)


class Scheduler:
    """수집 잡들을 주기적으로 실행하는 데몬 스케줄러."""

    def __init__(self):
        # (name, interval_sec, run_fn)
        self.jobs = [
            ("alarms", config.ALARM_INTERVAL, alarms.run),
            ("uptime", config.UPTIME_INTERVAL, uptime.run),
            ("traffic", config.TRAFFIC_INTERVAL, traffic.run),
            ("db", config.DB_INTERVAL, db.run),
            ("host", config.HOST_INTERVAL, ec2.run),
            ("cdn", config.CDN_INTERVAL, cloudfront.run),
            ("dooray", config.DOORAY_INTERVAL, dooray.run),
            ("gcal", config.GCAL_INTERVAL, gcal.run),
            # 수집기들 뒤에 둬 같은 tick 에서 최신 스냅샷으로 신호를 평가·푸시한다.
            ("alerts", config.ALERT_INTERVAL, alerting.run),
        ]
        self.stop_event = threading.Event()
        self._thread = None
        self._last_run = {name: 0 for name, _, _ in self.jobs}
        self._last_purge = 0

    def start(self):
        """데몬 스레드로 _loop 를 기동한다."""
        if self._thread is not None and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="dash-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        """루프 중단 신호를 보낸다(데몬이라 join 은 선택)."""
        self.stop_event.set()

    def _run_job(self, name, fn, now):
        """단일 잡 실행을 try/except 로 격리한다(루프 보호).
        잡 내부는 base.run_job 이 이미 예외를 잡지만, 이중 안전망으로 한 번 더 감싼다."""
        try:
            fn(now)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[scheduler] job '{name}' raised (isolated): {e}\n")
        self._last_run[name] = now

    def _loop(self):
        # 기동 직후 네트워크/자격증명 초기화 대기(첫 사이클 일시 실패 방지).
        # 대기 중 stop 신호가 오면 즉시 종료.
        if self.stop_event.wait(STARTUP_DELAY):
            return
        # 기동 후 1회 전 잡 실행
        now = int(time.time())
        for name, _, fn in self.jobs:
            self._run_job(name, fn, now)
        self._last_purge = now

        # 이후 60초 tick
        while not self.stop_event.wait(TICK):
            now = int(time.time())
            for name, interval, fn in self.jobs:
                if now - self._last_run[name] >= interval:
                    self._run_job(name, fn, now)
            # 하루 1회 purge
            if now - self._last_purge >= PURGE_INTERVAL:
                try:
                    storage.purge_older_than(config.RETENTION_DAYS)
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"[scheduler] purge failed: {e}\n")
                self._last_purge = now
