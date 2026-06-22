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

/* ── 토스(Toss) 라이트 색 토큰(JS 측 차트용) ───────────── */
var C = {
  blue: '#4b91f7',     /* 주 라인/막대(토스블루) */
  blue2: '#9aa0aa',    /* 보조 라인(muted) */
  gray: '#6b6f79',     /* 옅은 회색 라인 */
  ok: '#3dd68c',       /* success */
  alarm: '#ff5c5c',    /* error */
  amber: '#ffb020',    /* warning */
  ink: '#f4f5f7'       /* 라이트 위 진한 글자(범례 등) */
};
/* 격자/축 — 라이트 위 옅은 격자, muted 축 글자 */
var GRID = '#2a2c33';
var TICK = '#9aa0aa';
var SYS_FONT = 'Pretendard, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif';

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

/* bytes → 사람친화 문자열(KB/MB/GB). null → "—" */
function fmtBytes(v, digits) {
  if (v === null || v === undefined) return '—';
  var n = Number(v);
  var dg = (digits === undefined) ? 1 : digits;
  if (n < 1024) return n.toFixed(0) + 'B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(dg) + 'KB';
  if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(dg) + 'MB';
  return (n / (1024 * 1024 * 1024)).toFixed(dg) + 'GB';
}

/* bytes → MB(숫자). 차트 축 통일용. null → null */
function bytesToMb(v) {
  if (v === null || v === undefined) return null;
  return Number(v) / (1024 * 1024);
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

/* Chart.js 전역 기본 — 폰트 + 호버 인터랙션 + 다크 툴팁(모든 차트 일괄 적용) */
if (typeof Chart !== 'undefined' && Chart.defaults) {
  Chart.defaults.font.family = SYS_FONT;
  Chart.defaults.color = TICK;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.responsive = true;
  /* Chart.js v4: 중첩 기본값(interaction/plugins.tooltip)은 직접 할당/참조설정이
     반영되지 않는다(단순 속성 color/font 만 먹음). set() 으로 머지해야 한다.
     이게 누락되면 intersect:true 기본값이 남아 호버 툴팁이 안 뜬다. */
  Chart.defaults.set('interaction', { mode: 'index', intersect: false });
  Chart.defaults.set('plugins.tooltip', {
    enabled: true,
    backgroundColor: '#23252c', borderColor: '#2a2c33', borderWidth: 1,
    titleColor: '#f4f5f7', bodyColor: '#cfd3da', padding: 10, cornerRadius: 8,
    displayColors: true, titleFont: { size: 12, weight: '700' }, bodyFont: { size: 12 }
  });
}

/* 시계열 툴팁 title: dataIndex → epoch 배열에서 KST 'MM-DD HH:MM' */
function tsTitleCb(epochs) {
  return function (items) {
    if (!items || !items.length) return '';
    var e = epochs[items[0].dataIndex];
    if (e === null || e === undefined) return '';
    var d = new Date((e + 9 * 3600) * 1000);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' +
      p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  };
}

/* 라인/면적 포인트 기본(호버 타겟 확보 — pointRadius 0 금지) */
function pointOpts(secondary) {
  return {
    pointRadius: secondary ? 1.5 : 2,
    pointHoverRadius: 5,
    pointHitRadius: 8
  };
}

/* 값 라벨 콜백 생성(단위 붙이기). 'pct'|'ms'|'cnt' */
function labelUnitCb(kind, prefix) {
  return function (item) {
    var v = item.parsed.y;
    if (v === null || v === undefined) return null;
    var head = (prefix ? prefix : (item.dataset.label || '')) + ': ';
    if (kind === 'pct') return head + fmtFixed(v, 1) + '%';
    if (kind === 'ms') return head + fmtNum(Math.round(v)) + 'ms';
    return head + fmtNum(v) + '건';
  };
}

/* inline plugin: 가로막대 끝에 값 라벨(작은 텍스트) */
var barValuePlugin = {
  id: 'barValue',
  afterDatasetsDraw: function (chart) {
    var ctx = chart.ctx;
    var meta = chart.getDatasetMeta(0);
    if (!meta || meta.hidden) return;
    ctx.save();
    ctx.fillStyle = '#9aa0aa';
    ctx.font = '11px ' + SYS_FONT;
    ctx.textBaseline = 'middle';
    var data = chart.data.datasets[0].data;
    meta.data.forEach(function (bar, i) {
      var v = data[i];
      if (v === null || v === undefined) return;
      ctx.textAlign = 'left';
      ctx.fillText(fmtNum(v), bar.x + 6, bar.y);   /* 막대 끝 오른쪽에 값 */
    });
    ctx.restore();
  }
};

/* inline plugin: 도넛 중앙에 총 요청수 표시 */
function donutCenterPlugin(total) {
  return {
    id: 'donutCenter',
    afterDraw: function (chart) {
      var ctx = chart.ctx;
      var area = chart.chartArea;
      if (!area) return;
      var cx = (area.left + area.right) / 2;
      var cy = (area.top + area.bottom) / 2;
      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#f4f5f7';
      ctx.font = '700 22px ' + SYS_FONT;
      ctx.fillText(fmtNum(total), cx, cy - 6);
      ctx.fillStyle = TICK;
      ctx.font = '11px ' + SYS_FONT;
      ctx.fillText('총 요청', cx, cy + 14);
      ctx.restore();
    }
  };
}

/* Chart.js 공통 다크 스케일 */
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

/* 호스트/DB 시계열 색 팔레트(인스턴스 멀티라인용) */
var TS_COLORS = ['#4b91f7', '#3dd68c', '#ffb020', '#ff5c5c', '#8b5cf6', '#00b8d9', '#f06595'];

/* 공통 시계열 라인 차트 렌더.
 *  key       : charts[] 추적 키 / canvasId : <canvas> id
 *  series    : [{name, points:[{t,v}], color?}]  (멀티라인 가능)
 *  opts      : { title, unit('pct'|'ms'|'cnt'|'mb'|'gb'|''), fill(bool), legend(bool), yBeginZero(bool) }
 * 모든 시리즈의 t 합집합을 category 축으로, KST title 툴팁 + 단위 라벨. */
function renderTsLine(key, canvasId, series, opts) {
  opts = opts || {};
  if (!series || !series.length) {
    if (charts[key]) { charts[key].destroy(); charts[key] = null; }
    return;
  }
  /* t 합집합(오름차순) */
  var tset = {};
  series.forEach(function (s) { (s.points || []).forEach(function (pt) { tset[pt.t] = true; }); });
  var tsAll = Object.keys(tset).map(Number).sort(function (a, b) { return a - b; });
  if (!tsAll.length) {
    if (charts[key]) { charts[key].destroy(); charts[key] = null; }
    return;
  }
  var labels = tsAll.map(function (t) { return tsLabel(t, 1); });   /* 시계열은 HH:MM 위주 */
  var few = tsAll.length <= 12;

  var datasets = series.map(function (s, i) {
    var color = s.color || TS_COLORS[i % TS_COLORS.length];
    var map = {};
    (s.points || []).forEach(function (pt) { map[pt.t] = pt.v; });
    return {
      label: s.name,
      data: tsAll.map(function (t) {
        var v = map[t];
        if (v === undefined || v === null) return null;
        return (opts.unit === 'mb') ? bytesToMb(v) : (opts.unit === 'gb' ? Number(v) / (1024 * 1024 * 1024) : v);
      }),
      borderColor: color, backgroundColor: opts.fill ? 'rgba(75,145,247,0.16)' : color,
      pointBackgroundColor: color,
      borderWidth: 1.5, tension: few ? 0.1 : 0.25, spanGaps: true, fill: !!opts.fill,
      pointRadius: 2, pointHoverRadius: 5, pointHitRadius: 8
    };
  });

  function unitLabel(item) {
    var v = item.parsed.y;
    if (v === null || v === undefined) return null;
    var head = (item.dataset.label || '') + ': ';
    if (opts.unit === 'pct') return head + fmtFixed(v, 1) + '%';
    if (opts.unit === 'ms') return head + fmtNum(Math.round(v)) + 'ms';
    if (opts.unit === 'mb') return head + fmtFixed(v, 1) + 'MB';
    if (opts.unit === 'gb') return head + fmtFixed(v, 2) + 'GB';
    if (opts.unit === 'cnt') return head + fmtNum(Math.round(v));
    return head + fmtFixed(v, 2);
  }

  renderChart(key, canvasId, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      animation: false,
      plugins: {
        legend: opts.legend ? legendTop() : { display: false },
        title: opts.title ? { display: true, text: opts.title, color: TICK, font: { size: 12 } } : { display: false },
        tooltip: { callbacks: { title: tsTitleCb(tsAll), label: unitLabel } }
      },
      scales: baseScales(opts.yBeginZero === false ? { y: { grid: { color: GRID }, ticks: { color: TICK } } } : null)
    }
  });
}

/* fetch JSON 헬퍼(HTTP 에러를 throw) */
function fetchJson(url) {
  return fetch(url).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  });
}

/* ── 용어 도움말 ⓘ (hover title) ──────────────────────── */
/* inline SVG ⓘ + title 속성. text 는 고정 설명이라 안전 */
function helpIcon(text) {
  var span = document.createElement('span');
  span.className = 'help-icon';
  span.title = text;                 /* hover 설명 */
  span.setAttribute('aria-label', text);
  span.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">' +
    '<path fill="currentColor" d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 15a1 1 0 110 2 1 1 0 010-2zm1.3-4.6c-.5.4-.8.7-.8 1.3v.3h-1.9v-.4c0-1.1.5-1.8 1.3-2.4.6-.4.9-.7.9-1.2 0-.6-.5-1-1.1-1-.7 0-1.1.4-1.3 1l-1.7-.7C9.4 8 10.5 7.2 12 7.2c1.7 0 3 1 3 2.6 0 1.1-.6 1.8-1.7 2.6z"/></svg>';
  return span;
}
/* 라벨 + 도움말 묶음(요소 반환) */
function labelWithHelp(labelText, helpText) {
  var wrap = document.createElement('span');
  wrap.className = 'label-help';
  var t = document.createElement('span');
  t.textContent = labelText;
  wrap.appendChild(t);
  if (helpText) wrap.appendChild(helpIcon(helpText));
  return wrap;
}

/* 지표 용어 설명(핵심만) */
var TERM_HELP = {
  avg: '평균 응답시간',
  p95: '95퍼센타일 — 가장 느린 5% 요청 기준(체감 지연)',
  health: '헬스체크 엔드포인트',
  home: '홈페이지',
  dbload: 'DBLoad — 활성 세션 수(DB 부하)',
  credit: 'CPU 크레딧 — 버스트 가능 잔량(t계열, 소진 시 성능 저하)',
  iops: 'IOPS — 초당 디스크 입출력',
  conn: '동시 DB 연결 수',
  mem: '여유 메모리(FreeableMemory)'
};

