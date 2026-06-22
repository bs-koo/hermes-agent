/* dataviz-prod 운영 대시보드 프론트엔드 (Apple 디자인)
 *
 * - 패널별 독립 fetch(try/catch 격리) + setInterval 폴링
 * - 3-상태 렌더링(로딩/빈상태/정상), 빈 차트 미렌더
 * - traffic total===0 / quality buckets 합계 0 → 빈상태(빈 차트 금지)
 * - 기간 버튼: uptime 전용 1세트, traffic/quality 공유 1세트
 * - Chart.js 인스턴스는 재조회 시 destroy 후 재생성(메모리 누수 방지)
 * - 시각: 백엔드 epoch(UTC 기준) → +9h KST 표시 공통 유틸
 * - 보안: 외부유래 문자열(알람 name/reason, top_ep.key, top_err.key, 채팅 answer 등)은
 *         절대 innerHTML 문자열 concat 금지. 고정 골격만 innerHTML, 데이터 값은 textContent.
 *
 * API 키는 dashboard/api.py·aggregations.py 의 실제 응답 구조를 따른다(추측 금지).
 */
'use strict';

/* ── 폴링 주기(ms) ─────────────────────────────────────── */
var POLL = {
  alarms: 30000,
  uptime: 60000,
  traffic: 60000,
  meta: 30000
};

/* 트래픽/품질이 공유하는 기간(설계: 같은 period 공유) */
var trafficPeriod = 7;
/* 가동률 전용 기간 */
var uptimePeriod = 7;

/* Chart.js 인스턴스 보관(재조회 시 destroy 용) */
var charts = {};

/* ── Apple 색 토큰(JS 측 차트용) ───────────────────────── */
var C = {
  blue: '#0066cc',
  gray: '#86868b',
  ok: '#1d8a44',
  alarm: '#d70015',
  blue3: '#0066cc',
  amber: '#e8a33d',
  ink: '#1d1d1f'
};
/* 격자/축 — 옅은 hairline, muted 글자 */
var GRID = 'rgba(0,0,0,0.06)';
var TICK = '#6e6e73';
var SYS_FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif';

/* ── 공통 유틸 ─────────────────────────────────────────── */

/* epoch(초) → KST(+9h) "YYYY-MM-DD HH:MM" 문자열. null/0 은 "—" */
function epochToKst(epoch, withDate) {
  if (epoch === null || epoch === undefined || epoch === 0) return '—';
  var d = new Date((epoch + 9 * 3600) * 1000); // +9h, UTC 게터로 KST 표현
  var p = function (n) { return (n < 10 ? '0' : '') + n; };
  var hm = p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  if (withDate === false) return hm;
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + hm;
}

/* "N분 전" 상대 표현(현재 - epoch) */
function minutesAgo(epoch) {
  if (epoch === null || epoch === undefined) return '알 수 없음';
  var sec = Math.floor(Date.now() / 1000) - epoch;
  if (sec < 60) return sec + '초 전';
  var m = Math.floor(sec / 60);
  if (m < 60) return m + '분 전';
  var h = Math.floor(m / 60);
  if (h < 24) return h + '시간 ' + (m % 60) + '분 전';
  return Math.floor(h / 24) + '일 전';
}

/* 숫자 포맷(천단위 콤마). null → "—" */
function fmtNum(v) {
  if (v === null || v === undefined) return '—';
  return Number(v).toLocaleString('ko-KR');
}

/* 소수 자리 포맷. null → "—" */
function fmtFixed(v, digits) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(digits === undefined ? 1 : digits);
}

/* 시계열 x축 라벨: 기간 1일이면 "HH:MM", 그 외 "MM-DD" (KST).
 * Chart.js time 스케일(date adapter)에 의존하지 않기 위해 category 축 + 직접 라벨 사용. */
function tsLabel(epoch, period) {
  if (epoch === null || epoch === undefined) return '';
  var d = new Date((epoch + 9 * 3600) * 1000);
  var p = function (n) { return (n < 10 ? '0' : '') + n; };
  if (period === 1) return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  return p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate());
}

