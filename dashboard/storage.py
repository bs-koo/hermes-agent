# -*- coding: utf-8 -*-
"""SQLite 스토리지 계층(설계서 6테이블 스키마 SSOT).
시계열(uptime_bucket, alarm_history)은 purge 대상, 스냅샷 4테이블은 교체식이라 purge 제외.
매 호출마다 connect/close 하는 단순 패턴(check_same_thread 기본값 사용).
수집기 스레드와 FastAPI 메인 스레드가 같은 DB 파일을 공유하나 WAL + 짧은 트랜잭션으로 처리."""
import os
import json
import time
import sqlite3

from dashboard import config


# ── 연결/초기화 ───────────────────────────────────────────────────────
def connect():
    """DASH_DB 파일에 새 연결을 연다. 디렉토리가 없으면 생성한다."""
    db_path = config.DASH_DB
    d = os.path.dirname(db_path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """테이블 6종을 CREATE IF NOT EXISTS 하고 WAL 모드를 설정한다."""
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS alarm_state (
          alarm_name TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          state_reason TEXT,
          state_updated INTEGER,
          collected_at INTEGER NOT NULL,
          detail_json TEXT);

        CREATE TABLE IF NOT EXISTS alarm_history (
          alarm_name TEXT NOT NULL,
          state TEXT NOT NULL,
          state_updated INTEGER NOT NULL,
          UNIQUE(alarm_name, state_updated));

        CREATE TABLE IF NOT EXISTS uptime_bucket (
          endpoint TEXT NOT NULL,
          bucket_start INTEGER NOT NULL,
          ok_count INTEGER NOT NULL,
          total_count INTEGER NOT NULL,
          ms_avg REAL,
          ms_p95 REAL,
          UNIQUE(endpoint, bucket_start));

        CREATE TABLE IF NOT EXISTS traffic_snapshot (
          period_days INTEGER PRIMARY KEY,
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS db_snapshot (
          id INTEGER PRIMARY KEY CHECK (id=1),
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS host_snapshot (
          id INTEGER PRIMARY KEY CHECK (id=1),
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS cdn_snapshot (
          id INTEGER PRIMARY KEY CHECK (id=1),
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS dooray_snapshot (
          id INTEGER PRIMARY KEY CHECK (id=1),
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS dooray_layout (
          id INTEGER PRIMARY KEY CHECK (id=1),
          layout_json TEXT NOT NULL,
          updated_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS gcal_snapshot (
          id INTEGER PRIMARY KEY CHECK (id=1),
          payload_json TEXT NOT NULL,
          collected_at INTEGER NOT NULL);

        CREATE TABLE IF NOT EXISTS dooray_task_history (
          month TEXT NOT NULL,
          tag TEXT NOT NULL,
          subject TEXT NOT NULL,
          status TEXT,
          wfclass TEXT,
          assignee TEXT,
          week TEXT,
          body TEXT,
          first_at INTEGER,
          last_at INTEGER,
          PRIMARY KEY (month, tag, subject));

        CREATE TABLE IF NOT EXISTS collect_meta (
          job TEXT PRIMARY KEY,
          last_ok_at INTEGER,
          last_run_at INTEGER,
          last_status TEXT,
          last_error TEXT);

        CREATE TABLE IF NOT EXISTS alert_state (
          alert_key TEXT PRIMARY KEY,
          severity TEXT, area TEXT, title TEXT, evidence TEXT,
          first_seen INTEGER);
        """)
        # 마이그레이션: 기존 named volume 의 alarm_state 에 detail_json 이 없으면 추가.
        # 새 DB 는 위 CREATE 에 이미 포함되므로 이 ALTER 는 "이미 있음" 에러로 무시된다.
        try:
            conn.execute("ALTER TABLE alarm_state ADD COLUMN detail_json TEXT")
        except sqlite3.OperationalError:
            pass  # duplicate column name → 이미 존재(정상)
        conn.commit()
    finally:
        conn.close()


# ── 알람(스냅샷 + 이력) ───────────────────────────────────────────────
def upsert_alarm_state(rows):
    """알람 상태 스냅샷을 교체 upsert 한다.
    rows: [{alarm_name, state, state_reason, state_updated, collected_at, detail_json}]
    detail_json 은 선택(없으면 None 으로 저장)."""
    if not rows:
        return
    conn = connect()
    try:
        conn.executemany("""
            INSERT INTO alarm_state
              (alarm_name, state, state_reason, state_updated, collected_at, detail_json)
            VALUES
              (:alarm_name, :state, :state_reason, :state_updated, :collected_at,
               :detail_json)
            ON CONFLICT(alarm_name) DO UPDATE SET
              state=excluded.state,
              state_reason=excluded.state_reason,
              state_updated=excluded.state_updated,
              collected_at=excluded.collected_at,
              detail_json=excluded.detail_json
        """, rows)
        conn.commit()
    finally:
        conn.close()


def insert_alarm_history_if_changed(name, state, ts):
    """상태 전환 이력을 멱등 기록(UNIQUE 충돌 시 무시)."""
    conn = connect()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO alarm_history (alarm_name, state, state_updated)
            VALUES (?, ?, ?)
        """, (name, state, ts))
        conn.commit()
    finally:
        conn.close()


# ── 가동률 버킷(1h, 멱등 upsert) ─────────────────────────────────────
def append_uptime_buckets(rows):
    """1시간 버킷 집계를 UNIQUE(endpoint, bucket_start) 기준 upsert 한다.
    rows: [{endpoint, bucket_start, ok_count, total_count, ms_avg, ms_p95}]"""
    if not rows:
        return
    conn = connect()
    try:
        conn.executemany("""
            INSERT INTO uptime_bucket
              (endpoint, bucket_start, ok_count, total_count, ms_avg, ms_p95)
            VALUES
              (:endpoint, :bucket_start, :ok_count, :total_count, :ms_avg, :ms_p95)
            ON CONFLICT(endpoint, bucket_start) DO UPDATE SET
              ok_count=excluded.ok_count,
              total_count=excluded.total_count,
              ms_avg=excluded.ms_avg,
              ms_p95=excluded.ms_p95
        """, rows)
        conn.commit()
    finally:
        conn.close()


# ── 스냅샷 교체(트래픽/DB) ────────────────────────────────────────────
def replace_traffic_snapshot(period_days, payload_json, at):
    """기간별 트래픽 스냅샷 1행을 교체한다. payload_json 은 직렬화된 문자열."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO traffic_snapshot (period_days, payload_json, collected_at)
            VALUES (?, ?, ?)
            ON CONFLICT(period_days) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (period_days, payload_json, at))
        conn.commit()
    finally:
        conn.close()


def replace_db_snapshot(payload_json, at):
    """DB 스냅샷(id=1 단일행)을 교체한다."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO db_snapshot (id, payload_json, collected_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (payload_json, at))
        conn.commit()
    finally:
        conn.close()


def replace_host_snapshot(payload_json, at):
    """EC2 호스트 스냅샷(id=1 단일행)을 교체한다(db_snapshot 과 동일 패턴)."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO host_snapshot (id, payload_json, collected_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (payload_json, at))
        conn.commit()
    finally:
        conn.close()


def replace_cdn_snapshot(payload_json, at):
    """CloudFront CDN 스냅샷(id=1 단일행)을 교체한다(host_snapshot 과 동일 패턴)."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO cdn_snapshot (id, payload_json, collected_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (payload_json, at))
        conn.commit()
    finally:
        conn.close()


# ── 수집 메타(잡별 상태) ──────────────────────────────────────────────
def replace_dooray_snapshot(payload_json, at):
    """Dooray 업무 스냅샷(id=1 단일행)을 교체한다."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO dooray_snapshot (id, payload_json, collected_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (payload_json, at))
        conn.commit()
    finally:
        conn.close()


def get_dooray():
    """Dooray 업무 스냅샷(id=1)을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("SELECT payload_json, collected_at FROM dooray_snapshot WHERE id = 1")
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]), "collected_at": row["collected_at"]}
    finally:
        conn.close()


def get_dooray_layout():
    """주간보고 구성(레이아웃) JSON 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("SELECT layout_json, updated_at FROM dooray_layout WHERE id = 1")
        row = cur.fetchone()
        if row is None:
            return None
        return {"layout": json.loads(row["layout_json"]), "updated_at": row["updated_at"]}
    finally:
        conn.close()


def set_dooray_layout(layout_json, at):
    """주간보고 구성(레이아웃, id=1 단일행)을 교체한다. 누구나 수정(공용)."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO dooray_layout (id, layout_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              layout_json=excluded.layout_json,
              updated_at=excluded.updated_at
        """, (layout_json, at))
        conn.commit()
    finally:
        conn.close()


def replace_gcal_snapshot(payload_json, at):
    """Google Calendar 일정 스냅샷(id=1 단일행)을 교체한다."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO gcal_snapshot (id, payload_json, collected_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              collected_at=excluded.collected_at
        """, (payload_json, at))
        conn.commit()
    finally:
        conn.close()


def get_gcal():
    """Google Calendar 스냅샷(id=1)을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("SELECT payload_json, collected_at FROM gcal_snapshot WHERE id = 1")
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]), "collected_at": row["collected_at"]}
    finally:
        conn.close()