/* 알람명 키워드 → 쉬운 한국어 설명(친근) */
function alarmFriendly(name) {
  var n = (name || '').toLowerCase();
  if (n.indexOf('low-storage') >= 0 || n.indexOf('free-storage') >= 0 || n.indexOf('storage') >= 0)
    return '💾 DB 디스크 여유공간 부족 감시';
  if (n.indexOf('high-cpu') >= 0 || (n.indexOf('cpu') >= 0 && n.indexOf('rds') >= 0) || n.indexOf('cpu') >= 0)
    return '🔥 CPU 과부하 감시';
  if (n.indexOf('health') >= 0 && (n.indexOf('severe') >= 0 || n.indexOf('critical') >= 0))
    return '🩺 서버 환경 헬스 심각 감시';
  if (n.indexOf('health') >= 0) return '🩺 서버 헬스체크 감시';
  if (n.indexOf('5xx') >= 0 && n.indexOf('cloudfront') >= 0) return '🌐 CDN 5xx 서버에러율 감시';
  if (n.indexOf('5xx') >= 0) return '🌐 5xx 서버에러율 감시';
  if (n.indexOf('4xx') >= 0) return '⚠️ 4xx 클라이언트 에러율 감시';
  if (n.indexOf('latency') >= 0 || n.indexOf('response') >= 0) return '⏱️ 응답 지연 감시';
  if (n.indexOf('connection') >= 0 || n.indexOf('conn') >= 0) return '🔌 DB 연결 수 감시';
  return null;   /* 매핑 없으면 친근 설명 생략 */
}

/* ── KPI 요약 스트립 갱신(기존 패널 API 응답 재사용) ───── */
/* 한 타일의 큰 숫자/상태색을 갱신. cls: 'good'|'bad'|'muted'|'' */
function setKpiTile(id, value, cls) {
  var tile = document.getElementById(id);
  if (!tile) return;
  tile.classList.remove('good', 'bad', 'muted');
  if (cls) tile.classList.add(cls);
  tile.querySelector('.kpi-tile-val').textContent = value;  /* 숫자는 textContent */
}

/* KPI 타일·미니카드 클릭 → 해당 라우트로 이동(hash 변경) */
function bindRouteLinks() {
  document.querySelectorAll('[data-route-to]').forEach(function (el) {
    el.addEventListener('click', function () {
      location.hash = '#/' + el.getAttribute('data-route-to');
    });
  });
}

/* ── 헤더 상태배지(알람 summary 기반) ──────────────────── */
/* state: 'ok' (경보 0) | 'bad' (경보 N) | '' (미상). 텍스트는 textContent */
function setStatusPill(state, text) {
  var pill = document.getElementById('status-pill');
  var txt = document.getElementById('status-text');
  if (!pill || !txt) return;
  pill.classList.remove('ok', 'bad');
  if (state) pill.classList.add(state);
  txt.textContent = text;
}

/* ── dashboard 요약 미니카드 렌더 헬퍼(차트 없음, 텍스트만) ──
 * 한 미니카드 본문(#sum-*-body)을 비우고 "행(라벨/값)" 또는 메시지로 채움.
 * 모든 데이터 값은 textContent 로만 주입(XSS 방지). */
function sumSet(bodyId, rows, msgCls, msg) {
  var body = document.getElementById(bodyId);
  if (!body) return;
  body.innerHTML = '';
  if (msg !== undefined) {
    var m = document.createElement('span');
    m.className = 'mini-muted' + (msgCls ? ' ' + msgCls : '');
    m.textContent = msg;
    body.appendChild(m);
    return;
  }
  rows.forEach(function (r) {
    var row = document.createElement('div');
    row.className = 'mini-row';
    var lab = document.createElement('span');
    lab.className = 'mini-label';
    lab.textContent = r[0];
    var val = document.createElement('span');
    val.className = 'mini-val' + (r[2] ? ' ' + r[2] : '');
    val.textContent = r[1];
    row.appendChild(lab); row.appendChild(val);
    body.appendChild(row);
  });
}

/* ── 인사이트(룰 findings) 공통 렌더 ───────────────────── */
var SEV_LABEL = { critical: '위험', warning: '주의', info: '정보' };

/* id 요소의 textContent 설정(없으면 무시) */
function setText(id, v) {
  var el = document.getElementById(id);
  if (el) el.textContent = v;
}

/* finding → <li> 카드(외부유래 title/evidence 는 textContent 로만 주입) */
function makeInsightCard(f) {
  var li = document.createElement('li');
  li.className = 'insight-card ' + (f.severity || 'info');
  var top = document.createElement('div');
  top.className = 'insight-card-top';
  var sev = document.createElement('span');
  sev.className = 'insight-sev';
  sev.textContent = SEV_LABEL[f.severity] || f.severity || '';
  var area = document.createElement('span');
  area.className = 'insight-area';
  area.textContent = f.area || '';
  var title = document.createElement('span');
  title.className = 'insight-title';
  title.textContent = f.title || '';        /* XSS: 외부유래(알람명 등) → textContent */
  top.appendChild(sev); top.appendChild(area); top.appendChild(title);
  li.appendChild(top);
  if (f.evidence) {
    var ev = document.createElement('div');
    ev.className = 'insight-evidence';
    ev.textContent = f.evidence;            /* 근거 수치 → textContent */
    li.appendChild(ev);
  }
  return li;
}