/* 패널의 3-상태 전환. root 안의 [data-state] 블록을 토글 */
function setState(root, state, message) {
  if (!root) return;
  var blocks = root.querySelectorAll(':scope > [data-state]');
  blocks.forEach(function (b) {
    b.hidden = (b.getAttribute('data-state') !== state);
  });
  if (state === 'error' && message) {
    var err = root.querySelector(':scope > [data-state="error"]');
    if (err) err.textContent = '불러오기 실패: ' + message;
  }
}

/* 차트 destroy 후 새로 생성. key 로 인스턴스 추적 */
function renderChart(key, canvasId, cfg) {
  if (charts[key]) {
    charts[key].destroy();
    charts[key] = null;
  }
  var el = document.getElementById(canvasId);
  if (!el) return;
  charts[key] = new Chart(el.getContext('2d'), cfg);
}

/* Chart.js 전역 폰트(시스템 폰트) — 1회 적용 */
if (typeof Chart !== 'undefined' && Chart.defaults) {
  Chart.defaults.font.family = SYS_FONT;
  Chart.defaults.color = TICK;
}

/* Chart.js 공통 라이트 테마 스케일 */
function baseScales(extra) {
  var s = {
    x: { grid: { color: GRID }, ticks: { color: TICK, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
    y: { grid: { color: GRID }, ticks: { color: TICK }, beginAtZero: true }
  };
  if (extra) Object.assign(s, extra);
  return s;
}
function legendTop() {
  return { display: true, position: 'top', labels: { color: C.ink, boxWidth: 12, font: { size: 12 } } };
}

/* fetch JSON 헬퍼(HTTP 에러를 throw) */
function fetchJson(url) {
  return fetch(url).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  });
}