def upsert_dooray_history(rows):
    """월간 누적: (month, tag, subject) 단위로 업무를 upsert.
    rows: [{month, tag, subject, status, wfclass, assignee, week, body, last_at}]
    first_at 은 최초 1회만 기록(이후 보존), body 는 더 긴(완전한) 쪽을 유지."""
    if not rows:
        return
    conn = connect()
    try:
        conn.executemany("""
            INSERT INTO dooray_task_history
              (month, tag, subject, status, wfclass, assignee, week, body, first_at, last_at)
            VALUES
              (:month, :tag, :subject, :status, :wfclass, :assignee, :week, :body, :last_at, :last_at)
            ON CONFLICT(month, tag, subject) DO UPDATE SET
              status=excluded.status,
              wfclass=excluded.wfclass,
              assignee=excluded.assignee,
              week=excluded.week,
              body=CASE WHEN length(COALESCE(excluded.body,'')) >= length(COALESCE(dooray_task_history.body,''))
                        THEN excluded.body ELSE dooray_task_history.body END,
              last_at=excluded.last_at
        """, rows)
        conn.commit()
    finally:
        conn.close()


def get_dooray_history(month):
    """해당 월(YYYY-MM)의 누적 업무 행 목록(tag·최초기록순)."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT month, tag, subject, status, wfclass, assignee, week, body, first_at, last_at
            FROM dooray_task_history WHERE month = ? ORDER BY tag, first_at
        """, (month,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_dooray_history_months():
    """누적 데이터가 있는 월(YYYY-MM) 목록(최신순)."""
    conn = connect()
    try:
        cur = conn.execute("SELECT DISTINCT month FROM dooray_task_history ORDER BY month DESC")
        return [r["month"] for r in cur.fetchall()]
    finally:
        conn.close()


def set_collect_meta(job, ok, error=None, at=None):
    """잡 실행 결과를 기록한다. last_run_at 은 항상, last_ok_at 은 ok 일 때만 갱신."""
    if at is None:
        at = int(time.time())
    status = "ok" if ok else "error"
    ok_at = at if ok else None
    conn = connect()
    try:
        # 신규 행: ok 면 last_ok_at 도 채움. 기존 행: last_ok_at 은 ok 일 때만 덮어씀.
        conn.execute("""
            INSERT INTO collect_meta (job, last_ok_at, last_run_at, last_status, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job) DO UPDATE SET
              last_run_at=excluded.last_run_at,
              last_status=excluded.last_status,
              last_error=excluded.last_error,
              last_ok_at=CASE WHEN excluded.last_status='ok'
                              THEN excluded.last_ok_at
                              ELSE collect_meta.last_ok_at END
        """, (job, ok_at, at, status, error))
        conn.commit()
    finally:
        conn.close()


def get_collect_meta():
    """모든 잡의 메타를 [{job, last_ok_at, last_run_at, last_status, last_error}] 로 반환."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT job, last_ok_at, last_run_at, last_status, last_error
            FROM collect_meta
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── 패널별 조회 ───────────────────────────────────────────────────────
def get_alarms():
    """알람 상태 스냅샷 전체를 state_updated 최신순으로 반환."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT alarm_name, state, state_reason, state_updated, collected_at,
                   detail_json
            FROM alarm_state
            ORDER BY (state='ALARM') DESC, alarm_name ASC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_alarm_history(name):
    """특정 알람의 상태 전환 이력을 최신순으로 반환."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT alarm_name, state, state_updated
            FROM alarm_history
            WHERE alarm_name = ?
            ORDER BY state_updated DESC
        """, (name,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_uptime(period_days):
    """기간 내 가동률 버킷을 endpoint, bucket_start 오름차순으로 반환."""
    cutoff = int(time.time()) - period_days * 24 * 3600
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT endpoint, bucket_start, ok_count, total_count, ms_avg, ms_p95
            FROM uptime_bucket
            WHERE bucket_start >= ?
            ORDER BY endpoint ASC, bucket_start ASC
        """, (cutoff,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_traffic(period_days):
    """기간별 트래픽 스냅샷 1행을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT payload_json, collected_at
            FROM traffic_snapshot
            WHERE period_days = ?
        """, (period_days,))
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]),
                "collected_at": row["collected_at"]}
    finally:
        conn.close()


def get_db():
    """DB 스냅샷(id=1)을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT payload_json, collected_at FROM db_snapshot WHERE id = 1
        """)
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]),
                "collected_at": row["collected_at"]}
    finally:
        conn.close()


def get_host():
    """EC2 호스트 스냅샷(id=1)을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT payload_json, collected_at FROM host_snapshot WHERE id = 1
        """)
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]),
                "collected_at": row["collected_at"]}
    finally:
        conn.close()


def get_cdn():
    """CloudFront CDN 스냅샷(id=1)을 {payload, collected_at} 로 반환. 없으면 None."""
    conn = connect()
    try:
        cur = conn.execute("""
            SELECT payload_json, collected_at FROM cdn_snapshot WHERE id = 1
        """)
        row = cur.fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload_json"]),
                "collected_at": row["collected_at"]}
    finally:
        conn.close()


# ── 주의 신호 알림 상태(Chat 푸시 중복 방지) ─────────────────────────
def get_active_alerts():
    """현재 '발송됨' 상태로 추적 중인 주의 신호 목록."""
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT alert_key, severity, area, title, evidence, first_seen FROM alert_state")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_active_alert(alert_key, severity, area, title, evidence, first_seen):
    """발송한 신호를 활성 상태로 기록(이미 있으면 갱신)."""
    conn = connect()
    try:
        conn.execute("""
            INSERT INTO alert_state (alert_key, severity, area, title, evidence, first_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET
              severity=excluded.severity, area=excluded.area,
              title=excluded.title, evidence=excluded.evidence
        """, (alert_key, severity, area, title, evidence, first_seen))
        conn.commit()
    finally:
        conn.close()


def delete_active_alerts(keys):
    """해소된 신호를 활성 목록에서 제거."""
    if not keys:
        return
    conn = connect()
    try:
        conn.executemany("DELETE FROM alert_state WHERE alert_key = ?", [(k,) for k in keys])
        conn.commit()
    finally:
        conn.close()


# ── 보관(purge) ───────────────────────────────────────────────────────
def purge_older_than(days):
    """시계열 2테이블(uptime_bucket, alarm_history)에서 days 초과분을 삭제한다.
    스냅샷 4테이블은 교체식이라 purge 제외."""
    cutoff = int(time.time()) - days * 24 * 3600
    conn = connect()
    try:
        conn.execute("DELETE FROM uptime_bucket WHERE bucket_start < ?", (cutoff,))
        conn.execute("DELETE FROM alarm_history WHERE state_updated < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