/* 인사이트 뷰: /api/insights → 종합 카운트 + AI 코멘트 + 신호 리스트 */
function loadInsights() {
  var card = document.getElementById('panel-insights');
  if (!card) return;
  var body = card.querySelector('.card-body');
  fetchJson('/api/insights').then(function (d) {
    var findings = d.findings || [];
    var s = d.summary || {};
    setText('ins-cnt-crit', s.critical || 0);
    setText('ins-cnt-warn', s.warning || 0);
    setText('ins-cnt-info', s.info || 0);
    var sumEl = document.getElementById('ins-summary');
    var txt = document.getElementById('ins-summary-text');
    if (sumEl) sumEl.classList.remove('warn', 'bad');
    if (s.critical > 0) { if (sumEl) sumEl.classList.add('bad'); if (txt) txt.textContent = '위험 신호 ' + s.critical + '건 — 즉시 확인이 필요합니다'; }
    else if (s.warning > 0) { if (sumEl) sumEl.classList.add('warn'); if (txt) txt.textContent = '주의 신호 ' + s.warning + '건 — 점검을 권장합니다'; }
    else if (txt) { txt.textContent = '모든 지표 정상 — 주목할 신호가 없습니다'; }
    var meta = document.getElementById('ins-meta');
    if (meta) meta.textContent = '신호 ' + (s.total || 0) + '건';
    /* AI 종합 코멘트(있을 때만) */
    var ai = document.getElementById('ins-ai');
    var aiBody = document.getElementById('ins-ai-body');
    if (d.ai_comment) { if (ai) ai.hidden = false; if (aiBody) aiBody.textContent = d.ai_comment; }  /* XSS: AI → textContent */
    else if (ai) { ai.hidden = true; }
    /* 신호 리스트 / 없음 */
    var list = document.getElementById('ins-list');
    var none = document.getElementById('ins-none');
    if (list) list.innerHTML = '';
    if (findings.length === 0) {
      if (none) none.hidden = false;
      if (list) list.hidden = true;
    } else {
      if (none) none.hidden = true;
      if (list) { list.hidden = false; findings.forEach(function (f) { list.appendChild(makeInsightCard(f)); }); }
    }
    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* 개요 상단: 종합 상태 + 주목 필요(critical/warning) 카드 */
function loadDashboardInsight() {
  fetchJson('/api/insights').then(function (d) {
    var findings = d.findings || [];
    var s = d.summary || {};
    setText('dash-cnt-crit', s.critical || 0);
    setText('dash-cnt-warn', s.warning || 0);
    var sumEl = document.getElementById('dash-insight-summary');
    var txt = document.getElementById('dash-insight-text');
    if (sumEl) sumEl.classList.remove('warn', 'bad');
    if (s.critical > 0) { if (sumEl) sumEl.classList.add('bad'); if (txt) txt.textContent = '위험 ' + s.critical + '건 — 즉시 확인이 필요합니다'; }
    else if (s.warning > 0) { if (sumEl) sumEl.classList.add('warn'); if (txt) txt.textContent = '주의 ' + s.warning + '건 — 점검을 권장합니다'; }
    else if (txt) { txt.textContent = '정상 — 주목할 이상 신호가 없습니다'; }
    /* 주목 필요(critical/warning만) */
    var wrap = document.getElementById('dash-attention-wrap');
    var alist = document.getElementById('dash-attention-list');
    if (alist) alist.innerHTML = '';
    var attn = findings.filter(function (f) { return f.severity === 'critical' || f.severity === 'warning'; });
    if (!attn.length) { if (wrap) wrap.hidden = true; }
    else {
      if (wrap) wrap.hidden = false;
      attn.forEach(function (f) { if (alist) alist.appendChild(makeInsightCard(f)); });
    }
  }).catch(function () {
    setText('dash-insight-text', '인사이트 불러오기 실패');
  });
}

/* 개요 로드: 종합 인사이트 + 기존 요약(미니카드/KPI — 정상 접기 안) */
function loadDashboard() {
  loadDashboardInsight();
  loadDashboardSummary();
}

/* dashboard 요약: 각 API 를 가볍게 받아 미니카드 + KPI + 상태배지 갱신.
 * (차트 미생성 — 요약 화면은 텍스트만) */
function loadDashboardSummary() {
  /* 알람 요약(+ 상태배지·KPI 항상 갱신) */
  fetchJson('/api/alarms').then(function (d) {
    if (d.empty) {
      setStatusPill('', '—'); setKpiTile('kpi-alarms', '—', 'muted');
      sumSet('sum-alarms-body', null, '', '데이터 없음'); return;
    }
    var s = d.summary || { total: 0, alarm: 0 };
    if (s.alarm > 0) { setStatusPill('bad', s.alarm + ' Alarm'); setKpiTile('kpi-alarms', '경보 ' + s.alarm, 'bad'); }
    else { setStatusPill('ok', 'Operational'); setKpiTile('kpi-alarms', '정상', 'good'); }
    sumSet('sum-alarms-body', [
      ['총 알람', fmtNum(s.total), ''],
      ['경보', fmtNum(s.alarm), s.alarm > 0 ? 'bad' : 'good']
    ]);
  }).catch(function () {
    setStatusPill('', '연결 오류'); setKpiTile('kpi-alarms', '—', 'muted');
    sumSet('sum-alarms-body', null, 'bad', '불러오기 실패');
  });

  /* 가동률 요약(24h, period 7 고정 조회) */
  fetchJson('/api/uptime?period=7').then(function (d) {
    var s24 = (d && d.summary24h) || {};
    var eps = Object.keys(s24);
    if (d.empty || eps.length === 0) {
      setKpiTile('kpi-uptime', '—', 'muted'); sumSet('sum-uptime-body', null, '', '데이터 없음'); return;
    }
    var primary = (s24.health !== undefined) ? 'health' : eps[0];
    var hp = (s24[primary] && s24[primary].pct != null) ? s24[primary].pct : null;
    setKpiTile('kpi-uptime', hp == null ? '—' : fmtFixed(hp, 1) + '%', hp == null ? 'muted' : 'good');
    var rows = eps.slice(0, 3).map(function (ep) {
      var p = (s24[ep] && s24[ep].pct != null) ? s24[ep].pct : null;
      var cls = p == null ? '' : (p >= 99.5 ? 'good' : (p >= 95 ? 'warn' : 'bad'));
      return [ep, p == null ? '—' : fmtFixed(p, 2) + '%', cls];
    });
    sumSet('sum-uptime-body', rows);
  }).catch(function () {
    setKpiTile('kpi-uptime', '—', 'muted'); sumSet('sum-uptime-body', null, 'bad', '불러오기 실패');
  });

  /* 트래픽 요약(7d) */
  fetchJson('/api/traffic?period=7').then(function (d) {
    if (d.empty || !d.total) {
      setKpiTile('kpi-users', '—', 'muted'); sumSet('sum-traffic-body', null, '', '데이터 없음'); return;
    }
    setKpiTile('kpi-users', fmtNum(d.n_users), '');
    var top1 = (d.top_ep && d.top_ep.length) ? d.top_ep[0].key : '—';
    sumSet('sum-traffic-body', [
      ['총 요청', fmtNum(d.total), ''],
      ['사용자(IP)', fmtNum(d.n_users), ''],
      ['Top API', top1, '']   /* top1 은 외부유래 → textContent(sumSet 내부) */
    ]);
  }).catch(function () {
    setKpiTile('kpi-users', '—', 'muted'); sumSet('sum-traffic-body', null, 'bad', '불러오기 실패');
  });

  /* 품질 요약(7d) */
  fetchJson('/api/quality?period=7').then(function (d) {
    if (d.empty) { setKpiTile('kpi-5xx', '—', 'muted'); sumSet('sum-quality-body', null, '', '데이터 없음'); return; }
    var b = d.buckets || {};
    var n5 = b['5xx'] || 0;
    setKpiTile('kpi-5xx', fmtNum(n5), n5 > 0 ? 'bad' : 'muted');
    sumSet('sum-quality-body', [
      ['2xx', fmtNum(b['2xx'] || 0), 'good'],
      ['4xx', fmtNum(b['4xx'] || 0), (b['4xx'] || 0) > 0 ? 'warn' : ''],
      ['5xx', fmtNum(n5), n5 > 0 ? 'bad' : '']
    ]);
  }).catch(function () {
    setKpiTile('kpi-5xx', '—', 'muted'); sumSet('sum-quality-body', null, 'bad', '불러오기 실패');
  });

  /* DB 요약(인스턴스 배열) */
  fetchJson('/api/db').then(function (d) {
    var insts = d.instances;
    if ((!insts || !insts.length) && !d.empty && d.cpu_avg !== undefined) insts = [d];   /* 구 스키마 폴백 */
    if (d.empty || !insts || !insts.length) { setKpiTile('kpi-dbcpu', '—', 'muted'); sumSet('sum-db-body', null, '', '데이터 없음'); return; }
    /* primary(없으면 첫) + 최대 CPU */
    var primaryDbId = d.primary_db_id;
    var primary = insts[0], peakCpu = null;
    insts.forEach(function (it) {
      if (it.db_id === primaryDbId) primary = it;
      if (it.cpu_max != null && (peakCpu === null || it.cpu_max > peakCpu)) peakCpu = it.cpu_max;
    });
    var cpu = primary.cpu_avg;
    setKpiTile('kpi-dbcpu', cpu == null ? '—' : fmtFixed(cpu, 1) + '%', (cpu != null && cpu >= 80) ? 'bad' : '');
    sumSet('sum-db-body', [
      ['RDS 인스턴스', fmtNum(insts.length), ''],
      ['주 DB CPU', cpu == null ? '—' : fmtFixed(cpu, 1) + '%', (cpu != null && cpu >= 80) ? 'bad' : (cpu != null && cpu >= 60 ? 'warn' : '')],
      ['최대 CPU', peakCpu == null ? '—' : fmtFixed(peakCpu, 1) + '%', (peakCpu != null && peakCpu >= 80) ? 'bad' : '']
    ]);
  }).catch(function () {
    setKpiTile('kpi-dbcpu', '—', 'muted'); sumSet('sum-db-body', null, 'bad', '불러오기 실패');
  });

  /* 호스트(EC2) 요약 */
  fetchJson('/api/host').then(function (d) {
    if (d.empty || !d.instances || !d.instances.length) {
      sumSet('sum-host-body', null, '', '데이터 없음'); return;
    }
    var insts = d.instances;
    /* 평균 CPU + 최대 CPU 인스턴스 산출 */
    var sum = 0, cnt = 0, peak = null;
    insts.forEach(function (it) {
      if (it.cpu_avg != null) { sum += it.cpu_avg; cnt++; }
      if (it.cpu_max != null && (peak === null || it.cpu_max > peak.cpu_max)) peak = it;
    });
    var avg = cnt ? (sum / cnt) : null;
    sumSet('sum-host-body', [
      ['인스턴스', fmtNum(insts.length), ''],
      ['평균 CPU', avg == null ? '—' : fmtFixed(avg, 1) + '%', (avg != null && avg >= 80) ? 'bad' : (avg != null && avg >= 60 ? 'warn' : '')],
      ['최대 CPU', peak == null ? '—' : fmtFixed(peak.cpu_max, 1) + '%', (peak && peak.cpu_max >= 80) ? 'bad' : '']
    ]);
  }).catch(function () {
    sumSet('sum-host-body', null, 'bad', '불러오기 실패');
  });

  /* CDN(CloudFront) 요약 */
  fetchJson('/api/cdn').then(function (d) {
    if (d.empty || !d.distributions || !d.distributions.length) {
      sumSet('sum-cdn-body', null, '', '데이터 없음'); return;
    }
    var dists = d.distributions;
    var totReq = 0, maxErr = null;
    dists.forEach(function (it) {
      if (it.requests != null) totReq += it.requests;
      if (it.err_total != null && (maxErr === null || it.err_total > maxErr)) maxErr = it.err_total;
    });
    sumSet('sum-cdn-body', [
      ['배포', fmtNum(dists.length), ''],
      ['총 요청', fmtNum(totReq), ''],
      ['최대 에러율', maxErr == null ? '—' : fmtFixed(maxErr, 2) + '%', (maxErr != null && maxErr > 5) ? 'bad' : '']
    ]);
  }).catch(function () {
    sumSet('sum-cdn-body', null, 'bad', '불러오기 실패');
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
/* 알람 상태 → {배지·pill 클래스, 한글 상태 텍스트} */
var ALARM_STATE = {
  OK: { cls: 'ok', text: '정상' },
  ALARM: { cls: 'alarm', text: '경보' },
  INSUFFICIENT_DATA: { cls: 'insufficient', text: '데이터부족' }
};
function alarmStateInfo(state) {
  return ALARM_STATE[state] || { cls: 'insufficient', text: '데이터부족' };
}

function loadAlarms() {
  var card = document.getElementById('panel-alarms');
  var body = card.querySelector('.card-body');
  fetchJson('/api/alarms').then(function (d) {
    if (d.empty) {
      setState(body, 'empty');
      document.getElementById('alarms-summary').textContent = '';
      setKpiTile('kpi-alarms', '—', 'muted');
      setStatusPill('', '—');
      return;
    }
    var sum = d.summary || { total: 0, alarm: 0 };
    document.getElementById('alarms-summary').textContent =
      '총 ' + sum.total + ' · 경보 ' + sum.alarm;

    /* KPI 타일 + 헤더 상태배지: 경보 0 → 초록, 1+ → 빨강 */
    if (sum.alarm > 0) {
      setKpiTile('kpi-alarms', '경보 ' + sum.alarm, 'bad');
      setStatusPill('bad', sum.alarm + ' Alarm');
    } else {
      setKpiTile('kpi-alarms', '정상', 'good');
      setStatusPill('ok', 'Operational');
    }

    /* ALARM 0건이면 "모든 알람 정상" 배너 노출 */
    var allClear = document.getElementById('alarm-allclear');
    if (allClear) allClear.hidden = (sum.alarm > 0);

    /* 정보 풍부한 알람 카드(전부 createElement + textContent, XSS 안전) */
    var ul = document.getElementById('alarm-list');
    ul.innerHTML = '';
    (d.items || []).forEach(function (it) {
      var info = alarmStateInfo(it.state);
      var li = document.createElement('li');
      li.className = 'alarm-item ' + info.cls;

      /* 1행: 색점 + 알람명(굵게) + 상태배지 + 전환시각 */
      var top = document.createElement('div');
      top.className = 'alarm-item-top';
      var badge = document.createElement('span');
      badge.className = 'badge ' + info.cls;
      var name = document.createElement('span');
      name.className = 'alarm-name';
      name.textContent = it.name;                  /* XSS: 외부유래 알람명 → textContent */
      var statePill = document.createElement('span');
      statePill.className = 'alarm-state ' + info.cls;
      statePill.textContent = info.text;           /* 고정 한글 텍스트 */
      var time = document.createElement('span');
      time.className = 'alarm-time';
      time.textContent = epochToKst(it.state_updated);
      top.appendChild(badge); top.appendChild(name); top.appendChild(statePill); top.appendChild(time);
      li.appendChild(top);

      /* 쉬운 한국어 설명(알람명 키워드 매핑, 굵게) */
      var friendly = alarmFriendly(it.name);
      if (friendly) {
        var fr = document.createElement('div');
        fr.className = 'alarm-friendly';
        fr.textContent = friendly;                  /* 고정 매핑 텍스트 */
        li.appendChild(fr);
      }

      /* 설명(description, 한 줄) */
      if (it.description) {
        var desc = document.createElement('div');
        desc.className = 'alarm-desc';
        desc.textContent = it.description;          /* XSS: textContent */
        li.appendChild(desc);
      }
      /* 조건(condition, 코드체 회색) */
      if (it.condition) {
        var cond = document.createElement('code');
        cond.className = 'alarm-cond';
        cond.textContent = it.condition;            /* XSS: textContent */
        li.appendChild(cond);
      }
      /* 현재 이유(reason) */
      if (it.reason) {
        var rs = document.createElement('div');
        rs.className = 'alarm-reason';
        var rsLab = document.createElement('span');
        rsLab.className = 'alarm-reason-label';
        rsLab.textContent = '현재 상태 ';
        var rsTxt = document.createElement('span');
        rsTxt.textContent = it.reason;              /* XSS: textContent */
        rs.appendChild(rsLab); rs.appendChild(rsTxt);
        li.appendChild(rs);
      }
      ul.appendChild(li);
    });
    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
    setKpiTile('kpi-alarms', '—', 'muted');
    setStatusPill('', '연결 오류');
  });
}

/* ── 패널 2: 가동률·응답시간 ───────────────────────────── */
var EP_COLOR = { health: C.blue, home: C.blue2 };
var FALLBACK_COLORS = [C.blue, C.blue2, C.gray, C.amber, C.ok];
function colorFor(ep, idx) { return EP_COLOR[ep] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length]; }

function loadUptime() {
  var card = document.getElementById('panel-uptime');
  var body = card.querySelector('.card-body');
  fetchJson('/api/uptime?period=' + uptimePeriod).then(function (d) {
    if (d.empty) { setState(body, 'empty'); setKpiTile('kpi-uptime', '—', 'muted'); return; }
    var series = d.series || {};
    var eps = Object.keys(series);
    if (eps.length === 0) { setState(body, 'empty'); setKpiTile('kpi-uptime', '—', 'muted'); return; }

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

    /* 가동률 요약 카드(큰 숫자 + sparkline) — 평선이라 큰 차트 대신 축약 */
    var sumRow = document.getElementById('uptime-summary');
    sumRow.innerHTML = '';
    var s24 = d.summary24h || {};
    eps.forEach(function (ep) {
      var s = s24[ep] || {};
      var pct = (s.pct === null || s.pct === undefined) ? null : s.pct;
      var cls = pct === null ? 'muted' : (pct >= 99.5 ? 'good' : (pct >= 95 ? 'warn' : 'bad'));
      var cardEl = document.createElement('div');
      cardEl.className = 'uptime-card';
      var lab = document.createElement('div');
      lab.className = 'uptime-card-label';
      lab.appendChild(labelWithHelp(ep + ' · 24h 가동률', TERM_HELP[ep] || null));  /* ep 는 textContent(labelWithHelp 내부) */
      var val = document.createElement('div');
      val.className = 'uptime-card-val ' + cls;
      val.textContent = (pct === null ? '—' : fmtFixed(pct, 2) + '%');
      cardEl.appendChild(lab); cardEl.appendChild(val);
      /* sparkline canvas */
      var spWrap = document.createElement('div');
      spWrap.className = 'uptime-spark';
      var cv = document.createElement('canvas');
      cv.id = 'uptime-spark-' + ep;
      spWrap.appendChild(cv);
      cardEl.appendChild(spWrap);
      sumRow.appendChild(cardEl);
    });

    /* KPI 스트립: health(없으면 첫 endpoint) 24h 가동률 */
    var primaryEp = (s24.health !== undefined) ? 'health' : eps[0];
    var hpct = (s24[primaryEp] && s24[primaryEp].pct !== null && s24[primaryEp].pct !== undefined)
      ? s24[primaryEp].pct : null;
    setKpiTile('kpi-uptime', hpct === null ? '—' : fmtFixed(hpct, 1) + '%', hpct === null ? 'muted' : 'good');

    /* sparkline 렌더(작은 라인, 축/범례 없음, 점만 호버) */
    eps.forEach(function (ep, i) {
      var arr = mapBy(ep, 'pct');
      var minP = null;
      arr.forEach(function (v) { if (v !== null && (minP === null || v < minP)) minP = v; });
      var yLo = (minP === null) ? 99 : Math.max(0, Math.floor(minP) - 1);
      renderChart('uptimeSpark_' + ep, 'uptime-spark-' + ep, {
        type: 'line',
        data: { labels: labels, datasets: [{
          data: arr, borderColor: colorFor(ep, i), backgroundColor: 'rgba(75,145,247,0.16)',
          borderWidth: 1.5, tension: 0.3, spanGaps: true, fill: true,
          pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 6
        }] },
        options: {
          animation: false,
          plugins: {
            legend: { display: false }, title: { display: false },
            tooltip: { callbacks: { title: tsTitleCb(tsAll), label: function (it2) { return ' ' + fmtFixed(it2.parsed.y, 2) + '%'; } } }
          },
          scales: {
            x: { display: false },
            y: { display: false, suggestedMin: yLo, suggestedMax: 100 }
          }
        }
      });
    });

    /* 응답시간(avg, p95) — avg 실선+포인트, p95 점선+작은 포인트 (주 차트) */
    var msDatasets = [];
    eps.forEach(function (ep, i) {
      var c = colorFor(ep, i);
      msDatasets.push({
        label: ep + ' avg',
        data: mapBy(ep, 'avg'),
        borderColor: c, backgroundColor: c, pointBackgroundColor: c,
        borderWidth: 1.5, tension: 0.25, spanGaps: true,
        pointRadius: 2, pointHoverRadius: 5, pointHitRadius: 8
      });
      msDatasets.push({
        label: ep + ' p95',
        data: mapBy(ep, 'p95'),
        borderColor: c, backgroundColor: c, pointBackgroundColor: c,
        borderWidth: 1, borderDash: [4, 3], tension: 0.25, spanGaps: true,
        pointRadius: 1.5, pointHoverRadius: 5, pointHitRadius: 8
      });
    });
    renderChart('uptimeMs', 'uptime-ms-chart', {
      type: 'line',
      data: { labels: labels, datasets: msDatasets },
      options: {
        animation: false,
        plugins: {
          legend: legendTop(),
          title: { display: true, text: '응답시간 (ms)', color: TICK, font: { size: 12 } },
          tooltip: { callbacks: { title: tsTitleCb(tsAll), label: labelUnitCb('ms') } }
        },
        scales: baseScales()
      }
    });

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
    setKpiTile('kpi-uptime', '—', 'muted');
  });
}

/* ── 패널 3: 트래픽·사용자 ─────────────────────────────── */
function loadTraffic() {
  var card = document.getElementById('panel-traffic');
  var body = card.querySelector('.card-body');
  fetchJson('/api/traffic?period=' + trafficPeriod).then(function (d) {
    /* empty 또는 total===0 → 빈상태(빈 차트 렌더 금지) */
    if (d.empty || !d.total) {
      setState(body, 'empty');
      /* KPI 활성 사용자 타일은 7d 기준만 반영(다른 기간 선택 시 갱신 안 함) */
      if (trafficPeriod === 7) setKpiTile('kpi-users', '—', 'muted');
      return;
    }

    /* KPI 스트립: 활성 사용자(7d 기준일 때만 반영) */
    if (trafficPeriod === 7) {
      setKpiTile('kpi-users', fmtNum(d.n_users), '');
    }

    /* KPI 숫자 — "실사용자 요청"(헬스/핑 제외) 명확화 */
    var sumRow = document.getElementById('traffic-summary');
    sumRow.innerHTML = '';
    [
      ['실사용자 요청', fmtNum(d.total), ''],
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
    /* 헬스체크/핑 제외 설명(작은 회색) */
    if (d.health_hits !== undefined || d.total_all !== undefined) {
      var note = document.createElement('div');
      note.className = 'traffic-note';
      var hh = (d.health_hits == null) ? 0 : d.health_hits;
      var ta = (d.total_all == null) ? d.total : d.total_all;
      note.textContent = '헬스체크·가동률 핑 ' + fmtNum(hh) + '건 제외 (전체 ' + fmtNum(ta) + '건)';
      sumRow.appendChild(note);
    }

    /* Top API/화면 가로 막대 — 라벨(key)은 외부유래지만 Chart.js 가 canvas 에 텍스트로 그림(HTML 미주입) */
    var topEp = d.top_ep || [];
    if (topEp.length) {
      var topVals = topEp.map(function (x) { return x.hits; });
      var topMax = Math.max.apply(null, topVals);
      renderChart('trafficTopEp', 'traffic-topep-chart', {
        type: 'bar',
        data: {
          labels: topEp.map(function (x) { return x.key; }),
          datasets: [{ label: 'hits', data: topVals, backgroundColor: C.blue, borderWidth: 0, borderRadius: 4, barThickness: 'flex', maxBarThickness: 26 }]
        },
        options: {
          indexAxis: 'y',
          animation: false,
          plugins: {
            legend: { display: false },
            title: { display: true, text: 'Top API / 화면', color: TICK, font: { size: 12 } },
            tooltip: { callbacks: { label: function (it) { return ' ' + fmtNum(it.parsed.x) + '건'; } } }
          },
          scales: {
            /* 막대 끝 값 라벨 공간 확보(max 에 ~12% 여유) */
            x: { grid: { color: GRID }, ticks: { color: TICK }, beginAtZero: true, suggestedMax: Math.ceil(topMax * 1.12) || 1 },
            y: { grid: { display: false }, ticks: { color: TICK, font: { size: 11 } } }
          }
        },
        plugins: [barValuePlugin]
      });
    } else if (charts.trafficTopEp) { charts.trafficTopEp.destroy(); charts.trafficTopEp = null; }

    /* 시간대별 추세 면적 라인 — 청록 + 포인트 */
    var hourly = d.hourly || [];
    if (hourly.length) {
      var hourEpochs = hourly.map(function (h) { return h.t; });
      var fewPoints = hourly.length <= 12;   /* 데이터 적으면 곡률 낮춤 */
      renderChart('trafficHourly', 'traffic-hourly-chart', {
        type: 'line',
        data: {
          labels: hourly.map(function (h) { return tsLabel(h.t, trafficPeriod); }),
          datasets: [{
            label: '요청수',
            data: hourly.map(function (h) { return h.count; }),
            borderColor: C.blue, backgroundColor: 'rgba(75,145,247,0.16)',
            pointBackgroundColor: C.blue,
            borderWidth: 1.5, tension: fewPoints ? 0.1 : 0.25, fill: true,
            pointRadius: 2, pointHoverRadius: 5, pointHitRadius: 8
          }]
        },
        options: {
          animation: false,
          plugins: {
            legend: { display: false },
            title: { display: true, text: '시간대별 추세', color: TICK, font: { size: 12 } },
            tooltip: { callbacks: { title: tsTitleCb(hourEpochs), label: function (it) { return ' ' + fmtNum(it.parsed.y) + '건'; } } }
          },
          scales: baseScales()
        }
      });
    } else if (charts.trafficHourly) { charts.trafficHourly.destroy(); charts.trafficHourly = null; }

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
    if (trafficPeriod === 7) setKpiTile('kpi-users', '—', 'muted');
  });
}

/* ── 패널 4-a: 응답품질(상태코드/에러) ─────────────────── */
var BUCKET_ORDER = ['2xx', '3xx', '4xx', '5xx', '기타'];
var BUCKET_COLOR = { '2xx': C.ok, '3xx': C.blue, '4xx': C.amber, '5xx': C.alarm, '기타': C.gray };
/* 상태코드 → 에러 코드 색 클래스(4xx 주황 / 5xx 빨강) */
function errCodeCls(code) {
  var c = parseInt(code, 10);
  if (c >= 500) return 'c5xx';
  if (c >= 400) return 'c4xx';
  return '';
}

function loadQuality() {
  var card = document.getElementById('panel-quality');
  var sub = card.querySelector('.subsection'); /* 첫 subsection = 품질 */
  fetchJson('/api/quality?period=' + trafficPeriod).then(function (d) {
    if (d.empty) {
      setState(sub, 'empty');
      if (trafficPeriod === 7) setKpiTile('kpi-5xx', '—', 'muted');
      return;
    }

    /* 상태코드 도넛 — buckets 합계 0 이면 빈상태(빈 차트 금지) */
    var buckets = d.buckets || {};
    var labels = [], data = [], colors = [], bucketTotal = 0;
    BUCKET_ORDER.forEach(function (k) {
      var v = buckets[k] || 0;
      bucketTotal += v;
      if (v > 0) { labels.push(k); data.push(v); colors.push(BUCKET_COLOR[k]); }
    });

    /* KPI 스트립: 5xx 에러(7d 기준일 때만). 0이면 muted, 1+면 빨강 */
    if (trafficPeriod === 7) {
      var n5xx = buckets['5xx'] || 0;
      setKpiTile('kpi-5xx', fmtNum(n5xx), n5xx > 0 ? 'bad' : 'muted');
    }

    if (bucketTotal === 0) { setState(sub, 'empty'); return; }

    /* 상태코드 합계 배지(요약 한 줄) */
    var badges = document.getElementById('bucket-badges');
    if (badges) {
      badges.innerHTML = '';
      BUCKET_ORDER.forEach(function (k) {
        var v = buckets[k] || 0;
        if (v === 0 && (k === '3xx' || k === '기타')) return;   /* 0이고 부차적인 건 생략 */
        var b = document.createElement('span');
        b.className = 'bucket-badge bk-' + (k === '기타' ? 'etc' : k);
        var kEl = document.createElement('span'); kEl.className = 'bk-k'; kEl.textContent = k;
        var vEl = document.createElement('span'); vEl.className = 'bk-v'; vEl.textContent = fmtNum(v);
        b.appendChild(kEl); b.appendChild(vEl);
        badges.appendChild(b);
      });
    }

    /* 도넛(보조, details 안 — 작게) */
    var donutTotal = data.reduce(function (a, b) { return a + b; }, 0);
    if (data.length) {
      renderChart('qualityBuckets', 'quality-buckets-chart', {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: data, backgroundColor: colors, borderColor: '#1b1c22', borderWidth: 2 }] },
        options: {
          animation: false,
          cutout: '62%',
          interaction: { mode: 'nearest', intersect: true },
          plugins: {
            legend: { display: true, position: 'right', labels: { color: C.ink, boxWidth: 12, font: { size: 12 } } },
            title: { display: false },
            tooltip: { callbacks: { label: function (it) {
              var v = it.parsed;
              var pct = donutTotal ? (v / donutTotal * 100) : 0;
              return ' ' + it.label + ': ' + fmtNum(v) + '건 (' + fmtFixed(pct, 1) + '%)';
            } } }
          }
        },
        plugins: [donutCenterPlugin(donutTotal)]
      });
    } else if (charts.qualityBuckets) { charts.qualityBuckets.destroy(); charts.qualityBuckets = null; }

    /* 에러 목록(메인): "코드 · URL · 건수". key 예 "401 /api/charts" 파싱 */
    var heading = document.querySelector('.err-heading');
    var ul = document.getElementById('top-err-list');
    ul.innerHTML = '';
    var topErr = d.top_err || [];
    if (topErr.length === 0) {
      if (heading) heading.hidden = true;
      var none = document.createElement('li');
      none.className = 'err-none-big';
      none.textContent = '✓ 에러 없음';
      ul.appendChild(none);
    } else {
      if (heading) heading.hidden = false;
      topErr.forEach(function (e) {
        var li = document.createElement('li');
        li.className = 'err-item';
        /* key 에서 앞 토큰이 3자리 상태코드면 분리 */
        var key = e.key || '';
        var m = /^(\d{3})\s+(.*)$/.exec(key);
        var code = m ? m[1] : null;
        var url = m ? m[2] : key;
        var codeEl = document.createElement('span');
        codeEl.className = 'err-code ' + (code ? errCodeCls(code) : '');
        codeEl.textContent = code || '·';            /* 코드 textContent */
        var urlEl = document.createElement('span');
        urlEl.className = 'err-url';
        urlEl.textContent = url;                       /* XSS: 외부유래 URL → textContent */
        urlEl.title = key;
        var hitsEl = document.createElement('span');
        hitsEl.className = 'err-hits';
        hitsEl.textContent = fmtNum(e.hits) + '건';
        li.appendChild(codeEl); li.appendChild(urlEl); li.appendChild(hitsEl);
        ul.appendChild(li);
      });
    }

    /* 스캐너 탐침 별도 표시 */
    var scn = document.getElementById('scanner-note');
    if (scn) {
      var sc = (d.scanner_hits == null) ? 0 : d.scanner_hits;
      if (sc > 0) {
        scn.hidden = false;
        scn.textContent = '🤖 스캐너 탐침 ' + fmtNum(sc) + '건 (무시)';
      } else {
        scn.hidden = true;
      }
    }

    setState(sub, 'ok');
  }).catch(function (e) {
    setState(sub, 'error', e.message);
    if (trafficPeriod === 7) setKpiTile('kpi-5xx', '—', 'muted');
  });
}

/* ── 패널 4-b: DB(RDS) — /api/db (인스턴스 배열) ────────── */
/* db_id 축약(식별 가능하게 — 앞부분 유지) */
function shortDbId(id) {
  if (!id) return '—';
  return id.length > 22 ? id.slice(0, 20) + '…' : id;
}

function loadDb() {
  var sub = document.getElementById('subsection-db');
  fetchJson('/api/db').then(function (d) {
    /* 신/구 응답 호환: instances 배열 없으면 단일 객체를 배열로 래핑 */
    var insts = d.instances;
    if ((!insts || !insts.length) && !d.empty && d.cpu_avg !== undefined) {
      insts = [d];   /* 구 단일 스키마 폴백 */
    }
    if (d.empty || !insts || !insts.length) { setState(sub, 'empty'); setKpiTile('kpi-dbcpu', '—', 'muted'); return; }

    var primaryDbId = d.primary_db_id;
    /* primary 먼저 정렬 */
    insts = insts.slice().sort(function (a, b) {
      if (a.db_id === primaryDbId) return -1;
      if (b.db_id === primaryDbId) return 1;
      return 0;
    });

    /* KPI 스트립: primary(없으면 첫) CPU 평균 */
    var primary = insts[0];
    for (var i = 0; i < insts.length; i++) { if (insts[i].db_id === primaryDbId) { primary = insts[i]; break; } }
    var pcpu = primary.cpu_avg;
    setKpiTile('kpi-dbcpu', pcpu == null ? '—' : fmtFixed(pcpu, 1) + '%', (pcpu != null && pcpu >= 80) ? 'bad' : '');

    var summaryEl = document.getElementById('db-summary');
    if (summaryEl) summaryEl.textContent = 'RDS ' + insts.length + '개';

    /* 인스턴스별 카드(EC2 패턴, 클릭 시 상세 모달) */
    var grid = document.getElementById('db-instances');
    grid.innerHTML = '';
    insts.forEach(function (it) {
      var isPrimary = (it.db_id === primaryDbId);
      var cardEl = document.createElement('button');
      cardEl.type = 'button';
      cardEl.className = 'host-card' + (isPrimary ? ' primary' : '');
      cardEl.addEventListener('click', function () { openDbModal(it, isPrimary); });

      /* 헤더: db_id 제목 + primary ★ */
      var head = document.createElement('div');
      head.className = 'host-card-head';
      var titleWrap = document.createElement('div');
      titleWrap.className = 'host-title-wrap';
      var nameEl = document.createElement('span');
      nameEl.className = 'host-name';
      nameEl.textContent = shortDbId(it.db_id);     /* 외부유래 → textContent */
      nameEl.title = it.db_id || '';
      titleWrap.appendChild(nameEl);
      if (isPrimary) {
        var badge = document.createElement('span');
        badge.className = 'host-badge'; badge.textContent = '★'; badge.title = it.db_id + ' (primary)';
        titleWrap.appendChild(badge);
      }
      head.appendChild(titleWrap);
      cardEl.appendChild(head);

      /* CPU 평균/최대 막대 */
      var cpu = it.cpu_avg;
      var cpuCls = (cpu != null && cpu >= 80) ? 'bad' : (cpu != null && cpu >= 60 ? 'warn' : '');
      var cpuRow = document.createElement('div'); cpuRow.className = 'host-metric';
      var cpuTop = document.createElement('div'); cpuTop.className = 'host-metric-top';
      var cpuLab = document.createElement('span'); cpuLab.className = 'host-metric-label'; cpuLab.textContent = 'CPU 평균/최대';
      var cpuVal = document.createElement('span'); cpuVal.className = 'host-metric-val' + (cpuCls ? ' ' + cpuCls : '');
      cpuVal.textContent = (cpu == null ? '—' : fmtFixed(cpu, 1) + '%') + ' / ' + (it.cpu_max == null ? '—' : fmtFixed(it.cpu_max, 1) + '%');
      cpuTop.appendChild(cpuLab); cpuTop.appendChild(cpuVal); cpuRow.appendChild(cpuTop);
      var bar = document.createElement('div'); bar.className = 'host-bar';
      var fill = document.createElement('div'); fill.className = 'host-bar-fill' + (cpuCls ? ' ' + cpuCls : '');
      fill.style.width = Math.max(0, Math.min(100, cpu == null ? 0 : cpu)) + '%';
      bar.appendChild(fill); cpuRow.appendChild(bar); cardEl.appendChild(cpuRow);

      /* 요약 행: 연결 · 여유공간 · 메모리 */
      function metaRow(label, valText, cls) {
        var r = document.createElement('div'); r.className = 'host-row';
        var l = document.createElement('span'); l.className = 'host-row-label'; l.textContent = label;
        var v = document.createElement('span'); v.className = 'host-row-val' + (cls ? ' ' + cls : ''); v.textContent = valText;
        r.appendChild(l); r.appendChild(v); cardEl.appendChild(r);
      }
      metaRow('연결 (평균/최대)', fmtFixed(it.conn_avg, 0) + ' / ' + fmtFixed(it.conn_max, 0), '');
      var freeGb = (it.free_storage == null) ? null : it.free_storage / (1024 * 1024 * 1024);
      var freeCls = (freeGb != null && freeGb < 5) ? 'bad' : (freeGb != null && freeGb < 15 ? 'warn' : '');
      metaRow('여유공간', freeGb == null ? '—' : fmtFixed(freeGb, 1) + 'GB', freeCls);
      metaRow('여유 메모리', it.mem_free == null ? '—' : fmtBytes(it.mem_free, 1), '');
      grid.appendChild(cardEl);
    });

    setState(sub, 'ok');
  }).catch(function (e) {
    setState(sub, 'error', e.message);
    setKpiTile('kpi-dbcpu', '—', 'muted');
  });
}

/* ── RDS 인스턴스 상세 모달 ────────────────────────────── */
function openDbModal(it, isPrimary) {
  var overlay = document.getElementById('db-modal');
  if (!overlay) return;
  document.getElementById('db-modal-title').textContent = (isPrimary ? '★ ' : '') + (it.db_id || 'RDS 인스턴스');
  document.getElementById('db-modal-sub').textContent = it.db_id || '';

  var meta = document.getElementById('db-modal-meta');
  meta.innerHTML = '';
  var freeGb = (it.free_storage == null) ? null : it.free_storage / (1024 * 1024 * 1024);
  meta.appendChild(modalMetaRow('CPU 평균/최대',
    (it.cpu_avg == null ? '—' : fmtFixed(it.cpu_avg, 1) + '%') + ' / ' + (it.cpu_max == null ? '—' : fmtFixed(it.cpu_max, 1) + '%')));
  meta.appendChild(modalMetaRow('연결 평균/최대', fmtFixed(it.conn_avg, 0) + ' / ' + fmtFixed(it.conn_max, 0), TERM_HELP.conn));
  meta.appendChild(modalMetaRow('여유공간', freeGb == null ? '—' : fmtFixed(freeGb, 1) + 'GB'));
  meta.appendChild(modalMetaRow('여유 메모리', it.mem_free == null ? '—' : fmtBytes(it.mem_free, 1), TERM_HELP.mem));
  meta.appendChild(modalMetaRow('스왑', it.swap == null ? '—' : fmtBytes(it.swap, 1)));
  meta.appendChild(modalMetaRow('읽기/쓰기 지연',
    (it.read_lat == null ? '—' : fmtFixed(it.read_lat * 1000, 1) + 'ms') + ' / ' + (it.write_lat == null ? '—' : fmtFixed(it.write_lat * 1000, 1) + 'ms')));
  meta.appendChild(modalMetaRow('IOPS 읽기/쓰기', fmtFixed(it.read_iops, 0) + ' / ' + fmtFixed(it.write_iops, 0), TERM_HELP.iops));
  meta.appendChild(modalMetaRow('DBLoad 평균/최대', fmtFixed(it.dbload_avg, 2) + ' / ' + fmtFixed(it.dbload_max, 2), TERM_HELP.dbload));
  meta.appendChild(modalMetaRow('DBLoad CPU/비CPU', fmtFixed(it.dbload_cpu, 2) + ' / ' + fmtFixed(it.dbload_noncpu, 2)));
  meta.appendChild(modalMetaRow('디스크큐', fmtFixed(it.disk_q, 2)));
  if (it.max_txid !== undefined) meta.appendChild(modalMetaRow('최대 TXID', it.max_txid == null ? '—' : fmtNum(it.max_txid)));

  overlay.hidden = false;

  /* 시계열 4종(표시 후 렌더) */
  renderTsLine('dbDetailCpu', 'db-detail-cpu-chart',
    it.cpu_series ? [{ name: 'CPU', points: it.cpu_series, color: C.blue }] : null,
    { title: 'CPU %', unit: 'pct', fill: true });
  renderTsLine('dbDetailMem', 'db-detail-mem-chart',
    it.mem_series ? [{ name: '여유 메모리', points: it.mem_series, color: '#3dd68c' }] : null,
    { title: '여유 메모리 (MB)', unit: 'mb', fill: true });
  renderTsLine('dbDetailLoad', 'db-detail-load-chart',
    it.dbload_series ? [{ name: 'DBLoad', points: it.dbload_series, color: '#ffb020' }] : null,
    { title: 'DBLoad', unit: '', fill: true });
  renderTsLine('dbDetailConn', 'db-detail-conn-chart',
    it.conn_series ? [{ name: '연결 수', points: it.conn_series, color: '#8b5cf6' }] : null,
    { title: '연결 수', unit: 'cnt', fill: true });
}

function closeDbModal() {
  var overlay = document.getElementById('db-modal');
  if (!overlay) return;
  overlay.hidden = true;
  ['dbDetailCpu', 'dbDetailMem', 'dbDetailLoad', 'dbDetailConn'].forEach(function (k) {
    if (charts[k]) { charts[k].destroy(); charts[k] = null; }
  });
}

function bindDbModal() {
  var overlay = document.getElementById('db-modal');
  var closeBtn = document.getElementById('db-modal-close');
  if (closeBtn) closeBtn.addEventListener('click', closeDbModal);
  if (overlay) {
    overlay.addEventListener('click', function (ev) { if (ev.target === overlay) closeDbModal(); });
  }
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') closeDbModal(); });
}

/* ── 패널: 호스트(EC2) — /api/host ─────────────────────── */
/* instance_id 축약(끝 8자) — 표시용 */
function shortId(id) {
  if (!id) return '—';
  return id.length > 10 ? '…' + id.slice(-8) : id;
}

function loadHost() {
  var card = document.getElementById('panel-host');
  if (!card) return;
  var body = card.querySelector('.card-body');
  fetchJson('/api/host').then(function (d) {
    if (d.empty || !d.instances || !d.instances.length) { setState(body, 'empty'); return; }

    var primaryId = d.primary_instance_id;
    /* primary 먼저 정렬 */
    var insts = d.instances.slice().sort(function (a, b) {
      if (a.instance_id === primaryId) return -1;
      if (b.instance_id === primaryId) return 1;
      return 0;
    });

    document.getElementById('host-summary').textContent = '인스턴스 ' + insts.length + '개';

    /* 인스턴스별 카드 그리드(전부 createElement + textContent, XSS 안전) */
    var grid = document.getElementById('host-instances');
    grid.innerHTML = '';
    insts.forEach(function (it, idx) {
      var isPrimary = (it.instance_id === primaryId);
      var cardEl = document.createElement('button');   /* 클릭 가능 → 상세 모달 */
      cardEl.type = 'button';
      cardEl.className = 'host-card' + (isPrimary ? ' primary' : '');
      cardEl.addEventListener('click', function () { openHostModal(it, isPrimary); });

      /* 헤더: instance_name(역할) 제목 + primary 배지 + 상태점 */
      var head = document.createElement('div');
      head.className = 'host-card-head';
      var titleWrap = document.createElement('div');
      titleWrap.className = 'host-title-wrap';
      var nameEl = document.createElement('span');
      nameEl.className = 'host-name';
      nameEl.textContent = it.instance_name || shortId(it.instance_id);   /* 역할 이름 우선 */
      titleWrap.appendChild(nameEl);
      if (isPrimary) {
        var badge = document.createElement('span');
        badge.className = 'host-badge';
        badge.textContent = '★';
        badge.title = 'dataviz-prod (primary)';
        titleWrap.appendChild(badge);
      }
      head.appendChild(titleWrap);
      /* 상태체크 점 */
      var stChk = document.createElement('span');
      var sf = it.status_failed;
      stChk.className = 'host-status ' + (sf > 0 ? 'bad' : 'ok');
      stChk.textContent = sf > 0 ? '체크 실패 ' + sf : '정상';
      head.appendChild(stChk);
      cardEl.appendChild(head);

      /* 부제: private_ip · instance_type · id 끝자리 */
      var subParts = [];
      if (it.private_ip) subParts.push(it.private_ip);
      if (it.instance_type) subParts.push(it.instance_type);
      subParts.push(shortId(it.instance_id));
      var subEl = document.createElement('div');
      subEl.className = 'host-sub';
      subEl.textContent = subParts.join(' · ');     /* 외부유래 → textContent */
      cardEl.appendChild(subEl);

      /* CPU 평균/최대 + 미니 막대 */
      var cpuAvg = it.cpu_avg;
      var cpuCls = (cpuAvg != null && cpuAvg >= 80) ? 'bad' : (cpuAvg != null && cpuAvg >= 60 ? 'warn' : '');
      var cpuRow = document.createElement('div');
      cpuRow.className = 'host-metric';
      var cpuTop = document.createElement('div');
      cpuTop.className = 'host-metric-top';
      var cpuLab = document.createElement('span'); cpuLab.className = 'host-metric-label';
      cpuLab.textContent = 'CPU 평균/최대';
      var cpuVal = document.createElement('span'); cpuVal.className = 'host-metric-val' + (cpuCls ? ' ' + cpuCls : '');
      cpuVal.textContent = (cpuAvg == null ? '—' : fmtFixed(cpuAvg, 1) + '%') + ' / ' +
        (it.cpu_max == null ? '—' : fmtFixed(it.cpu_max, 1) + '%');
      cpuTop.appendChild(cpuLab); cpuTop.appendChild(cpuVal);
      cpuRow.appendChild(cpuTop);
      var bar = document.createElement('div'); bar.className = 'host-bar';
      var fill = document.createElement('div'); fill.className = 'host-bar-fill' + (cpuCls ? ' ' + cpuCls : '');
      fill.style.width = Math.max(0, Math.min(100, cpuAvg == null ? 0 : cpuAvg)) + '%';
      bar.appendChild(fill); cpuRow.appendChild(bar);
      cardEl.appendChild(cpuRow);

      /* 네트워크/EBS/크레딧 미니 행 */
      function metaRow(label, valText, cls) {
        var r = document.createElement('div'); r.className = 'host-row';
        var l = document.createElement('span'); l.className = 'host-row-label'; l.textContent = label;
        var v = document.createElement('span'); v.className = 'host-row-val' + (cls ? ' ' + cls : ''); v.textContent = valText;
        r.appendChild(l); r.appendChild(v); cardEl.appendChild(r);
      }
      metaRow('네트워크 In/Out', fmtBytes(it.net_in) + ' / ' + fmtBytes(it.net_out), '');
      metaRow('EBS 읽기/쓰기', fmtBytes(it.ebs_read) + ' / ' + fmtBytes(it.ebs_write), '');
      if (it.credit_min !== undefined && it.credit_min !== null) {
        var crCls = it.credit_min < 20 ? 'warn' : '';
        var crR = document.createElement('div'); crR.className = 'host-row';
        var crL = document.createElement('span'); crL.className = 'host-row-label';
        crL.appendChild(labelWithHelp('CPU 크레딧', TERM_HELP.credit));
        var crV = document.createElement('span'); crV.className = 'host-row-val' + (crCls ? ' ' + crCls : '');
        crV.textContent = fmtFixed(it.credit_min, 0);
        crR.appendChild(crL); crR.appendChild(crV); cardEl.appendChild(crR);
      }
      grid.appendChild(cardEl);
    });

    /* CPU 시계열 멀티라인(인스턴스별) */
    var series = insts
      .filter(function (it) { return it.cpu_series && it.cpu_series.length; })
      .map(function (it, i) {
        return { name: shortId(it.instance_id), points: it.cpu_series, color: TS_COLORS[i % TS_COLORS.length] };
      });
    renderTsLine('hostCpu', 'host-cpu-chart', series.length ? series : null,
      { title: '인스턴스별 CPU %', unit: 'pct', legend: true });

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* ── EC2 인스턴스 상세 모달 ────────────────────────────── */
/* 모달 메타 한 행(라벨/값) — 값은 textContent. helpText 있으면 라벨에 ⓘ */
function modalMetaRow(label, value, helpText) {
  var r = document.createElement('div');
  r.className = 'modal-meta-row';
  var l = document.createElement('span');
  l.className = 'modal-meta-label';
  if (helpText) { l.appendChild(labelWithHelp(label, helpText)); }
  else { l.textContent = label; }
  var v = document.createElement('span');
  v.className = 'modal-meta-val';
  v.textContent = (value === null || value === undefined || value === '') ? '—' : value;
  r.appendChild(l); r.appendChild(v);
  return r;
}

function openHostModal(it, isPrimary) {
  var overlay = document.getElementById('host-modal');
  if (!overlay) return;
  /* 제목: 역할 이름 (+ primary ★) / 부제: 전체 instance_id */
  var titleEl = document.getElementById('host-modal-title');
  titleEl.textContent = (isPrimary ? '★ ' : '') + (it.instance_name || it.instance_id || '인스턴스');
  document.getElementById('host-modal-sub').textContent = it.instance_id || '';

  /* 메타 그리드 */
  var meta = document.getElementById('host-modal-meta');
  meta.innerHTML = '';
  meta.appendChild(modalMetaRow('Private IP', it.private_ip));
  meta.appendChild(modalMetaRow('타입', it.instance_type));
  meta.appendChild(modalMetaRow('상태', it.state));
  meta.appendChild(modalMetaRow('CPU 평균/최대',
    (it.cpu_avg == null ? '—' : fmtFixed(it.cpu_avg, 1) + '%') + ' / ' +
    (it.cpu_max == null ? '—' : fmtFixed(it.cpu_max, 1) + '%')));
  meta.appendChild(modalMetaRow('네트워크 In/Out', fmtBytes(it.net_in) + ' / ' + fmtBytes(it.net_out)));
  if (it.credit_min !== undefined && it.credit_min !== null) {
    meta.appendChild(modalMetaRow('CPU 크레딧', fmtFixed(it.credit_min, 0)));
  }

  overlay.hidden = false;

  /* 시계열 차트(CPU + 네트워크 In/Out) — 모달 표시 후 렌더(가시 상태라 크기 정상) */
  renderTsLine('hostDetailCpu', 'host-detail-cpu-chart',
    it.cpu_series ? [{ name: 'CPU', points: it.cpu_series, color: C.blue }] : null,
    { title: 'CPU %', unit: 'pct', fill: true });

  var netSeries = [];
  if (it.net_in_series && it.net_in_series.length) netSeries.push({ name: 'In', points: it.net_in_series, color: C.blue });
  if (it.net_out_series && it.net_out_series.length) netSeries.push({ name: 'Out', points: it.net_out_series, color: '#3dd68c' });
  renderTsLine('hostDetailNet', 'host-detail-net-chart', netSeries.length ? netSeries : null,
    { title: '네트워크 In/Out (MB)', unit: 'mb', legend: netSeries.length > 1, fill: netSeries.length === 1 });
}

function closeHostModal() {
  var overlay = document.getElementById('host-modal');
  if (!overlay) return;
  overlay.hidden = true;
  if (charts.hostDetailCpu) { charts.hostDetailCpu.destroy(); charts.hostDetailCpu = null; }
  if (charts.hostDetailNet) { charts.hostDetailNet.destroy(); charts.hostDetailNet = null; }
}

function bindHostModal() {
  var overlay = document.getElementById('host-modal');
  var closeBtn = document.getElementById('host-modal-close');
  if (closeBtn) closeBtn.addEventListener('click', closeHostModal);
  if (overlay) {
    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay) closeHostModal();   /* 배경 클릭 닫기 */
    });
  }
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeHostModal();
  });
}

/* ── 패널: CDN(CloudFront) — /api/cdn ──────────────────── */
function loadCdn() {
  var card = document.getElementById('panel-cdn');
  if (!card) return;
  var body = card.querySelector('.card-body');
  fetchJson('/api/cdn').then(function (d) {
    if (d.empty || !d.distributions || !d.distributions.length) { setState(body, 'empty'); return; }
    var dists = d.distributions;
    document.getElementById('cdn-summary').textContent = '배포 ' + dists.length + '개';

    /* 배포별 카드(전부 createElement + textContent, XSS 안전) */
    var grid = document.getElementById('cdn-distributions');
    grid.innerHTML = '';
    dists.forEach(function (it) {
      var cardEl = document.createElement('div');
      cardEl.className = 'host-card';

      var head = document.createElement('div');
      head.className = 'host-card-head';
      var idEl = document.createElement('span');
      idEl.className = 'host-id';
      idEl.textContent = it.dist_id || '—';           /* 외부유래 → textContent */
      idEl.title = it.dist_id || '';
      head.appendChild(idEl);
      cardEl.appendChild(head);

      function metaRow(label, valText, cls) {
        var r = document.createElement('div'); r.className = 'host-row';
        var l = document.createElement('span'); l.className = 'host-row-label'; l.textContent = label;
        var v = document.createElement('span'); v.className = 'host-row-val' + (cls ? ' ' + cls : ''); v.textContent = valText;
        r.appendChild(l); r.appendChild(v); cardEl.appendChild(r);
      }
      metaRow('요청수', fmtNum(it.requests), '');
      metaRow('다운로드/업로드', fmtBytes(it.bytes_down) + ' / ' + fmtBytes(it.bytes_up), '');
      var e4 = it.err_4xx, e5 = it.err_5xx, et = it.err_total;
      metaRow('4xx 에러율', e4 == null ? '—' : fmtFixed(e4, 2) + '%', (e4 != null && e4 > 5) ? 'warn' : '');
      metaRow('5xx 에러율', e5 == null ? '—' : fmtFixed(e5, 2) + '%', (e5 != null && e5 > 1) ? 'bad' : '');
      metaRow('전체 에러율', et == null ? '—' : fmtFixed(et, 2) + '%', (et != null && et > 5) ? 'bad' : '');
      grid.appendChild(cardEl);
    });

    /* 요청수 시계열(배포별 멀티라인) */
    var reqSeries = dists
      .filter(function (it) { return it.requests_series && it.requests_series.length; })
      .map(function (it, i) { return { name: it.dist_id, points: it.requests_series, color: TS_COLORS[i % TS_COLORS.length] }; });
    renderTsLine('cdnReq', 'cdn-req-chart', reqSeries.length ? reqSeries : null,
      { title: '요청수 추세', unit: 'cnt', legend: reqSeries.length > 1, fill: reqSeries.length === 1 });

    /* 전체 에러율 시계열(배포별 멀티라인) */
    var errSeries = dists
      .filter(function (it) { return it.err_total_series && it.err_total_series.length; })
      .map(function (it, i) { return { name: it.dist_id, points: it.err_total_series, color: TS_COLORS[i % TS_COLORS.length] }; });
    renderTsLine('cdnErr', 'cdn-err-chart', errSeries.length ? errSeries : null,
      { title: '에러율 추세 (%)', unit: 'pct', legend: errSeries.length > 1 });

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
  });
}

/* ── 채팅 패널: POST /api/chat ─────────────────────────── */
var chatBusy = false;

/* 메시지 영역 맨 아래로 스크롤 */
function chatScrollToBottom() {
  var wrap = document.getElementById('chat-messages');
  wrap.scrollTop = wrap.scrollHeight;
}

/* 봇/에러 메시지 좌측 아바타(다크 톤 박스) */
function makeAvatar() {
  var av = document.createElement('span');
  av.className = 'chat-avatar';
  av.setAttribute('aria-hidden', 'true');
  av.textContent = '🤖';
  return av;
}

/* role → 라벨(YOU/ASSISTANT). 고정 텍스트라 안전 */
function roleLabel(role) {
  return role === 'user' ? 'YOU' : 'ASSISTANT';
}

/* 말풍선 추가(role: 'user'|'bot'|'error'). 본문은 textContent 로만 주입 */
function appendChatMsg(role, text) {
  var wrap = document.getElementById('chat-messages');
  var msg = document.createElement('div');
  msg.className = 'chat-msg ' + role;
  if (role !== 'user') msg.appendChild(makeAvatar());  /* 봇/에러: 좌측 아바타 */
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  var roleEl = document.createElement('span');
  roleEl.className = 'chat-role';
  roleEl.textContent = roleLabel(role);                /* 시안 라벨 YOU/ASSISTANT */
  var textEl = document.createElement('span');
  textEl.className = 'chat-text';
  textEl.textContent = text;                           /* XSS: 사용자 입력·봇 answer → textContent */
  bubble.appendChild(roleEl);
  bubble.appendChild(textEl);
  msg.appendChild(bubble);
  wrap.appendChild(msg);
  chatScrollToBottom();
  return msg;
}

/* typing indicator(점 3개 바운스) 말풍선 추가 — 고정 골격만 DOM 생성 */
function appendChatTyping() {
  var wrap = document.getElementById('chat-messages');
  var msg = document.createElement('div');
  msg.className = 'chat-msg bot';
  msg.appendChild(makeAvatar());
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  var roleEl = document.createElement('span');
  roleEl.className = 'chat-role';
  roleEl.textContent = 'ASSISTANT';
  var typing = document.createElement('span');
  typing.className = 'chat-typing';
  typing.appendChild(document.createElement('span'));
  typing.appendChild(document.createElement('span'));
  typing.appendChild(document.createElement('span'));
  bubble.appendChild(roleEl);
  bubble.appendChild(typing);
  msg.appendChild(bubble);
  wrap.appendChild(msg);
  chatScrollToBottom();
  return msg;
}

/* 채팅 전송 중 UI 토글(버튼 스피너 + 입력 비활성) */
function setChatBusy(busy) {
  chatBusy = busy;
  var btn = document.getElementById('chat-send');
  var input = document.getElementById('chat-input');
  btn.disabled = busy;
  input.disabled = busy;
  btn.classList.toggle('busy', busy);
}

function sendChat(question) {
  if (chatBusy) return;
  var input = document.getElementById('chat-input');
  var q = (question !== undefined ? question : (input.value || '')).trim();
  if (!q) return;                                  /* 빈 질문 전송 금지 */

  /* 첫 전송 시 예시 질문 칩 숨김 */
  var chips = document.getElementById('chat-chips');
  if (chips) chips.hidden = true;

  appendChatMsg('user', q);
  input.value = '';

  /* 전송 중: 버튼 스피너 + typing indicator */
  setChatBusy(true);
  var typing = appendChatTyping();

  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q })
  }).then(function (r) {
    return r.json().then(function (j) { return { ok: r.ok, body: j }; });
  }).then(function (res) {
    if (typing.parentNode) typing.parentNode.removeChild(typing);
    var b = res.body || {};
    if (b.error) {
      appendChatMsg('error', b.error);
    } else if (b.answer !== undefined && b.answer !== null) {
      appendChatMsg('bot', b.answer);              /* XSS: answer → textContent, white-space:pre-wrap */
    } else {
      appendChatMsg('error', '응답을 이해하지 못했습니다');
    }
  }).catch(function (e) {
    if (typing.parentNode) typing.parentNode.removeChild(typing);
    appendChatMsg('error', '요청 실패: ' + e.message);
  }).then(function () {
    setChatBusy(false);
    input.focus();
  });
}

