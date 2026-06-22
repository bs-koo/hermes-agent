# -*- coding: utf-8 -*-
"""CloudFront CDN 메트릭 수집기(10분 주기).
aws_clients.cwe(us-east-1) 로 list_metrics 에서 모든 DistributionId 를 발견하고
배포별로 aggregations.cloudfront_metrics(최근 24h)를 호출해 요약 + 시계열을 모은 뒤
cdn_snapshot(id=1) 단일행에 {"distributions":[...], ...} payload 로 교체 저장한다.
CloudFront 메트릭은 us-east-1(글로벌)에만 존재하므로 cw 가 아닌 cwe 를 쓴다.
get_metric_data/list_metrics(read-only)만 사용한다.
한 배포의 메트릭 실패가 전체 수집을 막지 않도록 배포별 try/except 로 격리하고,
유효 데이터가 0(배포 없음 또는 전부 실패)이면 이전 캐시를 보존(저장 skip)한다."""
import sys
import json
import datetime

from dashboard import aws_clients, storage, aggregations
from dashboard.collectors import base


def _dist_has_data(dist):
    """한 배포 dict 가 유효한 수집값을 담았는지 판정한다.
    error 표시이거나 요약 전부 None + 시계열 전부 빈 경우 False."""
    if dist.get("error"):
        return False
    if dist.get("requests_series") or dist.get("err_total_series"):
        return True
    for k, v in dist.items():
        if k in ("dist_id", "requests_series", "err_total_series", "error"):
            continue
        if v is not None:
            return True
    return False


def _collect(now):
    cwe = aws_clients.cwe()
    t1 = int(now.timestamp()) if isinstance(now, datetime.datetime) else int(now)
    t0 = t1 - 24 * 3600

    dists = aggregations.list_cloudfront_dists(cwe)

    distributions = []
    for dist in dists:
        try:
            distributions.append(aggregations.cloudfront_metrics(cwe, t0, t1, dist))
        except Exception as e:  # noqa: BLE001 — 한 배포 실패가 전체를 막지 않게 격리
            sys.stderr.write(f"[collector:cdn] distribution {dist} 메트릭 실패: {e}\n")
            distributions.append({
                "dist_id": dist,
                "requests": None, "bytes_down": None, "bytes_up": None,
                "err_4xx": None, "err_5xx": None, "err_total": None,
                "requests_series": [], "err_total_series": [],
                "error": True,
            })

    # 유효 데이터가 0(배포 없음 또는 전부 error/빈)이면 이전 캐시 보존(저장 skip).
    if not any(_dist_has_data(d) for d in distributions):
        sys.stderr.write("[collector:cdn] 유효 데이터 0 — 이전 스냅샷 보존(저장 skip)\n")
        return

    payload = {
        "distributions": distributions,
        "collected_at": t1,
    }
    storage.replace_cdn_snapshot(json.dumps(payload, ensure_ascii=False), t1)


def run(now):
    """CloudFront CDN 수집 1회 실행(base.run_job 으로 감싸 예외 격리)."""
    return base.run_job("cdn", lambda: _collect(now))