/* ── 패널 0: 메타(상단 배너 + 마지막 갱신) ─────────────── */
function loadMeta() {
  fetchJson('/api/meta').then(function (d) {
    document.getElementById('meta-last-ok').textContent = epochToKst(d.last_ok_at);
    var banner = document.getElementById('stale-banner');
    if (d.stale) {
      banner.textContent = '수집 실패 (마지막 성공: ' + minutesAgo(d.last_ok_at) + ')';
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }).catch(function () {
    /* meta 실패는 배너만 미갱신(다른 패널 영향 없음) */
  });
}

/* ── 패널 1: 알람 ──────────────────────────────────────── */
function loadAlarms() {
  var card = document.getElementById('panel-alarms');
  var body = card.querySelector('.card-body');
  fetchJson('/api/alarms').then(function (d) {
    if (d.empty) { setState(body, 'empty'); document.getElementById('alarms-summary').textContent = ''; return; }
    var sum = d.summary || { total: 0, alarm: 0 };
    document.getElementById('alarms-summary').textContent =
      '총 ' + sum.total + ' · 경보 ' + sum.alarm;
    var ul = document.getElementById('alarm-list');
    ul.innerHTML = '';
    (d.items || []).forEach(function (it) {
      var li = document.createElement('li');
      var cls = it.state === 'OK' ? 'ok' : (it.state === 'ALARM' ? 'alarm' : 'insufficient');
      var badge = document.createElement('span');
      badge.className = 'badge ' + cls;
      var name = document.createElement('span');
      name.className = 'alarm-name';
      name.textContent = it.name;                 /* XSS: 외부유래 알람명 → textContent */
      var time = document.createElement('span');
      time.className = 'alarm-time';
      time.textContent = epochToKst(it.state_updated);
      if (it.reason) { name.title = it.reason; }   /* XSS: reason 은 title 속성(textContent 와 동급, concat 아님) */
      li.appendChild(badge); li.appendChild(name); li.appendChild(time);
      ul.appendChild(li);
    });
    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* ── 패널 2: 가동률·응답시간 ───────────────────────────── */
var EP_COLOR = { health: C.blue, home: C.gray };
var FALLBACK_COLORS = [C.blue, C.gray, C.amber, C.ok, C.alarm];
function colorFor(ep, idx) { return EP_COLOR[ep] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length]; }

function loadUptime() {
  var card = document.getElementById('panel-uptime');
  var body = card.querySelector('.card-body');
  fetchJson('/api/uptime?period=' + uptimePeriod).then(function (d) {
    if (d.empty) { setState(body, 'empty'); return; }
    var series = d.series || {};
    var eps = Object.keys(series);
    if (eps.length === 0) { setState(body, 'empty'); return; }

    /* 상단 summary24h KPI */
    var sumRow = document.getElementById('uptime-summary');
    sumRow.innerHTML = '';
    var s24 = d.summary24h || {};
    eps.forEach(function (ep) {
      var s = s24[ep] || {};
      var pct = (s.pct === null || s.pct === undefined) ? null : s.pct;
      var cls = pct === null ? '' : (pct >= 99.5 ? 'good' : (pct >= 95 ? 'warn' : 'bad'));
      var div = document.createElement('div');
      div.className = 'kpi ' + cls;
      div.innerHTML = '<span class="kpi-val"></span><span class="kpi-label"></span>';
      div.querySelector('.kpi-val').textContent = (pct === null ? '—' : fmtFixed(pct, 2) + '%');
      div.querySelector('.kpi-label').textContent = ep + ' 24h 가동률';  /* ep 는 textContent */
      sumRow.appendChild(div);
    });

    /* 공통 시간축(모든 endpoint 의 t 합집합, 오름차순) — category 축으로 사용 */
    var tset = {};
    eps.forEach(function (ep) { series[ep].forEach(function (pt) { tset[pt.t] = true; }); });
    var tsAll = Object.keys(tset).map(Number).sort(function (a, b) { return a - b; });
    var labels = tsAll.map(function (t) { return tsLabel(t, uptimePeriod); });
    /* endpoint별 t→값 빠른 조회 맵 */
    function mapBy(ep, field) {
      var m = {};
      series[ep].forEach(function (pt) { m[pt.t] = pt[field]; });
      return tsAll.map(function (t) { return m[t] === undefined ? null : m[t]; });
    }

    /* 가동률% 라인 차트 — endpoint별 데이터셋(메인=Action Blue, 보조=muted gray) */
    var pctDatasets = eps.map(function (ep, i) {
      var c = colorFor(ep, i);
      var secondary = (ep !== 'health' && i > 0);
      return {
        label: ep,
        data: mapBy(ep, 'pct'),
        borderColor: c,
        backgroundColor: c,
        borderWidth: 1.5, pointRadius: 0, tension: 0.2, spanGaps: true,
        borderDash: secondary ? [4, 3] : []   /* 보조 endpoint 는 점선 */
      };
    });
    renderChart('uptimePct', 'uptime-pct-chart', {
      type: 'line',
      data: { labels: labels, datasets: pctDatasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: legendTop(), title: { display: true, text: '가동률 %', color: TICK, font: { size: 12 } } },
        scales: baseScales({
          y: { grid: { color: GRID }, ticks: { color: TICK }, suggestedMin: 90, suggestedMax: 100 }
        })
      }
    });

    /* 응답시간(avg, p95) — endpoint × (avg/p95) 데이터셋. p95 는 점선 */
    var msDatasets = [];
    eps.forEach(function (ep, i) {
      var c = colorFor(ep, i);
      msDatasets.push({
        label: ep + ' avg',
        data: mapBy(ep, 'avg'),
        borderColor: c, backgroundColor: c, borderWidth: 1.5, pointRadius: 0, tension: 0.2, spanGaps: true
      });
      msDatasets.push({
        label: ep + ' p95',
        data: mapBy(ep, 'p95'),
        borderColor: c, backgroundColor: c, borderWidth: 1, borderDash: [4, 3], pointRadius: 0, tension: 0.2, spanGaps: true
      });
    });
    renderChart('uptimeMs', 'uptime-ms-chart', {
      type: 'line',
      data: { labels: labels, datasets: msDatasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: legendTop(), title: { display: true, text: '응답시간 (ms)', color: TICK, font: { size: 12 } } },
        scales: baseScales()
      }
    });

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* ── 패널 3: 트래픽·사용자 ─────────────────────────────── */
function loadTraffic() {
  var card = document.getElementById('panel-traffic');
  var body = card.querySelector('.card-body');
  fetchJson('/api/traffic?period=' + trafficPeriod).then(function (d) {
    /* empty 또는 total===0 → 빈상태(빈 차트 렌더 금지) */
    if (d.empty || !d.total) { setState(body, 'empty'); return; }

    /* KPI 숫자 */
    var sumRow = document.getElementById('traffic-summary');
    sumRow.innerHTML = '';
    [
      ['총 요청', fmtNum(d.total), ''],
      ['사용자(IP)', fmtNum(d.n_users), ''],
      ['스캐너 탐침', fmtNum(d.scanner_hits), d.scanner_hits > 0 ? 'warn' : '']
    ].forEach(function (row) {
      var div = document.createElement('div');
      div.className = 'kpi ' + row[2];
      div.innerHTML = '<span class="kpi-val"></span><span class="kpi-label"></span>';
      div.querySelector('.kpi-val').textContent = row[1];
      div.querySelector('.kpi-label').textContent = row[0];
      sumRow.appendChild(div);
    });

    /* Top API/화면 가로 막대 — 라벨(key)은 외부유래지만 Chart.js 가 canvas 에 텍스트로 그림(HTML 미주입) */
    var topEp = d.top_ep || [];
    if (topEp.length) {
      renderChart('trafficTopEp', 'traffic-topep-chart', {
        type: 'bar',
        data: {
          labels: topEp.map(function (x) { return x.key; }),
          datasets: [{ label: 'hits', data: topEp.map(function (x) { return x.hits; }), backgroundColor: C.blue, borderWidth: 0, borderRadius: 4 }]
        },
        options: {
          indexAxis: 'y',
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { legend: { display: false }, title: { display: true, text: 'Top API / 화면', color: TICK, font: { size: 12 } } },
          scales: {
            x: { grid: { color: GRID }, ticks: { color: TICK }, beginAtZero: true },
            y: { grid: { display: false }, ticks: { color: TICK, font: { size: 11 } } }
          }
        }
      });
    } else if (charts.trafficTopEp) { charts.trafficTopEp.destroy(); charts.trafficTopEp = null; }

    /* 시간대별 추세 라인 — Action Blue */
    var hourly = d.hourly || [];
    if (hourly.length) {
      renderChart('trafficHourly', 'traffic-hourly-chart', {
        type: 'line',
        data: {
          labels: hourly.map(function (h) { return tsLabel(h.t, trafficPeriod); }),
          datasets: [{
            label: '요청수',
            data: hourly.map(function (h) { return h.count; }),
            borderColor: C.blue, backgroundColor: 'rgba(0,102,204,0.10)',
            borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: true
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { legend: { display: false }, title: { display: true, text: '시간대별 추세', color: TICK, font: { size: 12 } } },
          scales: baseScales()
        }
      });
    } else if (charts.trafficHourly) { charts.trafficHourly.destroy(); charts.trafficHourly = null; }

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* ── 패널 4-a: 응답품질(상태코드/에러) ─────────────────── */
var BUCKET_ORDER = ['2xx', '3xx', '4xx', '5xx', '기타'];
var BUCKET_COLOR = { '2xx': C.ok, '3xx': C.blue, '4xx': C.amber, '5xx': C.alarm, '기타': C.gray };

function loadQuality() {
  var card = document.getElementById('panel-quality');
  var sub = card.querySelector('.subsection'); /* 첫 subsection = 품질 */
  fetchJson('/api/quality?period=' + trafficPeriod).then(function (d) {
    if (d.empty) { setState(sub, 'empty'); return; }

    /* 상태코드 도넛 — buckets 합계 0 이면 빈상태(빈 차트 금지) */
    var buckets = d.buckets || {};
    var labels = [], data = [], colors = [], bucketTotal = 0;
    BUCKET_ORDER.forEach(function (k) {
      var v = buckets[k] || 0;
      bucketTotal += v;
      if (v > 0) { labels.push(k); data.push(v); colors.push(BUCKET_COLOR[k]); }
    });
    if (bucketTotal === 0) { setState(sub, 'empty'); return; }

    if (data.length) {
      renderChart('qualityBuckets', 'quality-buckets-chart', {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: data, backgroundColor: colors, borderColor: '#ffffff', borderWidth: 2 }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          cutout: '62%',
          plugins: { legend: { display: true, position: 'right', labels: { color: C.ink, boxWidth: 12, font: { size: 12 } } },
            title: { display: true, text: '상태코드 분포', color: TICK, font: { size: 12 } } }
        }
      });
    } else if (charts.qualityBuckets) { charts.qualityBuckets.destroy(); charts.qualityBuckets = null; }

    /* 에러 Top 목록 */
    var ul = document.getElementById('top-err-list');
    ul.innerHTML = '';
    var topErr = d.top_err || [];
    if (topErr.length === 0) {
      var none = document.createElement('li');
      var noneSpan = document.createElement('span');
      noneSpan.className = 'err-none';
      noneSpan.textContent = '에러 없음';
      none.appendChild(noneSpan);
      ul.appendChild(none);
    } else {
      topErr.forEach(function (e) {
        var li = document.createElement('li');
        li.innerHTML = '<span class="err-key"></span><span class="err-hits"></span>';
        li.querySelector('.err-key').textContent = e.key;   /* XSS: 외부유래 err key → textContent */
        li.querySelector('.err-hits').textContent = fmtNum(e.hits);
        ul.appendChild(li);
      });
    }

    setState(sub, 'ok');
  }).catch(function (e) {
    setState(sub, 'error', e.message);
  });
}

/* ── 패널 4-b: DB(RDS) — /api/db 독립 격리 ─────────────── */
function loadDb() {
  var sub = document.getElementById('subsection-db');
  fetchJson('/api/db').then(function (d) {
    if (d.empty) { setState(sub, 'empty'); return; }
    var box = document.getElementById('db-kpis');
    box.innerHTML = '';
    /* aggregations.rds_perf 10항목 키(평탄화) */
    var cpu = d.cpu_avg;
    var freeGb = (d.free_storage === null || d.free_storage === undefined) ? null : d.free_storage / (1024 * 1024 * 1024);
    [
      ['CPU 평균', d.cpu_avg === null ? '—' : fmtFixed(d.cpu_avg, 1) + '%', cpu !== null && cpu >= 80 ? 'bad' : (cpu !== null && cpu >= 60 ? 'warn' : '')],
      ['CPU 최대', d.cpu_max === null ? '—' : fmtFixed(d.cpu_max, 1) + '%', ''],
      ['연결 평균', fmtFixed(d.conn_avg, 0), ''],
      ['연결 최대', fmtFixed(d.conn_max, 0), ''],
      ['읽기지연', d.read_lat === null ? '—' : fmtFixed(d.read_lat * 1000, 1) + 'ms', ''],
      ['쓰기지연', d.write_lat === null ? '—' : fmtFixed(d.write_lat * 1000, 1) + 'ms', ''],
      ['DBLoad 평균', fmtFixed(d.dbload_avg, 2), ''],
      ['DBLoad 최대', fmtFixed(d.dbload_max, 2), ''],
      ['디스크큐', fmtFixed(d.disk_q, 2), ''],
      ['여유공간', freeGb === null ? '—' : fmtFixed(freeGb, 1) + 'GB', freeGb !== null && freeGb < 5 ? 'bad' : (freeGb !== null && freeGb < 15 ? 'warn' : 'good')]
    ].forEach(function (row) {
      var div = document.createElement('div');
      div.className = 'kpi ' + row[2];
      div.innerHTML = '<span class="kpi-val"></span><span class="kpi-label"></span>';
      div.querySelector('.kpi-val').textContent = row[1];
      div.querySelector('.kpi-label').textContent = row[0];
      box.appendChild(div);
    });
    setState(sub, 'ok');
  }).catch(function (e) {
    setState(sub, 'error', e.message);
  });
}

/* ── 채팅 패널: POST /api/chat ─────────────────────────── */
var chatBusy = false;

/* 말풍선 추가(role: 'user'|'bot'|'error'|'loading'). 텍스트는 textContent 로만 주입 */
function appendChatMsg(role, text) {
  var wrap = document.getElementById('chat-messages');
  var msg = document.createElement('div');
  msg.className = 'chat-msg ' + role;
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  bubble.textContent = text;                       /* XSS: 사용자 입력·봇 answer → textContent */
  msg.appendChild(bubble);
  wrap.appendChild(msg);
  wrap.scrollTop = wrap.scrollHeight;
  return msg;
}

function sendChat() {
  if (chatBusy) return;
  var input = document.getElementById('chat-input');
  var btn = document.getElementById('chat-send');
  var q = (input.value || '').trim();
  if (!q) return;                                  /* 빈 질문 전송 금지 */

  appendChatMsg('user', q);
  input.value = '';

  /* 전송 중: 버튼 비활성 + 로딩 말풍선 */
  chatBusy = true;
  btn.disabled = true;
  input.disabled = true;
  var loading = appendChatMsg('loading', '답변 생성 중…');

  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q })
  }).then(function (r) {
    return r.json().then(function (j) { return { ok: r.ok, body: j }; });
  }).then(function (res) {
    if (loading.parentNode) loading.parentNode.removeChild(loading);
    var b = res.body || {};
    if (b.error) {
      appendChatMsg('error', b.error);
    } else if (b.answer !== undefined && b.answer !== null) {
      appendChatMsg('bot', b.answer);              /* XSS: answer → textContent, white-space:pre-wrap */
    } else {
      appendChatMsg('error', '응답을 이해하지 못했습니다');
    }
  }).catch(function (e) {
    if (loading.parentNode) loading.parentNode.removeChild(loading);
    appendChatMsg('error', '요청 실패: ' + e.message);
  }).then(function () {
    chatBusy = false;
    btn.disabled = false;
    input.disabled = false;
    input.focus();
  });
}

function bindChat() {
  var form = document.getElementById('chat-form');
  if (!form) return;
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();                           /* Enter/전송 모두 폼 submit 으로 통합 */
    sendChat();
  });
}

/* ── 트래픽/품질 공유 재조회 ───────────────────────────── */
function reloadTrafficGroup() {
  loadTraffic();
  loadQuality();
}

/* ── 기간 버튼 바인딩 ──────────────────────────────────── */
function bindPeriodButtons() {
  document.querySelectorAll('.period-btns').forEach(function (group) {
    var kind = group.getAttribute('data-period-group'); /* 'uptime' | 'traffic' */
    group.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var p = parseInt(btn.getAttribute('data-period'), 10);
        if (kind === 'uptime') {
          uptimePeriod = p;
          markActive('uptime', p);
          loadUptime();
        } else {
          trafficPeriod = p;
          markActive('traffic', p); /* traffic·quality 양쪽 버튼 세트 동기화 */
          reloadTrafficGroup();
        }
      });
    });
  });
}

/* 같은 그룹의 모든 버튼 세트에서 active 동기화(traffic 은 2개 세트) */
function markActive(kind, period) {
  document.querySelectorAll('.period-btns[data-period-group="' + kind + '"] button').forEach(function (b) {
    b.classList.toggle('active', parseInt(b.getAttribute('data-period'), 10) === period);
  });
}

/* ── 초기화 + 폴링 등록 ────────────────────────────────── */
function init() {
  bindPeriodButtons();
  bindChat();

  /* 최초 1회 */
  loadMeta();
  loadAlarms();
  loadUptime();
  loadTraffic();
  loadQuality();
  loadDb();

  /* 패널별 폴링 */
  setInterval(loadMeta, POLL.meta);
  setInterval(loadAlarms, POLL.alarms);
  setInterval(loadUptime, POLL.uptime);
  setInterval(loadTraffic, POLL.traffic);
  setInterval(loadQuality, POLL.traffic);
  setInterval(loadDb, POLL.traffic);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