function bindChat() {
  var form = document.getElementById('chat-form');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();                         /* Enter/전송 모두 폼 submit 으로 통합 */
      sendChat();
    });
  }
  /* 예시 질문 칩: 클릭 시 해당 질문 자동 전송(칩 텍스트는 고정이라 안전) */
  var chips = document.getElementById('chat-chips');
  if (chips) {
    chips.querySelectorAll('.chat-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        sendChat(chip.getAttribute('data-q'));
      });
    });
  }
}

/* ══ hash 라우터(클라이언트 멀티뷰) ══════════════════════
 * 라우트별: 활성 뷰만 표시 + 네비 active + 해당 뷰 데이터 로드(차트는 활성 시 생성).
 * 떠나는 뷰의 차트는 destroy 하여 숨김 컨테이너 0-크기 렌더 방지. */
var ROUTES = ['dashboard', 'insights', 'alarms', 'uptime', 'traffic', 'quality', 'host', 'cdn', 'database'];
var VIEW_META = {
  dashboard: { eyebrow: 'Overview', title: 'Dashboard' },
  insights:  { eyebrow: 'Overview', title: '운영 인사이트' },
  alarms:    { eyebrow: 'Monitoring', title: '알람' },
  uptime:    { eyebrow: 'Monitoring', title: '가동률·응답시간' },
  traffic:   { eyebrow: 'Traffic', title: '트래픽·사용자' },
  quality:   { eyebrow: 'Traffic', title: '응답품질' },
  host:      { eyebrow: 'Infra', title: 'EC2 인스턴스' },
  cdn:       { eyebrow: 'Infra', title: 'CloudFront CDN' },
  database:  { eyebrow: 'Database', title: 'DB 성능' }
};
/* 각 라우트가 보유한 차트 key(뷰 떠날 때 destroy 대상) */
var ROUTE_CHARTS = {
  uptime: ['uptimeMs'],
  traffic: ['trafficTopEp', 'trafficHourly'],
  quality: ['qualityBuckets'],
  host: ['hostCpu'],
  cdn: ['cdnReq', 'cdnErr'],
  database: []   /* DB 시계열은 인스턴스별 상세 모달에서 렌더 */
};
var currentRoute = null;

/* 현재 hash → 라우트명(유효하지 않으면 dashboard) */
function routeFromHash() {
  var h = (location.hash || '').replace(/^#\/?/, '');
  return ROUTES.indexOf(h) >= 0 ? h : 'dashboard';
}

/* 라우트의 차트 인스턴스 destroy(숨김 전 정리) */
function destroyRouteCharts(route) {
  (ROUTE_CHARTS[route] || []).forEach(function (k) {
    if (charts[k]) { charts[k].destroy(); charts[k] = null; }
  });
  /* uptime sparkline 은 endpoint 별 동적 키(uptimeSpark_*) — 접두사로 일괄 정리 */
  if (route === 'uptime') {
    Object.keys(charts).forEach(function (k) {
      if (k.indexOf('uptimeSpark_') === 0 && charts[k]) { charts[k].destroy(); charts[k] = null; }
    });
  }
}

/* 활성 뷰 데이터 로드(차트는 이 시점에 생성됨) */
function loadRoute(route) {
  if (route === 'dashboard') { loadDashboard(); return; }
  if (route === 'insights') { loadInsights(); return; }
  if (route === 'alarms') { loadAlarms(); return; }
  if (route === 'uptime') { loadUptime(); return; }
  if (route === 'traffic') { loadTraffic(); return; }
  if (route === 'quality') { loadQuality(); return; }
  if (route === 'host') { loadHost(); return; }
  if (route === 'cdn') { loadCdn(); return; }
  if (route === 'database') { loadDb(); return; }
}

/* 뷰 전환: 떠나는 뷰 차트 destroy → 활성 뷰만 표시 → 네비/헤더 동기화 → 로드 */
function applyRoute() {
  var route = routeFromHash();
  if (route === currentRoute) { loadRoute(route); return; }  /* 같은 해시 재진입 = 새로고침 로드 */

  if (currentRoute) destroyRouteCharts(currentRoute);
  closeHostModal();          /* 라우트 전환 시 열린 EC2 모달 닫기 */
  closeDbModal();            /* 라우트 전환 시 열린 RDS 모달 닫기 */

  /* 뷰 표시 토글 */
  document.querySelectorAll('.view[data-view]').forEach(function (v) {
    v.hidden = (v.getAttribute('data-view') !== route);
  });
  /* 네비 active */
  document.querySelectorAll('.nav-item[data-route]').forEach(function (it) {
    it.classList.toggle('active', it.getAttribute('data-route') === route);
  });
  /* 헤더 제목 */
  var meta = VIEW_META[route] || VIEW_META.dashboard;
  var eb = document.getElementById('view-eyebrow');
  var ti = document.getElementById('view-title');
  if (eb) eb.textContent = meta.eyebrow;
  if (ti) ti.textContent = meta.title;

  currentRoute = route;
  /* 항상 알람을 가볍게 받아 상태배지 갱신(알람 뷰가 아니어도) */
  if (route !== 'alarms' && route !== 'dashboard') loadAlarms();
  loadRoute(route);            /* 활성 뷰 데이터(차트는 여기서 생성) */
}

/* 잘못된/빈 해시는 dashboard 로 정규화 */
function bindRouter() {
  window.addEventListener('hashchange', applyRoute);
  if (ROUTES.indexOf((location.hash || '').replace(/^#\/?/, '')) < 0) {
    location.replace('#/dashboard');   /* 히스토리 오염 없이 기본 라우트 */
  }
  applyRoute();
}

/* ── refresh 버튼: 현재 뷰 + 공통(메타·상태배지) 재조회 ─── */
function bindRefresh() {
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    btn.classList.add('spinning');
    loadMeta();
    loadAlarms();              /* 상태배지 항상 갱신 */
    loadRoute(currentRoute || routeFromHash());   /* 활성 뷰만 재조회 */
    setTimeout(function () { btn.classList.remove('spinning'); }, 800);
  });
}

/* ── 트래픽/품질 공유 재조회(활성 뷰만 차트 렌더) ──────────
 * traffic·quality 는 trafficPeriod 를 공유하지만, 숨김 뷰에 차트를
 * 생성하면 0-크기로 깨지므로 현재 활성 라우트만 로드한다.
 * (다른 뷰는 진입 시 공유된 trafficPeriod 로 로드되어 일관성 유지) */
function reloadTrafficGroup() {
  if (currentRoute === 'traffic') loadTraffic();
  else if (currentRoute === 'quality') loadQuality();
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
  bindRouteLinks();          /* KPI 타일·미니카드 → 라우트 이동 */
  bindRefresh();
  bindChat();
  bindHostModal();           /* EC2 상세 모달 닫기/배경/ESC */
  bindDbModal();             /* RDS 상세 모달 닫기/배경/ESC */
  bindRouter();              /* 라우터 시작(초기 hash → 활성 뷰 로드) */

  /* 공통 메타(마지막 갱신·stale 배너) 최초 1회 */
  loadMeta();

  /* ── 폴링: 공통(메타·상태배지) + 활성 뷰만 갱신 ──
   * 숨김 뷰는 갱신 생략(차트 0-크기 렌더 방지 + 불필요 fetch 절감). */
  setInterval(loadMeta, POLL.meta);
  setInterval(function () {
    loadAlarms();            /* 상태배지·알람 KPI 는 항상 가볍게 갱신 */
  }, POLL.alarms);
  setInterval(function () {
    var r = currentRoute || routeFromHash();
    /* alarms 는 위 loadAlarms 가 이미 갱신하므로 중복 제외 */
    if (r === 'dashboard') loadDashboard();
    else if (r !== 'alarms') loadRoute(r);
  }, POLL.traffic);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
