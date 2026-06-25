/* dataviz-prod 운영 대시보드 프론트엔드 (Apple 디자인)
 *
 * - 패널별 독립 fetch(try/catch 격리) + setInterval 폴링
 * - 3-상태 렌더링(로딩/빈상태/정상), 빈 차트 미렌더
 * - 기간 버튼: uptime 전용 1세트(트래픽 메뉴는 폐지 — 이상 징후는 인사이트로)
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
  view: 60000,     /* 활성 뷰 일반 갱신 주기 */
  meta: 30000
};

/* 가동률 전용 기간 */
var uptimePeriod = 7;

/* Chart.js 인스턴스 보관(재조회 시 destroy 용) */
var charts = {};

/* ── 네이비/슬레이트 차트 색 토큰(Claude Design 목업 기준) ─── */
var C = {
  blue: '#3c5a8c',     /* 주 라인/막대(슬레이트블루 액센트) */
  blue2: '#8b95a1',    /* 보조 라인(tertiary) */
  gray: '#b4bbc4',     /* 옅은 회색 라인(disabled) */
  ok: '#3f8f6b',       /* success(목업 green) */
  alarm: '#c5473e',    /* destructive(목업 peak) */
  amber: '#be8636',    /* warning */
  ink: '#41495a'       /* 라이트 위 진한 글자(범례 등) */
};
/* 격자/축 — 라이트 위 옅은 격자, muted 축 글자 */
var GRID = '#eef0f3';
var TICK = '#9ba3ae';
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
    position: 'nearest',   /* 커서에 가장 가까운 점에 붙고 빈 쪽으로 자동 뒤집힘(막대·라인 공통) */
    backgroundColor: '#1e2530', borderColor: 'transparent', borderWidth: 0,
    titleColor: '#ffffff', bodyColor: '#cdd3da', padding: 10, cornerRadius: 8,
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
    ctx.fillStyle = TICK;
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
      ctx.fillStyle = '#1e2530';
      ctx.font = '800 22px ' + SYS_FONT;
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

/* 호스트/DB 시계열 색 팔레트(인스턴스 멀티라인용 — 목업 ec2Colors) */
var TS_COLORS = ['#3c5a8c', '#6e6597', '#3f8f6b', '#be8636', '#c5473e', '#5a6472', '#0891b2'];

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
  var labels = tsAll.map(function (t) { return tsLabel(t, opts.labelPeriod || 1); });   /* 기본 HH:MM, labelPeriod=30이면 MM-DD(일별) */
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
      borderColor: color, backgroundColor: opts.fill ? 'rgba(60,90,140,0.10)' : color,
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
        tooltip: {
          position: 'nearest',   /* 커서에 가장 가까운 점에 붙고 빈 쪽으로 자동 뒤집힘(차트 가림 방지) */
          itemSort: function (a, b) { return (b.parsed.y || 0) - (a.parsed.y || 0); },  /* 값 큰 순 */
          callbacks: { title: tsTitleCb(tsAll), label: unitLabel }
        }
      },
      scales: baseScales(opts.yBeginZero === false ? { y: { grid: { color: GRID }, ticks: { color: TICK } } } : null)
    }
  });
}

/* ── 인증 만료 처리 ─────────────────────────────────────
 * 401 수신 시 로그인 화면으로 보낸다. 여러 fetch 가 동시에 401 을 받아도
 * __loggingOut 가드로 리다이렉트는 한 번만 실행한다(중복 이동 방지). */
function redirectToLogin(reason) {
  if (window.__loggingOut) return;
  window.__loggingOut = true;
  location.replace('/login' + (reason === 'expired' ? '?expired=1' : ''));
}
/* 응답이 401 이면 로그인으로 보내고 true 반환(호출부는 이후 처리를 중단). */
function handle401(resp) {
  if (resp && resp.status === 401) { redirectToLogin('expired'); return true; }
  return false;
}

/* fetch JSON 헬퍼(HTTP 에러를 throw) */
function fetchJson(url) {
  return fetch(url).then(function (r) {
    if (handle401(r)) throw new Error('UNAUTHORIZED');
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
  avg: '평균 응답시간이에요. 기간 내 요청들의 평균 응답 속도로, 낮을수록 빠릅니다.',
  p95: '95%의 요청이 이 시간보다 빨랐다는 뜻이에요(느린 5%만 이보다 김). 가장 느린 사용자들의 체감 지연을 보여줍니다.',
  health: '서버가 살아있는지 확인하는 점검용 주소예요. 여기서 실패하면 서버 자체가 응답하지 못하는 상태입니다.',
  home: '실제 사용자가 보는 홈페이지예요. 사용자 체감 가동률·속도를 대표합니다.',
  conn: '지금 DB에 동시에 연결된 수예요. 한계에 가까워지면 새 연결이 거부될 수 있습니다.',
  mem: 'DB가 더 쓸 수 있는 남은 메모리예요. 0에 가까울수록 느려지거나 불안정해집니다.'
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
/* 드릴다운 영역 → 사람이 읽는 라벨(인사이트 카드 '자세히 보기' 버튼) */
var ROUTE_LABEL = {
  host: '호스트(EC2)', database: 'DB 성능', cdn: 'CDN', alarms: '알람',
  uptime: '가동률·응답시간'
};

/* id 요소의 textContent 설정(없으면 무시) */
function setText(id, v) {
  var el = document.getElementById(id);
  if (el) el.textContent = v;
}

/* finding → <li> 카드(외부유래 title/evidence 는 textContent 로만 주입)
 * compact=true(개요 '주목 필요'): 설명+이동만 / false(인사이트 뷰): 조치 목록까지 */
function makeInsightCard(f, compact) {
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
  /* 언제 감지됐는지(수집 시각) — 위치는 영역 배지+제목 */
  if (f.ts) {
    var when = document.createElement('div');
    when.className = 'insight-when';
    when.textContent = '🕘 ' + epochToKst(f.ts, true) + ' 감지';
    li.appendChild(when);
  }
  /* 쉬운 설명(무엇/왜) */
  if (f.meaning) {
    var mean = document.createElement('div');
    mean.className = 'insight-meaning';
    mean.textContent = f.meaning;
    li.appendChild(mean);
  }
  /* 권장 조치(인사이트 뷰에서만 펼침) */
  if (!compact && f.action && f.action.length) {
    var acts = document.createElement('div');
    acts.className = 'insight-actions';
    var ahead = document.createElement('div');
    ahead.className = 'insight-actions-head';
    ahead.textContent = '권장 조치';
    acts.appendChild(ahead);
    var aul = document.createElement('ul');
    aul.className = 'insight-action-list';
    f.action.forEach(function (step) {
      var ali = document.createElement('li');
      ali.textContent = step;
      aul.appendChild(ali);
    });
    acts.appendChild(aul);
    li.appendChild(acts);
  }
  /* 해당 영역으로 이동(드릴다운) */
  if (f.route) {
    var drill = document.createElement('button');
    drill.type = 'button';
    drill.className = 'insight-drill';
    drill.textContent = (ROUTE_LABEL[f.route] || f.route) + '에서 자세히 보기 →';
    drill.addEventListener('click', function () { location.hash = '#/' + f.route; });
    li.appendChild(drill);
  }
  return li;
}

/* 인사이트 뷰: /api/insights → 종합 카운트 + AI 코멘트 + 신호 리스트 */
function loadInsights() {
  var card = document.getElementById('panel-insights');
  if (!card) return;
  var body = card.querySelector('.card-body');
  return fetchJson('/api/insights').then(function (d) {
    var findings = d.findings || [];
    var s = d.summary || {};
    setText('ins-cnt-crit', s.critical || 0);
    setText('ins-cnt-warn', s.warning || 0);
    setText('ins-cnt-info', s.info || 0);
    updateInsightBadge(findings);
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

/* 인사이트 nav 배지(미해결 신호 수) — 0이면 숨김 */
function updateInsightBadge(findings) {
  var badge = document.getElementById('nav-insight-badge');
  if (!badge) return;
  var n = (findings && findings.length) || 0;
  if (n > 0) { badge.textContent = String(n); badge.hidden = false; }
  else { badge.hidden = true; }
}

/* 인라인 SVG 스파크라인(면적+라인) — 목업 mini() 이식. vals=[숫자…] */
function miniSpark(vals, color) {
  if (!vals || !vals.length) return '';
  if (vals.length === 1) vals = [vals[0], vals[0]];
  var W = 200, H = 48, p = 4;
  var max = Math.max.apply(null, vals) * 1.15 || 1;
  var xOf = function (i) { return p + (i / (vals.length - 1)) * (W - 2 * p); };
  var yOf = function (v) { return p + (H - 2 * p) - (v / max) * (H - 2 * p); };
  var d = vals.map(function (v, i) { return (i ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1); }).join(' ');
  var area = d + ' L ' + xOf(vals.length - 1).toFixed(1) + ' ' + (H - p) + ' L ' + p + ' ' + (H - p) + ' Z';
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:48px;display:block">'
    + '<path d="' + area + '" fill="' + color + '1f"/>'
    + '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

/* 시계열 [{t,v}] 배열을 t 기준으로 합산(여러 시리즈 → 시각별 합) */
function _sumSeriesByT(seriesList) {
  var agg = {};
  seriesList.forEach(function (pts) {
    (pts || []).forEach(function (pt) {
      if (pt && pt.v != null) agg[pt.t] = (agg[pt.t] || 0) + Number(pt.v);
    });
  });
  return Object.keys(agg).map(Number).sort(function (a, b) { return a - b; }).map(function (t) { return agg[t]; });
}
/* 시계열 [{t,v}] 배열을 t 기준으로 평균(시각별 평균) */
function _avgSeriesByT(seriesList) {
  var sum = {}, cnt = {};
  seriesList.forEach(function (pts) {
    (pts || []).forEach(function (pt) {
      if (pt && pt.v != null) { sum[pt.t] = (sum[pt.t] || 0) + Number(pt.v); cnt[pt.t] = (cnt[pt.t] || 0) + 1; }
    });
  });
  return Object.keys(sum).map(Number).sort(function (a, b) { return a - b; }).map(function (t) { return sum[t] / cnt[t]; });
}

/* 개요: 최근 24시간 추세 3종(EC2 평균 CPU / 주 DB CPU / CDN 요청수) 스파크라인 */
function loadDashboardTrend() {
  /* EC2 평균 CPU */
  fetchJson('/api/host').then(function (d) {
    var el = document.getElementById('trend-ec2-spark');
    var insts = (d && d.instances) || [];
    if (d.empty || !insts.length) { if (el) el.innerHTML = ''; setText('trend-ec2-val', '—'); return; }
    var vals = _avgSeriesByT(insts.map(function (it) { return it.cpu_series; }));
    var sum = 0, cnt = 0;
    insts.forEach(function (it) { if (it.cpu_avg != null) { sum += it.cpu_avg; cnt++; } });
    setText('trend-ec2-val', cnt ? fmtFixed(sum / cnt, 1) + '%' : '—');
    if (el) el.innerHTML = miniSpark(vals, C.blue);
  }).catch(function () {});

  /* 주 DB CPU(primary) */
  fetchJson('/api/db').then(function (d) {
    var el = document.getElementById('trend-db-spark');
    var insts = (d && d.instances) || [];
    if (d.empty || !insts.length) { if (el) el.innerHTML = ''; setText('trend-db-val', '—'); return; }
    var primary = insts[0];
    insts.forEach(function (it) { if (it.db_id === d.primary_db_id) primary = it; });
    var vals = (primary.cpu_series || []).map(function (pt) { return pt.v; }).filter(function (v) { return v != null; });
    setText('trend-db-val', primary.cpu_avg == null ? '—' : fmtFixed(primary.cpu_avg, 1) + '%');
    if (el) el.innerHTML = miniSpark(vals, C.ok);
  }).catch(function () {});

  /* CDN 요청수(합) */
  fetchJson('/api/cdn').then(function (d) {
    var el = document.getElementById('trend-cdn-spark');
    var dists = (d && d.distributions) || [];
    if (d.empty || !dists.length) { if (el) el.innerHTML = ''; setText('trend-cdn-val', '—'); return; }
    var vals = _sumSeriesByT(dists.map(function (it) { return it.requests_series; }));
    var tot = 0; dists.forEach(function (it) { if (it.requests != null) tot += it.requests; });
    setText('trend-cdn-val', fmtNum(tot));
    if (el) el.innerHTML = miniSpark(vals, '#6e6597');
  }).catch(function () {});
}

/* 개요 상단: 종합 상태 + 주목 필요(critical/warning) 카드 */
function loadDashboardInsight() {
  return fetchJson('/api/insights').then(function (d) {
    var findings = d.findings || [];
    var s = d.summary || {};
    setText('dash-cnt-crit', s.critical || 0);
    setText('dash-cnt-warn', s.warning || 0);
    setText('dash-cnt-info', s.info || 0);
    updateInsightBadge(findings);
    /* 영역별 요약: 인사이트 미니카드 */
    var totalSig = (s.critical || 0) + (s.warning || 0) + (s.info || 0);
    if (totalSig === 0) {
      sumSet('sum-insights-body', null, 'good', '주목할 신호 없음');
    } else {
      var topF = findings[0] || {};
      sumSet('sum-insights-body', [
        ['미해결 신호', fmtNum(totalSig), s.critical > 0 ? 'bad' : (s.warning > 0 ? 'warn' : '')],
        ['유형', (SEV_LABEL[topF.severity] || '정보') + ' · ' + (topF.area || topF.title || '—'), '']
      ]);
    }
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
      attn.forEach(function (f) { if (alist) alist.appendChild(makeInsightCard(f, true)); });
    }
  }).catch(function () {
    setText('dash-insight-text', '인사이트 불러오기 실패');
  });
}

/* 개요 로드: 종합 인사이트 + 기존 요약(미니카드/KPI — 정상 접기 안) */
function loadDashboard() {
  loadDashboardSummary();
  loadDashboardTrend();
  loadDashboardDooray();
  loadDashboardCal();
  return loadDashboardInsight();   /* AI 재생성 포함 — 새로고침 완료 신호로 사용 */
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
    if (s.alarm > 0) { setStatusPill('bad', '경보 ' + s.alarm); setKpiTile('kpi-alarms', '경보 ' + s.alarm, 'bad'); }
    else { setStatusPill('ok', '정상'); setKpiTile('kpi-alarms', '정상', 'good'); }
    setText('kpi-alarms-sub', '감시 ' + fmtNum(s.total) + ' · 경보 ' + fmtNum(s.alarm));
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
    setText('kpi-uptime-sub', '서비스 ' + eps.length + '종 정상');
    var rows = eps.slice(0, 3).map(function (ep) {
      var p = (s24[ep] && s24[ep].pct != null) ? s24[ep].pct : null;
      var cls = p == null ? '' : (p >= 99.5 ? 'good' : (p >= 95 ? 'warn' : 'bad'));
      return [ep, p == null ? '—' : fmtFixed(p, 2) + '%', cls];
    });
    sumSet('sum-uptime-body', rows);
  }).catch(function () {
    setKpiTile('kpi-uptime', '—', 'muted'); sumSet('sum-uptime-body', null, 'bad', '불러오기 실패');
  });

  /* 활성 사용자(7d) 토플라인 — 트래픽 메뉴는 폐지, KPI 수치만 유지 */
  fetchJson('/api/traffic?period=7').then(function (d) {
    if (d.empty || !d.total) { setKpiTile('kpi-users', '—', 'muted'); return; }
    setKpiTile('kpi-users', fmtNum(d.n_users), '');
  }).catch(function () {
    setKpiTile('kpi-users', '—', 'muted');
  });

  /* 트래픽·응답품질 요약 제거 — 메뉴 폐지(이상 징후는 인사이트, 5xx는 CDN에서 다룸) */

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
    setText('kpi-dbcpu-sub', (primary.db_id || '주 DB') + ' · ' + ((cpu != null && cpu >= 80) ? '주의' : '여유'));
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
      if (it.err_5xx != null && (maxErr === null || it.err_5xx > maxErr)) maxErr = it.err_5xx;
    });
    sumSet('sum-cdn-body', [
      ['배포', fmtNum(dists.length), ''],
      ['총 요청', fmtNum(totReq), ''],
      ['최대 5xx 에러율', maxErr == null ? '—' : fmtFixed(maxErr, 2) + '%', (maxErr != null && maxErr > 1) ? 'bad' : '']
    ]);
  }).catch(function () {
    sumSet('sum-cdn-body', null, 'bad', '불러오기 실패');
  });

}

/* 대시보드 Dooray 패널 — 진행/완료/할일 타일 + 프로젝트별 분포(풍부 요약) */
function loadDashboardDooray() {
  var host = document.getElementById('dash-dooray'); if (!host) return;
  fetchJson('/api/dooray').then(function (d) {
    var tasks = (d && d.tasks) || [];
    var wkName = (d && d.current_week && d.current_week.name) || '이번 주';
    setText('dash-dooray-week', '파트업무진행 · ' + wkName);
    if (d.empty || !tasks.length) {
      host.innerHTML = ''; var m = document.createElement('span'); m.className = 'mini-muted';
      m.textContent = (d && d.configured === false) ? '토큰 미설정' : '데이터 없음'; host.appendChild(m); return;
    }
    var cnt = { working: 0, closed: 0, registered: 0 }, byTag = {};
    tasks.forEach(function (t) {
      var c = t.workflowClass || 'registered'; cnt[c] = (cnt[c] || 0) + 1;
      (t.tags && t.tags.length ? t.tags : ['기타']).forEach(function (tag) { byTag[tag] = (byTag[tag] || 0) + 1; });
    });
    host.innerHTML = '';
    var tiles = document.createElement('div'); tiles.className = 'dd-tiles';
    [['진행', cnt.working, 'working'], ['완료', cnt.closed, 'closed'], ['할 일', cnt.registered, 'registered']].forEach(function (x) {
      var t = document.createElement('div'); t.className = 'dd-tile dd-' + x[2];
      var l = document.createElement('div'); l.className = 'dd-tile-l'; l.textContent = x[0];
      var v = document.createElement('div'); v.className = 'dd-tile-v'; v.textContent = x[1];
      t.appendChild(l); t.appendChild(v); tiles.appendChild(t);   /* 라벨 위 · 숫자 아래(KPI 타일과 통일) */
    });
    host.appendChild(tiles);
    var projs = Object.keys(byTag).filter(function (k) { return k !== '기타'; }).sort(function (a, b) { return byTag[b] - byTag[a]; });
    if (projs.length) {
      var wrap = document.createElement('div'); wrap.className = 'dd-projs';
      var h = document.createElement('div'); h.className = 'dd-projs-h'; h.textContent = '프로젝트별 (전체 ' + tasks.length + '건)'; wrap.appendChild(h);
      var ch = document.createElement('div'); ch.className = 'dd-chips';
      projs.slice(0, 8).forEach(function (k) {
        var c = document.createElement('span'); c.className = 'dd-chip';
        c.appendChild(document.createTextNode(k + ' '));
        var b = document.createElement('b'); b.textContent = byTag[k]; c.appendChild(b);
        ch.appendChild(c);
      });
      wrap.appendChild(ch); host.appendChild(wrap);
    }
  }).catch(function () { host.innerHTML = '<span class="mini-muted">불러오기 실패</span>'; });
}

/* 근태(휴가·반차) 여부 — 본부 일정을 근태/업무로 나누는 기준 */
function _isLeave(e) {
  var c = _calCat(e);
  return c === CAL_CATS.leave || c === CAL_CATS.amhalf || c === CAL_CATS.pmhalf;
}

/* 이번 주 아젠다(날짜별 그룹)를 host 에 렌더 */
function _renderAgenda(host, evs, emptyMsg) {
  host.innerHTML = '';
  if (!evs.length) { var m = document.createElement('span'); m.className = 'mini-muted'; m.textContent = emptyMsg; host.appendChild(m); return; }
  var curKey = null, group = null;
  evs.forEach(function (e) {
    var key = _calDayKeyOf(_calKstDate(e.start));
    if (key !== curKey) {
      curKey = key;
      var h = document.createElement('div'); h.className = 'dc-day'; h.textContent = _calDayLabel(e.start); host.appendChild(h);
      group = document.createElement('div'); group.className = 'dc-items'; host.appendChild(group);
    }
    var cat = _calCat(e);
    var row = document.createElement('div'); row.className = 'dc-event';
    var dot = document.createElement('span'); dot.className = 'dc-dot'; dot.style.background = cat.line; row.appendChild(dot);
    var tm = document.createElement('span'); tm.className = 'dc-time'; tm.textContent = e.all_day ? '' : _calHm(e.start); row.appendChild(tm);
    var ti = document.createElement('span'); ti.className = 'dc-title'; ti.textContent = e.title || ''; row.appendChild(ti);
    group.appendChild(row);
  });
}

/* 대시보드 본부 일정 — 이번 주(오늘~+7일)를 근태/업무 두 섹션으로 분리 */
function loadDashboardCal() {
  var leaveHost = document.getElementById('dash-cal-leave'), workHost = document.getElementById('dash-cal-work');
  if (!leaveHost || !workHost) return;
  fetchJson('/api/calendar').then(function (d) {
    var evs = (d && d.events) || [];
    var demo = !evs.length;
    if (demo) evs = _demoCalEvents();
    var nk = new Date(Date.now() + 9 * 3600 * 1000);
    var t0 = Date.UTC(nk.getUTCFullYear(), nk.getUTCMonth(), nk.getUTCDate()) / 1000 - 9 * 3600;  // 오늘 0시(KST)
    var dow = nk.getUTCDay();                         // 0=일 … 6=토(KST)
    var daysToMon = (1 - dow + 7) % 7; if (daysToMon === 0) daysToMon = 7;  // 오늘 ~ 이번 주 일요일(다음 월요일 직전)
    var weekEnd = t0 + daysToMon * 86400;
    var wk = evs.filter(function (e) { return e.start >= t0 && e.start < weekEnd; })
                .sort(function (a, b) { return a.start - b.start; });
    /* 개요에선 각 섹션을 오늘 포함 가까운 3건까지만(시간순). 전체 일정은 #/calendar 월간 달력에서. */
    var DASH_CAL_MAX = 3;
    _renderAgenda(leaveHost, wk.filter(_isLeave).slice(0, DASH_CAL_MAX), '이번 주 근태가 없습니다.');
    _renderAgenda(workHost, wk.filter(function (e) { return !_isLeave(e); }).slice(0, DASH_CAL_MAX), '이번 주 업무 일정이 없습니다.');
    var note = document.getElementById('dash-cal-note');
    if (note) { note.hidden = !demo; note.textContent = demo ? '예시 데이터 (iCal 연동 전 미리보기)' : ''; }
  }).catch(function () { leaveHost.innerHTML = '<span class="mini-muted">불러오기 실패</span>'; workHost.innerHTML = ''; });
}

/* 일정 시각 라벨: 오늘/내일/MM/DD + HH:MM(종일은 시간 생략) */
function _calWhen(e) {
  if (!e.start) return '';
  var d = new Date((e.start + 9 * 3600) * 1000);          /* +9h 후 UTC 게터 = KST */
  var now = new Date(Date.now() + 9 * 3600 * 1000);
  function ymd(x) { return x.getUTCFullYear() + '-' + (x.getUTCMonth() + 1) + '-' + x.getUTCDate(); }
  var tmr = new Date(now.getTime() + 86400000);
  var md = ('0' + (d.getUTCMonth() + 1)).slice(-2) + '/' + ('0' + d.getUTCDate()).slice(-2);
  var hm = ('0' + d.getUTCHours()).slice(-2) + ':' + ('0' + d.getUTCMinutes()).slice(-2);
  var day = (ymd(d) === ymd(now)) ? '오늘' : (ymd(d) === ymd(tmr) ? '내일' : md);
  return e.all_day ? day : (day + ' ' + hm);
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
    var items = d.items || [];
    document.getElementById('alarms-summary').textContent =
      '감시 ' + sum.total + '개 · 경보 ' + sum.alarm;

    /* KPI 타일 + 헤더 상태배지 */
    if (sum.alarm > 0) {
      setKpiTile('kpi-alarms', '경보 ' + sum.alarm, 'bad');
      setStatusPill('bad', '경보 ' + sum.alarm);
    } else {
      setKpiTile('kpi-alarms', '정상', 'good');
      setStatusPill('ok', '정상');
    }

    /* 현재 발생 중인 경보 요약(이벤트 중심) */
    var statusEl = document.getElementById('alarm-status');
    var statusTxt = document.getElementById('alarm-status-text');
    if (statusEl) statusEl.className = 'alarm-status ' + (sum.alarm > 0 ? 'bad' : 'ok');
    if (statusTxt) statusTxt.textContent = sum.alarm > 0
      ? ('현재 발생 중인 경보 ' + sum.alarm + '건 — 즉시 확인이 필요합니다')
      : '현재 발생 중인 경보 없음 · 모두 정상';

    /* 최근 알람 이벤트(상태 변경 시각 내림차순) */
    var events = document.getElementById('alarm-events');
    if (events) {
      events.innerHTML = '';
      var sorted = items.slice().sort(function (a, b) { return (b.state_updated || 0) - (a.state_updated || 0); });
      if (!sorted.length) {
        var noneEv = document.createElement('li');
        noneEv.className = 'alarm-event-none';
        noneEv.textContent = '최근 알람 이벤트 없음';
        events.appendChild(noneEv);
      }
      sorted.slice(0, 10).forEach(function (ev) {
        var einfo = alarmStateInfo(ev.state);
        var eli = document.createElement('li');
        eli.className = 'alarm-event ' + einfo.cls;
        var tm = document.createElement('span'); tm.className = 'alarm-event-time';
        tm.textContent = epochToKst(ev.state_updated, true);
        var nm = document.createElement('span'); nm.className = 'alarm-event-name';
        nm.textContent = alarmFriendly(ev.name) || ev.name;     /* 친근 이름, 없으면 원문 */
        nm.title = ev.name;
        var stt = document.createElement('span'); stt.className = 'alarm-event-state ' + einfo.cls;
        stt.textContent = einfo.text;
        eli.appendChild(tm); eli.appendChild(nm); eli.appendChild(stt);
        events.appendChild(eli);
      });
    }

    /* 감시 중인 알람(조건) — 접기 요약 라벨 */
    var cfgSummary = document.getElementById('alarm-config-summary');
    if (cfgSummary) cfgSummary.textContent = '감시 중인 알람 ' + sum.total + '개 (조건 보기)';

    /* 정보 풍부한 알람 카드(조건 — 접힘 안, createElement + textContent, XSS 안전) */
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

    /* 서비스별 카드: 가동률(큰 숫자) + 정상 배지 + 평균 응답시간 + 응답시간 면적 차트(목업) */
    var sumRow = document.getElementById('uptime-summary');
    sumRow.innerHTML = '';
    var s24 = d.summary24h || {};
    var RESP = [
      { line: C.blue, fill: 'rgba(60,90,140,0.10)' },
      { line: C.ok, fill: 'rgba(63,143,107,0.10)' },
      { line: '#6e6597', fill: 'rgba(110,101,151,0.10)' }
    ];
    eps.forEach(function (ep) {
      var s = s24[ep] || {};
      var pct = (s.pct === null || s.pct === undefined) ? null : s.pct;
      var cls = pct === null ? 'muted' : (pct >= 99.5 ? 'good' : (pct >= 95 ? 'warn' : 'bad'));
      var badge = pct === null ? { t: '데이터 없음', c: '' }
        : (pct >= 99.5 ? { t: '정상', c: 'good' } : (pct >= 95 ? { t: '주의', c: 'warn' } : { t: '위험', c: 'bad' }));
      /* 평균 응답시간(avg 평균) */
      var lat = 0, ln = 0;
      series[ep].forEach(function (pt) { if (pt.avg != null) { lat += pt.avg; ln++; } });
      var latMs = ln ? Math.round(lat / ln) : null;

      var cardEl = document.createElement('div');
      cardEl.className = 'uptime-card';
      var head = document.createElement('div');
      head.className = 'uptime-card-head';
      var lab = document.createElement('span');
      lab.className = 'uptime-card-label';
      lab.textContent = ep + ' · 가동률';
      var bd = document.createElement('span');
      bd.className = 'uptime-card-badge ' + badge.c;
      bd.textContent = badge.t;
      head.appendChild(lab); head.appendChild(bd);
      var val = document.createElement('div');
      val.className = 'uptime-card-val ' + cls;
      val.textContent = (pct === null ? '—' : fmtFixed(pct, 2) + '%');
      var latRow = document.createElement('div');
      latRow.className = 'uptime-card-latency';
      var lL = document.createElement('span'); lL.textContent = '평균 응답시간';
      var lV = document.createElement('b'); lV.textContent = (latMs === null ? '—' : latMs + 'ms');
      latRow.appendChild(lL); latRow.appendChild(lV);
      var chWrap = document.createElement('div');
      chWrap.className = 'chart-wrap uptime-resp';
      var cv = document.createElement('canvas');
      cv.id = 'uptime-resp-' + ep;
      chWrap.appendChild(cv);
      cardEl.appendChild(head); cardEl.appendChild(val); cardEl.appendChild(latRow); cardEl.appendChild(chWrap);
      sumRow.appendChild(cardEl);
    });

    /* KPI 스트립: health(없으면 첫 endpoint) 24h 가동률 */
    var primaryEp = (s24.health !== undefined) ? 'health' : eps[0];
    var hpct = (s24[primaryEp] && s24[primaryEp].pct !== null && s24[primaryEp].pct !== undefined)
      ? s24[primaryEp].pct : null;
    setKpiTile('kpi-uptime', hpct === null ? '—' : fmtFixed(hpct, 1) + '%', hpct === null ? 'muted' : 'good');

    /* 응답시간(avg) 면적 차트 — 축 표시, 단일 라인(목업) */
    eps.forEach(function (ep, i) {
      var arr = mapBy(ep, 'avg');
      var rc = RESP[i % RESP.length];
      var few = labels.length <= 16;
      renderChart('uptimeResp_' + ep, 'uptime-resp-' + ep, {
        type: 'line',
        data: { labels: labels, datasets: [{
          data: arr, borderColor: rc.line, backgroundColor: rc.fill,
          borderWidth: 2, tension: 0.3, spanGaps: true, fill: true,
          pointRadius: few ? 2.4 : 0, pointHoverRadius: 5, pointHitRadius: 8,
          pointBackgroundColor: '#fff', pointBorderColor: rc.line, pointBorderWidth: 1.5
        }] },
        options: {
          animation: false,
          plugins: {
            legend: { display: false }, title: { display: false },
            tooltip: { callbacks: { title: tsTitleCb(tsAll), label: function (it2) { return ' ' + Math.round(it2.parsed.y) + 'ms'; } } }
          },
          scales: {
            x: { display: true, grid: { display: false }, ticks: { color: TICK, font: { size: 10.5 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } },
            y: { display: true, beginAtZero: true, grid: { color: GRID }, ticks: { color: TICK, font: { size: 10.5 }, maxTicksLimit: 5 } }
          }
        }
      });
    });

    setState(body, 'ok');
  }).catch(function (e) {
    setState(body, 'error', e.message);
    setKpiTile('kpi-uptime', '—', 'muted');
  });
}

/* 트래픽·사용자 / 응답품질 패널 제거 — 메뉴 폐지(이상 징후는 insights.traffic_findings 로 표면화). */
/* (loadTraffic·loadQuality 및 전용 헬퍼 BUCKET_ORDER/BUCKET_COLOR/errCodeCls 삭제) */

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
      cardEl.className = 'host-card';   /* primary 강조 제거 — 선택된 것처럼 보여 혼란 */
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
      /* primary ★ 배지 제거 */
      head.appendChild(titleWrap);
      cardEl.appendChild(head);

      /* CPU 평균/최고(2열 stat + 막대) */
      cardEl.appendChild(cpuStatBlock(it.cpu_avg, it.cpu_max));

      /* 요약 행: 연결 · 여유공간 · 메모리 */
      function metaRow(label, valText, cls) {
        var r = document.createElement('div'); r.className = 'host-row';
        var l = document.createElement('span'); l.className = 'host-row-label'; l.textContent = label;
        var v = document.createElement('span'); v.className = 'host-row-val' + (cls ? ' ' + cls : ''); v.textContent = valText;
        r.appendChild(l); r.appendChild(v); cardEl.appendChild(r);
      }
      metaRow('연결 수', '평균 ' + fmtFixed(it.conn_avg, 0) + ' · 최고 ' + fmtFixed(it.conn_max, 0), '');
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
  document.getElementById('db-modal-title').textContent = (it.db_id || 'RDS 인스턴스');
  document.getElementById('db-modal-sub').textContent = it.db_id || '';

  var meta = document.getElementById('db-modal-meta');
  meta.innerHTML = '';
  var freeGb = (it.free_storage == null) ? null : it.free_storage / (1024 * 1024 * 1024);
  meta.appendChild(modalMetaRow('CPU 사용률', cpuUsageText(it.cpu_avg, it.cpu_max)));
  meta.appendChild(modalMetaRow('연결 수', '평균 ' + fmtFixed(it.conn_avg, 0) + ' · 최고 ' + fmtFixed(it.conn_max, 0), TERM_HELP.conn));
  meta.appendChild(modalMetaRow('여유공간', freeGb == null ? '—' : fmtFixed(freeGb, 1) + 'GB'));
  meta.appendChild(modalMetaRow('여유 메모리', it.mem_free == null ? '—' : fmtBytes(it.mem_free, 1), TERM_HELP.mem));
  /* 스왑·읽기/쓰기 지연·IOPS·DBLoad·디스크큐·최대 TXID 제거 — 비전문가에게 난해 */

  overlay.hidden = false;

  /* 시계열 4종(표시 후 렌더) */
  var dCpu = [];
  if (it.cpu_series && it.cpu_series.length) dCpu.push({ name: '평균', points: it.cpu_series, color: C.blue });
  if (it.cpu_max_series && it.cpu_max_series.length) dCpu.push({ name: '최고(순간)', points: it.cpu_max_series, color: C.alarm });
  renderTsLine('dbDetailCpu', 'db-detail-cpu-chart', dCpu.length ? dCpu : null,
    { title: 'CPU % — 최근 24시간(시간별)', unit: 'pct', legend: dCpu.length > 1, fill: dCpu.length === 1 });
  var dCpuD = [];
  if (it.cpu_series_d && it.cpu_series_d.length) dCpuD.push({ name: '평균', points: it.cpu_series_d, color: C.blue });
  if (it.cpu_max_series_d && it.cpu_max_series_d.length) dCpuD.push({ name: '최고', points: it.cpu_max_series_d, color: C.alarm });
  renderTsLine('dbDetailCpuD', 'db-detail-cpud-chart', dCpuD.length ? dCpuD : null,
    { title: 'CPU % — 최근 30일(일별)', unit: 'pct', legend: dCpuD.length > 1, fill: dCpuD.length === 1, labelPeriod: 30 });
  renderTsLine('dbDetailMem', 'db-detail-mem-chart',
    it.mem_series ? [{ name: '여유 메모리', points: it.mem_series, color: '#3f8f6b' }] : null,
    { title: '여유 메모리 (MB)', unit: 'mb', fill: true });
  /* DBLoad 차트 제거(전문 지표) */
  renderTsLine('dbDetailConn', 'db-detail-conn-chart',
    it.conn_series ? [{ name: '연결 수', points: it.conn_series, color: '#6e6597' }] : null,
    { title: '연결 수', unit: 'cnt', fill: true });
}

function closeDbModal() {
  var overlay = document.getElementById('db-modal');
  if (!overlay) return;
  overlay.hidden = true;
  ['dbDetailCpu', 'dbDetailCpuD', 'dbDetailMem', 'dbDetailConn'].forEach(function (k) {
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

/* 시계열에서 최댓값을 찍은 시각(KST) — "최고"는 언제 찍었는지가 핵심 */
function peakTime(series) {
  if (!series || !series.length) return null;
  var best = null;
  series.forEach(function (p) { if (p && p.v != null && (best === null || p.v > best.v)) best = p; });
  return best ? epochToKst(best.t, true).slice(5) : null;   /* 연도 생략(MM-DD HH:MM) */
}
/* CPU 사용률 표기 — "0.2% / 2.6%"가 비율로 오해돼서 라벨 분리 + 최고치 시각 표기 */
function cpuUsageText(avg, max, series) {
  var a = (avg == null) ? '—' : fmtFixed(avg, 1) + '%';
  if (max == null) return '평균 ' + a;
  var when = peakTime(series);
  return '평균 ' + a + ' · 최고 ' + fmtFixed(max, 1) + '%' + (when ? ' (' + when + ')' : '');
}

/* CPU 사용률 블록(카드용) — 평균/최고를 2열 stat + 막대로(시각 제거, 가독성↑) */
function _cpuCls(v) { return (v != null && v >= 80) ? 'bad' : (v != null && v >= 60 ? 'warn' : ''); }
function cpuStatBlock(avg, max) {
  var wrap = document.createElement('div'); wrap.className = 'host-cpu';
  var h = document.createElement('div'); h.className = 'host-cpu-head'; h.textContent = 'CPU 사용률'; wrap.appendChild(h);
  var stats = document.createElement('div'); stats.className = 'host-cpu-stats';
  function stat(label, val, cls) {
    var s = document.createElement('div'); s.className = 'host-stat';
    var l = document.createElement('span'); l.className = 'host-stat-l'; l.textContent = label;
    var v = document.createElement('span'); v.className = 'host-stat-v' + (cls ? ' ' + cls : '');
    v.textContent = (val == null ? '—' : fmtFixed(val, 1) + '%');
    s.appendChild(l); s.appendChild(v); return s;
  }
  stats.appendChild(stat('평균', avg, _cpuCls(avg)));
  if (max != null) stats.appendChild(stat('최고', max, _cpuCls(max)));
  wrap.appendChild(stats);
  var bar = document.createElement('div'); bar.className = 'host-bar';
  var fill = document.createElement('div'); fill.className = 'host-bar-fill' + (_cpuCls(avg) ? ' ' + _cpuCls(avg) : '');
  fill.style.width = Math.max(0, Math.min(100, avg == null ? 0 : avg)) + '%';
  bar.appendChild(fill); wrap.appendChild(bar);
  return wrap;
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
      cardEl.className = 'host-card';   /* primary 강조 제거 — 선택된 것처럼 보여 혼란 */
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
      /* primary ★ 배지 제거 */
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
      if (!it.private_ip) subParts.push(shortId(it.instance_id));   /* IP 없을 때만 식별자 노출 */
      var subEl = document.createElement('div');
      subEl.className = 'host-sub';
      subEl.textContent = subParts.join(' · ');     /* 외부유래 → textContent */
      cardEl.appendChild(subEl);

      /* CPU 평균/최고(2열 stat + 막대) */
      cardEl.appendChild(cpuStatBlock(it.cpu_avg, it.cpu_max));

      /* 네트워크/EBS/크레딧 미니 행 */
      function metaRow(label, valText, cls) {
        var r = document.createElement('div'); r.className = 'host-row';
        var l = document.createElement('span'); l.className = 'host-row-label'; l.textContent = label;
        var v = document.createElement('span'); v.className = 'host-row-val' + (cls ? ' ' + cls : ''); v.textContent = valText;
        r.appendChild(l); r.appendChild(v); cardEl.appendChild(r);
      }
      /* 네트워크 In/Out · EBS 읽기/쓰기 · CPU 크레딧 제거 — 용어 난해·액션 불가 */
      grid.appendChild(cardEl);
    });

    /* CPU 시계열 멀티라인(인스턴스별) */
    var series = insts
      .filter(function (it) { return it.cpu_series && it.cpu_series.length; })
      .map(function (it, i) {
        /* 범례·툴팁은 역할 이름 우선(없으면 IP → 마지막에 id 끝자리) — 카드 헤더와 일관 */
        var label = it.instance_name || it.private_ip || shortId(it.instance_id);
        return { name: label, points: it.cpu_series, color: TS_COLORS[i % TS_COLORS.length] };
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
  titleEl.textContent = (it.instance_name || it.instance_id || '인스턴스');
  document.getElementById('host-modal-sub').textContent = it.instance_id || '';

  /* 메타 그리드 */
  var meta = document.getElementById('host-modal-meta');
  meta.innerHTML = '';
  meta.appendChild(modalMetaRow('Private IP', it.private_ip));
  meta.appendChild(modalMetaRow('타입', it.instance_type));
  meta.appendChild(modalMetaRow('상태', it.state));
  meta.appendChild(modalMetaRow('CPU 사용률', cpuUsageText(it.cpu_avg, it.cpu_max)));
  /* 네트워크·CPU크레딧 제거 — 용어 난해·액션 불가 */

  overlay.hidden = false;

  /* 시계열 차트(CPU + 네트워크 In/Out) — 모달 표시 후 렌더(가시 상태라 크기 정상) */
  var hCpu = [];
  if (it.cpu_series && it.cpu_series.length) hCpu.push({ name: '평균', points: it.cpu_series, color: C.blue });
  if (it.cpu_max_series && it.cpu_max_series.length) hCpu.push({ name: '최고(순간)', points: it.cpu_max_series, color: C.alarm });
  renderTsLine('hostDetailCpu', 'host-detail-cpu-chart', hCpu.length ? hCpu : null,
    { title: 'CPU % — 최근 24시간(시간별)', unit: 'pct', legend: hCpu.length > 1, fill: hCpu.length === 1 });
  var hCpuD = [];
  if (it.cpu_series_d && it.cpu_series_d.length) hCpuD.push({ name: '평균', points: it.cpu_series_d, color: C.blue });
  if (it.cpu_max_series_d && it.cpu_max_series_d.length) hCpuD.push({ name: '최고', points: it.cpu_max_series_d, color: C.alarm });
  renderTsLine('hostDetailCpuD', 'host-detail-cpud-chart', hCpuD.length ? hCpuD : null,
    { title: 'CPU % — 최근 30일(일별)', unit: 'pct', legend: hCpuD.length > 1, fill: hCpuD.length === 1, labelPeriod: 30 });

  /* 네트워크 In/Out 차트 제거 — 용어 난해·액션 불가 */
}

function closeHostModal() {
  var overlay = document.getElementById('host-modal');
  if (!overlay) return;
  overlay.hidden = true;
  if (charts.hostDetailCpu) { charts.hostDetailCpu.destroy(); charts.hostDetailCpu = null; }
  if (charts.hostDetailCpuD) { charts.hostDetailCpuD.destroy(); charts.hostDetailCpuD = null; }
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
      var e5 = it.err_5xx;
      metaRow('5xx 서버 에러율', e5 == null ? '—' : fmtFixed(e5, 2) + '%', (e5 != null && e5 > 1) ? 'bad' : '');
      /* 5xx 유형 분해(502/503/504) — 무슨 에러였는지. 추가 지표 미활성이면 안내 */
      if (e5 != null && e5 > 0) {
        var parts = [];
        if (it.err_502 != null && it.err_502 > 0) parts.push('502 ' + fmtFixed(it.err_502, 2) + '%');
        if (it.err_503 != null && it.err_503 > 0) parts.push('503 ' + fmtFixed(it.err_503, 2) + '%');
        if (it.err_504 != null && it.err_504 > 0) parts.push('504 ' + fmtFixed(it.err_504, 2) + '%');
        metaRow('5xx 유형', parts.length ? parts.join(' · ') : '추가 지표 미활성(CloudFront)', '');
      }
      /* 4xx·전체 에러율 제거 — 4xx는 흔한 노이즈, 전체는 4xx로 부풀려져 오해 소지 */
      grid.appendChild(cardEl);
    });

    /* 요청수 시계열(배포별 멀티라인) */
    var reqSeries = dists
      .filter(function (it) { return it.requests_series && it.requests_series.length; })
      .map(function (it, i) { return { name: it.dist_id, points: it.requests_series, color: TS_COLORS[i % TS_COLORS.length] }; });
    renderTsLine('cdnReq', 'cdn-req-chart', reqSeries.length ? reqSeries : null,
      { title: '요청수 추세', unit: 'cnt', legend: reqSeries.length > 1, fill: reqSeries.length === 1 });

    /* 에러율 추세 차트 제거 — 4xx 포함 전체라 스파이크가 오해 소지(5xx 수치는 카드에 표기) */
    if (charts.cdnErr) { charts.cdnErr.destroy(); charts.cdnErr = null; }

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

/* SSE 스트리밍 채팅: POST /api/chat/stream 의 'data: {json}' 프레임을 읽어
 * 봇 말풍선에 답변 조각을 누적(textContent — XSS 안전). 첫 조각 도착 시 typing 제거.
 * 스트림 미지원/실패 시 비스트리밍 /api/chat 으로 폴백. */
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
  input.style.height = 'auto';                     /* textarea 높이 리셋 */

  /* 전송 중: 버튼 스피너 + typing indicator */
  setChatBusy(true);
  var typing = appendChatTyping();

  var botMsg = null, botTextEl = null, acc = '', finished = false, errored = false;

  function removeTyping() {
    if (typing && typing.parentNode) { typing.parentNode.removeChild(typing); typing = null; }
  }
  function ensureBot() {                            /* 첫 조각 도착 시 봇 말풍선 생성 */
    if (botMsg) return;
    removeTyping();
    botMsg = appendChatMsg('bot', '');
    botTextEl = botMsg.querySelector('.chat-text');
  }
  function pushText(t) {
    if (errored) return;                             /* 에러 표시 후 늦게 온 text 프레임 무시 */
    ensureBot();
    acc += t;
    botTextEl.textContent = acc;                   /* XSS: 조각 → textContent */
    chatScrollToBottom();
  }
  function fail(msg) {                              /* 받은 내용 있으면 유지, 없으면 에러 말풍선 1개만 */
    removeTyping();
    if (!acc && !errored) appendChatMsg('error', msg);
    errored = true;
  }
  function done() {
    if (finished) return;
    finished = true;
    removeTyping();
    if (!acc && !botMsg && !errored) appendChatMsg('error', '응답을 받지 못했습니다');
    setChatBusy(false);
    input.focus();
  }

  if (!window.fetch || !window.ReadableStream) {   /* 구형 브라우저 → 폴백 */
    sendChatFallback(q, removeTyping, function () { done(); });
    return;
  }

  fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q })
  }).then(function (r) {
    if (handle401(r)) return;
    if (!r.ok || !r.body) throw new Error('HTTP ' + r.status);
    var reader = r.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buf = '';
    function pump() {
      return reader.read().then(function (res) {
        if (res.done) { done(); return; }
        buf += decoder.decode(res.value, { stream: true });
        var idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {  /* SSE 프레임 경계 */
          var frame = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (frame.indexOf('data:') !== 0) continue;
          var data = frame.slice(5).trim();
          if (data === '[DONE]') { done(); return; }
          try {
            var j = JSON.parse(data);
            if (j.error) fail(j.error);
            else if (j.text) pushText(j.text);
          } catch (e) { /* 부분 프레임/파싱 실패 무시 */ }
        }
        return pump();
      });
    }
    return pump();
  }).catch(function (e) {
    /* 스트림 연결 자체 실패 → 비스트리밍으로 폴백(이미 일부 받았으면 그대로 종료) */
    if (acc) { done(); return; }
    sendChatFallback(q, removeTyping, function () { done(); });
  });
}

/* 비스트리밍 폴백: 기존 /api/chat(한 번에 answer) */
function sendChatFallback(q, removeTyping, onDone) {
  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q })
  }).then(function (r) {
    if (handle401(r)) return null;
    return r.json().then(function (j) { return { ok: r.ok, body: j }; });
  }).then(function (res) {
    if (!res) return;   /* 401 → 로그인 이동 중 */
    if (removeTyping) removeTyping();
    var b = res.body || {};
    if (b.error) appendChatMsg('error', b.error);
    else if (b.answer !== undefined && b.answer !== null) appendChatMsg('bot', b.answer);
    else appendChatMsg('error', '응답을 이해하지 못했습니다');
  }).catch(function (e) {
    if (removeTyping) removeTyping();
    appendChatMsg('error', '요청 실패: ' + e.message);
  }).then(function () { if (onDone) onDone(); });
}

function bindChat() {
  var form = document.getElementById('chat-form');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();                         /* 전송 버튼 → submit */
      sendChat();
    });
  }
  /* textarea: Enter=전송 / Shift+Enter=줄바꿈 / 한글 조합 중엔 전송 안 함 + 자동 높이 */
  var cinput = document.getElementById('chat-input');
  if (cinput) {
    cinput.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendChat(); }
    });
    cinput.addEventListener('input', function () {
      cinput.style.height = 'auto';
      cinput.style.height = Math.min(cinput.scrollHeight, 120) + 'px';
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
/* ── 업무(Dooray): 주간 보고 ──────────────────────────────────────── */
function _doorayEmptyText(d) {
  return d.configured
    ? '두레이 업무 수집 대기 중 — 잠시 후 자동으로 채워집니다.'
    : '두레이 토큰 미설정 — 서버 환경변수 DOORAY_TOKEN 을 설정하면 표시됩니다.';
}

var _weeklyTasks = [], _weeklyLayout = { buckets: [] }, _weeklyMeta = {};

/* Dooray 데이터 공통 적재(업무 현황·주간 보고 공용). onOk(d) 콜백, 빈/오류는 자체 처리 */
function _doorayFetch(card, onOk) {
  var body = card.querySelector('.card-body');
  return Promise.all([
    fetchJson('/api/dooray'),
    fetchJson('/api/dooray/layout').catch(function () { return null; })
  ]).then(function (arr) {
    var d = arr[0] || {};
    _weeklyLayout = ((arr[1] || {}).layout) || { buckets: [] };
    if (d.empty) {
      var e0 = body.querySelector('[data-state="empty"]');
      if (e0) e0.textContent = _doorayEmptyText(d);
      onOk(null);
      setState(body, 'empty'); return;
    }
    _weeklyTasks = d.tasks || [];
    _weeklyMeta = { project_name: d.project_name, week: (d.current_week || {}).name || '' };
    onOk(d);
    setState(body, 'ok');
  }).catch(function (e) { setState(body, 'error', e.message); });
}

/* 카드 설명(구어체) — mode 'tasks' | 'weekly' */
function _doorayNote(noteId, d, mode) {
  var note = document.getElementById(noteId); if (!note) return;
  note.innerHTML = '';
  var pname = d.project_name || '파트업무진행';
  var desc = document.createElement('span'); desc.className = 'card-sub-desc';
  if (mode === 'weekly') {
    desc.textContent = '이번 주 업무를 파트장 메일(전주 실적) 형식으로 만들어요. [주간보고 생성]을 누르면 도전·개선·생존 분류대로 정리되고, [복사]로 그대로 붙여넣을 수 있어요. 분류가 안 맞으면 [구성 편집]에서 바꾸세요.';
  } else {
    desc.textContent = '매일 아침 두레이 ‘' + pname + '’ 프로젝트에서 이번 주 업무를 자동으로 가져와 프로젝트별로 보여줘요. 업무를 클릭하면 본문·히스토리와 AI 요약을 팝업에서 확인할 수 있어요.';
  }
  var meta = document.createElement('span'); meta.className = 'card-sub-meta';
  meta.textContent = (_weeklyMeta.week || '이번 주') + ' · 업무 ' + _weeklyTasks.length + '건'
    + (d.collected_at ? ' · 최근 수집 ' + epochToKst(d.collected_at, true) : '');
  note.appendChild(desc); note.appendChild(meta);
}

/* 업무 현황: 프로젝트(도전·개선·생존)별 진행 + 도넛 요약 */
function loadTasks() {
  var card = document.getElementById('panel-tasks'); if (!card) return;
  return _doorayFetch(card, function (d) {
    if (!d) return;
    setText('tasks-week', _weeklyMeta.week);
    _doorayNote('tasks-note', d, 'tasks');
    var cnt = { registered: 0, working: 0, closed: 0 };
    _weeklyTasks.forEach(function (t) { var c = t.workflowClass || 'registered'; cnt[c] = (cnt[c] || 0) + 1; });
    _renderWeekSummary(_weeklyTasks, cnt, 'tasks-summary');
    _renderTasksByProject(_weeklyTasks, 'tasks-report');
  });
}

/* 업무 현황: 도전/개선/생존 없이 '프로젝트(태그)별'로만 그룹핑(많은 순) */
function _renderTasksByProject(tasks, wrapId) {
  var wrap = document.getElementById(wrapId); if (!wrap) return; wrap.innerHTML = '';
  if (!tasks.length) {
    var nz = document.createElement('div'); nz.className = 'insight-none-sub';
    nz.textContent = '이번 주 등록된 업무가 없습니다.'; wrap.appendChild(nz); return;
  }
  var byTag = _projectMap(tasks);
  var tags = Object.keys(byTag).sort(function (a, b) {
    if ((a === '기타') !== (b === '기타')) return a === '기타' ? 1 : -1;   /* 기타는 맨 뒤 */
    return (byTag[b].length - byTag[a].length) || a.localeCompare(b);
  });
  tags.forEach(function (tag) { wrap.appendChild(_renderProjectGroup({ tag: tag, tasks: byTag[tag] })); });
}

/* 주간 보고: 생성 버튼 → 전주 실적 양식 미리보기 → 복사 */
function loadWeekly() {
  var card = document.getElementById('panel-weekly'); if (!card) return;
  return _doorayFetch(card, function (d) {
    document.querySelectorAll('#panel-weekly .js-layout-edit').forEach(function (b) { b.hidden = !d; });
    if (!d) {                                        /* 데이터 없으면 생성 전(빈) 상태 유지 */
      var gen0 = document.getElementById('weekly-gen'); if (gen0) gen0.hidden = false;
      var pw0 = document.getElementById('weekly-preview-wrap'); if (pw0) pw0.hidden = true;
      return;
    }
    setText('weekly-week', _weeklyMeta.week);
    _doorayNote('weekly-note', d, 'weekly');
    _generateWeekly();                               /* 진입 시 자동으로 미리 생성 → 새로고침마다 최신 데이터로 갱신 */
  });
}

/* 주간보고 생성 — 전주 실적 양식 DOM 을 미리보기에 렌더(기타 제외, 복사와 동일) */
function _generateWeekly() {
  var host = document.getElementById('weekly-preview'); if (!host) return;
  host.innerHTML = '';
  host.appendChild(_buildWeeklyPreviewDOM());
  var gen = document.getElementById('weekly-gen'); if (gen) gen.hidden = true;
  var pw = document.getElementById('weekly-preview-wrap'); if (pw) pw.hidden = false;
}

function _wkLine(cls, txt) { var x = document.createElement('div'); x.className = cls; x.textContent = txt; return x; }

/* 버킷(도전·개선·생존) → 프로젝트 → o 업무 → ◦ 세부 를 root 에 추가(기타 제외, 복사와 동일) */
function _appendReportBody(root, tasks, layout) {
  var bz = _bucketize(tasks, layout);
  bz.buckets.forEach(function (bk) {
    if (!bk.projects.length) return;
    root.appendChild(_wkLine('wk-bucket', '[' + bk.label + ']' + (bk.goal ? ' ' + bk.goal : '')));
    bk.projects.forEach(function (p) {
      root.appendChild(_wkLine('wk-proj', p.tag));
      var seen = {};
      p.tasks.forEach(function (t) {
        var s = (t.subject || '').trim(); if (!s || seen[s]) return; seen[s] = 1;
        root.appendChild(_wkLine('wk-task', 'o ' + s));
        _bodyItems(t.body).forEach(function (ln) { root.appendChild(_wkLine('wk-sub', ln)); });
      });
    });
  });
}

function _buildWeeklyPreviewDOM() {
  var root = document.createElement('div'); root.className = 'wk-report';
  root.appendChild(_wkLine('wk-h1', '📋 전주 실적'));
  _appendReportBody(root, _weeklyTasks, _weeklyLayout);
  root.appendChild(_wkLine('wk-h1', '📅 금주 계획'));
  root.appendChild(_wkLine('wk-plain', '(다음 주 계획을 작성하세요)'));
  root.appendChild(_wkLine('wk-h1', '📋 기타사항'));
  root.appendChild(_wkLine('wk-plain', '특이사항 없음'));
  return root;
}

/* YYYY-MM → '26년 06월' */
function _fmtMonth(m) {
  if (!m || m.length < 7) return m || '';
  var p = m.split('-');
  return p[0].slice(2) + '년 ' + p[1] + '월';
}

/* 프로젝트(태그)별 본문 — 도전/개선/생존 버킷 없이(월간 리포트용). 많은 순, 기타 뒤 */
function _appendReportBodyByProject(root, tasks) {
  var byTag = _projectMap(tasks);
  Object.keys(byTag).sort(function (a, b) {
    if ((a === '기타') !== (b === '기타')) return a === '기타' ? 1 : -1;
    return (byTag[b].length - byTag[a].length) || a.localeCompare(b);
  }).forEach(function (tag) {
    root.appendChild(_wkLine('wk-proj', tag));
    var seen = {};
    byTag[tag].forEach(function (t) {
      var s = (t.subject || '').trim(); if (!s || seen[s]) return; seen[s] = 1;
      root.appendChild(_wkLine('wk-task', 'o ' + s));   /* 제목만 표시(요약·펼치기 없음) */
    });
  });
}

/* 월간 리포트 본문 렌더(프로젝트별, 버킷 없음) */
function _renderMonthlyReport() {
  var host = document.getElementById('monthly-report'); if (!host) return;
  host.innerHTML = '';
  host.appendChild(_wkLine('wk-h1', '📋 ' + _fmtMonth(_monthlyMonth) + ' 실적'));
  _appendReportBodyByProject(host, _monthlyTasks);
}

/* 레이아웃 저장 후 활성 Dooray 뷰 재렌더 */
function _refreshDoorayViews() {
  /* 업무 현황은 프로젝트별이라 레이아웃과 무관 → 재렌더 불필요.
     도전/개선/생존 레이아웃은 주간 보고·월간 리포트에만 영향. */
  var pw = document.getElementById('weekly-preview-wrap');
  if (pw && !pw.hidden) _generateWeekly();
  if (_monthlyTasks && _monthlyTasks.length) _renderMonthlyReport();
}

/* ── 월간 리포트(프로젝트별 누적, 월 선택) ── */
var _monthlyTasks = [];
var _monthlyMonth = '';
function loadMonthly() {
  var card = document.getElementById('panel-monthly'); if (!card) return;
  var body = card.querySelector('.card-body');
  return Promise.all([
    fetchJson('/api/dooray/monthly' + (_monthlyMonth ? '?month=' + encodeURIComponent(_monthlyMonth) : '')),
    fetchJson('/api/dooray/layout').catch(function () { return null; })
  ]).then(function (arr) {
    var d = arr[0] || {};
    _weeklyLayout = ((arr[1] || {}).layout) || _weeklyLayout || { buckets: [] };
    if (d.empty || !(d.months || []).length) {
      var e0 = body.querySelector('[data-state="empty"]');
      if (e0) e0.textContent = '아직 누적된 월간 데이터가 없습니다. 매일 아침 자동으로 쌓이며, 다음 달부터 온전히 채워집니다.';
      document.querySelectorAll('#panel-monthly .js-layout-edit').forEach(function (b) { b.hidden = true; });
      setState(body, 'empty'); return;
    }
    _monthlyMonth = d.month;
    _monthlyTasks = d.tasks || [];
    /* 월 선택 드롭다운 */
    var sel = document.getElementById('monthly-select');
    if (sel) {
      sel.innerHTML = '';
      (d.months || []).forEach(function (m) {
        var o = document.createElement('option'); o.value = m; o.textContent = _fmtMonth(m);
        if (m === d.month) o.selected = true; sel.appendChild(o);
      });
    }
    setText('monthly-month', _fmtMonth(d.month));
    var note = document.getElementById('monthly-note');
    if (note) {
      note.innerHTML = '';
      var ds = document.createElement('span'); ds.className = 'card-sub-desc';
      ds.textContent = '매일 아침 수집할 때마다 이번 주 업무를 ‘주차 시작월’ 기준으로 프로젝트별로 누적해요. 달이 바뀌면 새 칸에 쌓이고, [복사]로 월간 실적을 그대로 붙여넣을 수 있어요.';
      var ms = document.createElement('span'); ms.className = 'card-sub-meta';
      ms.textContent = _fmtMonth(d.month) + ' · 누적 업무 ' + _monthlyTasks.length + '건';
      note.appendChild(ds); note.appendChild(ms);
    }
    document.querySelectorAll('#panel-monthly .js-layout-edit').forEach(function (b) { b.hidden = false; });
    _renderMonthlyReport();
    setState(body, 'ok');
  }).catch(function (e) { setState(body, 'error', e.message); });
}

/* ── 본부 일정(Google Calendar) ── */
function _calKstDate(epoch) { return new Date((epoch + 9 * 3600) * 1000); }   /* +9h 후 UTC 게터 = KST */
function _calDayKeyOf(d) { return d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate(); }
function _calDayLabel(epoch) {
  var d = _calKstDate(epoch), now = new Date(Date.now() + 9 * 3600 * 1000), tmr = new Date(now.getTime() + 86400000);
  var W = ['일', '월', '화', '수', '목', '금', '토'];
  var md = (d.getUTCMonth() + 1) + '월 ' + d.getUTCDate() + '일 (' + W[d.getUTCDay()] + ')';
  if (_calDayKeyOf(d) === _calDayKeyOf(now)) return '오늘 · ' + md;
  if (_calDayKeyOf(d) === _calDayKeyOf(tmr)) return '내일 · ' + md;
  return md;
}
function _calHm(epoch) { var d = _calKstDate(epoch); return ('0' + d.getUTCHours()).slice(-2) + ':' + ('0' + d.getUTCMinutes()).slice(-2); }

var _calData = [];      /* 현재 표시 중 일정(실제 또는 데모) */
var _calDemo = false;   /* 데모 데이터 여부 */
var _calYM = null;      /* 표시 중 월 {y, m(0-based)} */

function _calTodayYM() { var n = new Date(Date.now() + 9 * 3600 * 1000); return { y: n.getUTCFullYear(), m: n.getUTCMonth() }; }

/* 일정 카테고리 — 근태(연차·반차·외근) vs 업무를 색으로 구분 */
var CAL_CATS = {
  work:   { key: 'work',   label: '업무',      line: '#3c5a8c', bg: '#ecf0f6' },
  leave:  { key: 'leave',  label: '연차·휴가', line: '#e0483b', bg: '#fdecea' },
  amhalf: { key: 'amhalf', label: '오전반차',  line: '#e8902a', bg: '#fcf1e0' },
  pmhalf: { key: 'pmhalf', label: '오후반차',  line: '#8257d6', bg: '#efe9fb' }
};
function _calCat(e) {
  var t = e.title || '';
  if (/오전\s*반차/.test(t)) return CAL_CATS.amhalf;
  if (/오후\s*반차/.test(t)) return CAL_CATS.pmhalf;
  if (/연차|휴가|반차|월차|경조/.test(t)) return CAL_CATS.leave;
  if (/외근|출장|파견/.test(t)) return CAL_CATS.work;   /* 외근·출장도 업무로 통일 */
  if (e.kind === 'leave') return CAL_CATS.leave;
  return CAL_CATS.work;
}

/* 더미(데모) 일정 — 오늘 기준 상대일로 생성(연동 전 미리보기용, 근태·업무 혼합) */
function _demoCalEvents() {
  var now = new Date(Date.now() + 9 * 3600 * 1000);
  var y = now.getUTCFullYear(), m = now.getUTCMonth(), dd = now.getUTCDate();
  function mk(off, h, min, title, loc, allday, kind) {
    var d = new Date(Date.UTC(y, m, dd + off, (h || 0) - 9, min || 0, 0));  /* KST→UTC epoch */
    return { start: Math.floor(d.getTime() / 1000), title: title, location: loc || '', all_day: !!allday, kind: kind || 'work' };
  }
  return [
    mk(-2, 14, 0, '스프린트 회고', '회의실 A', false, 'work'),
    mk(0, 10, 0, '주간 파트 회의', '대회의실', false, 'work'),
    mk(0, 0, 0, '김준오 연차', '', true, 'leave'),
    mk(1, 0, 0, '이경남 오전반차', '', true, 'leave'),
    mk(1, 11, 0, 'KT AI 에이전트 도입 회의', '방배', false, 'work'),
    mk(2, 14, 0, '[에너지 과제] 그린버튼 중간보고', '온라인', false, 'work'),
    mk(2, 0, 0, '안혜선 오후반차', '', true, 'leave'),
    mk(3, 15, 0, '국토부 진도보고(세종 출장)', '세종', false, 'work'),
    mk(5, 0, 0, '정화식 연차', '', true, 'leave'),
    mk(7, 10, 0, '[에너지 과제] TTA 시험 준비 점검', '회의실 B', false, 'work'),
    mk(8, 0, 0, '파트 워크샵 (1박2일)', '', true, 'work'),
    mk(9, 0, 0, '파트 워크샵 (1박2일)', '', true, 'work'),
    mk(12, 13, 0, '월간 보고', '대회의실', false, 'work'),
    mk(14, 0, 0, '신동윤 오전반차', '', true, 'leave')
  ];
}

/* 카테고리 범례 렌더 */
function _renderCalLegend() {
  var host = document.getElementById('cal-legend'); if (!host) return; host.innerHTML = '';
  ['work', 'leave', 'amhalf', 'pmhalf'].forEach(function (k) {
    var c = CAL_CATS[k];
    var item = document.createElement('span'); item.className = 'cal-legend-item';
    var dot = document.createElement('span'); dot.className = 'cal-legend-dot'; dot.style.background = c.line;
    item.appendChild(dot); item.appendChild(document.createTextNode(c.label));
    host.appendChild(item);
  });
}

function loadCalendar() {
  var card = document.getElementById('panel-calendar'); if (!card) return;
  var body = card.querySelector('.card-body');
  return fetchJson('/api/calendar').then(function (d) {
    var evs = (d && d.events) || [];
    _calDemo = !(evs.length);                       /* 실제 일정 없으면 데모로 미리보기 */
    _calData = _calDemo ? _demoCalEvents() : evs;
    setText('cal-window', _calDemo ? '예시' : ('다가오는 ' + (d.window_days || 14) + '일'));
    var note = document.getElementById('cal-note');
    if (note) {
      note.innerHTML = '';
      var ds = document.createElement('span'); ds.className = 'card-sub-desc';
      ds.textContent = _calDemo
        ? '구글 캘린더(본부 일정) 연동 전 미리보기예요. iCal 비밀 주소를 .env(GCAL_ICS_URL)에 넣고 재실행하면 실제 일정으로 바뀝니다.'
        : '구글 캘린더(본부 일정)에서 다가오는 일정을 자동으로 가져와 달력에 표시해요. 30분마다 갱신됩니다.';
      var ms = document.createElement('span'); ms.className = 'card-sub-meta';
      ms.textContent = _calDemo ? '예시 데이터' : ('일정 ' + evs.length + '건' + (d.collected_at ? ' · 최근 수집 ' + epochToKst(d.collected_at, true) : ''));
      note.appendChild(ds); note.appendChild(ms);
    }
    var dn = document.getElementById('cal-demo-note'); if (dn) dn.hidden = !_calDemo;
    if (!_calYM) _calYM = _calTodayYM();
    _renderCalLegend();
    _renderCalGrid();
    setState(body, 'ok');
  }).catch(function (e) { setState(body, 'error', e.message); });
}

/* 월간 달력 그리드 렌더(요일 헤더 + 날짜 셀 + 일정 칩) */
function _renderCalGrid() {
  var grid = document.getElementById('cal-grid'); if (!grid) return; grid.innerHTML = '';
  var y = _calYM.y, m = _calYM.m;
  setText('cal-title', y + '년 ' + (m + 1) + '월');
  ['일', '월', '화', '수', '목', '금', '토'].forEach(function (w, i) {
    var h = document.createElement('div'); h.className = 'cal-dow' + (i === 0 ? ' sun' : (i === 6 ? ' sat' : ''));
    h.textContent = w; grid.appendChild(h);
  });
  var byDay = {};
  _calData.forEach(function (e) {
    var d = _calKstDate(e.start);
    if (d.getUTCFullYear() === y && d.getUTCMonth() === m) (byDay[d.getUTCDate()] = byDay[d.getUTCDate()] || []).push(e);
  });
  Object.keys(byDay).forEach(function (k) { byDay[k].sort(function (a, b) { return a.start - b.start; }); });
  var startDow = new Date(Date.UTC(y, m, 1)).getUTCDay();
  var dim = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  var now = _calTodayYM(); var todayD = new Date(Date.now() + 9 * 3600 * 1000).getUTCDate();
  for (var b = 0; b < startDow; b++) { var ec = document.createElement('div'); ec.className = 'cal-cell empty'; grid.appendChild(ec); }
  for (var day = 1; day <= dim; day++) {
    var dow = (startDow + day - 1) % 7;
    var cell = document.createElement('div'); cell.className = 'cal-cell' + ((y === now.y && m === now.m && day === todayD) ? ' today' : '');
    var dnum = document.createElement('div'); dnum.className = 'cal-date' + (dow === 0 ? ' sun' : (dow === 6 ? ' sat' : ''));
    dnum.textContent = day; cell.appendChild(dnum);
    (byDay[day] || []).slice(0, 4).forEach(function (e) {
      var cat = _calCat(e);
      var chip = document.createElement('div'); chip.className = 'cal-chip cal-chip--' + cat.key;
      chip.title = (e.all_day ? '' : (_calHm(e.start) + ' ')) + (e.title || '') + ' · ' + cat.label + (e.location ? ' @' + e.location : '');
      /* 모든 일정 = 색 점 + (시간) + 제목 으로 통일 */
      var dot = document.createElement('span'); dot.className = 'cal-dot'; dot.style.background = cat.line; chip.appendChild(dot);
      if (!e.all_day) { var tt = document.createElement('span'); tt.className = 'cal-chip-t'; tt.style.color = cat.line; tt.textContent = _calHm(e.start); chip.appendChild(tt); }
      chip.appendChild(document.createTextNode(e.title || ''));
      cell.appendChild(chip);
    });
    var extra = (byDay[day] || []).length - 4;
    if (extra > 0) { var mo = document.createElement('div'); mo.className = 'cal-more'; mo.textContent = '+' + extra + '건 더'; cell.appendChild(mo); }
    grid.appendChild(cell);
  }
}

function _calShiftMonth(delta) {
  if (!_calYM) _calYM = _calTodayYM();
  var mm = _calYM.m + delta;
  _calYM = { y: _calYM.y + Math.floor(mm / 12), m: ((mm % 12) + 12) % 12 };
  _renderCalGrid();
}
function bindCalendarNav() {
  var p = document.getElementById('cal-prev'); if (p) p.addEventListener('click', function () { _calShiftMonth(-1); });
  var n = document.getElementById('cal-next'); if (n) n.addEventListener('click', function () { _calShiftMonth(1); });
  var t = document.getElementById('cal-today'); if (t) t.addEventListener('click', function () { _calYM = _calTodayYM(); _renderCalGrid(); });
}

/* tag -> tasks 맵(태그 없으면 '기타') */
function _projectMap(tasks) {
  var m = {};
  tasks.forEach(function (t) {
    var keys = (t.tags && t.tags.length) ? t.tags : ['기타'];
    keys.forEach(function (k) { (m[k] = m[k] || []).push(t); });
  });
  return m;
}

/* 레이아웃(buckets)에 따라 tasks 를 대항목→프로젝트로 정리. 미배정 태그는 etc(기타). */
function _bucketize(tasks, layout) {
  var byTag = _projectMap(tasks);
  var assigned = {};
  var buckets = ((layout && layout.buckets) || []).map(function (b) {
    var projs = [];
    (b.tags || []).forEach(function (tag) {
      if (byTag[tag] && byTag[tag].length) { projs.push({ tag: tag, tasks: byTag[tag] }); assigned[tag] = 1; }
    });
    return { label: b.label, goal: b.goal, projects: projs };
  });
  var etc = [];
  Object.keys(byTag).forEach(function (tag) { if (!assigned[tag]) etc.push({ tag: tag, tasks: byTag[tag] }); });
  etc.sort(function (a, b) {
    if ((a.tag === '기타') !== (b.tag === '기타')) return a.tag === '기타' ? 1 : -1;
    return (b.tasks.length - a.tasks.length) || a.tag.localeCompare(b.tag);
  });
  return { buckets: buckets, etc: etc };
}

/* 프로젝트(태그) 그룹 1개 렌더 — 태그 헤더 + 클릭형 업무행 */
function _renderProjectGroup(p) {
  var grp = document.createElement('div'); grp.className = 'report-group';
  var h = document.createElement('div'); h.className = 'report-tag'; h.textContent = '[' + p.tag + ']'; grp.appendChild(h);
  p.tasks.forEach(function (t) {
    var item = document.createElement('button'); item.type = 'button'; item.className = 'report-task report-task--btn';
    var head = document.createElement('div'); head.className = 'report-task-head';
    var s = document.createElement('span'); s.className = 'report-subj'; s.textContent = t.subject;
    var m = document.createElement('span'); m.className = 'report-meta'; m.textContent = t.assignee || '-';
    var sb = document.createElement('span'); sb.className = 'status-badge s-' + (t.workflowClass || 'registered'); sb.textContent = t.status || '';
    head.appendChild(s); head.appendChild(m); head.appendChild(sb);
    item.appendChild(head);
    item.addEventListener('click', function () { openTaskModal(t); });
    grp.appendChild(item);
  });
  return grp;
}

/* 주간보고 본문 — 대항목(버킷) 계층 + 목표문장 + 프로젝트별 업무(메일 형식) */
function _renderReportBuckets(tasks, layout, wrapId) {
  var wrap = document.getElementById(wrapId || 'weekly-report'); if (!wrap) return; wrap.innerHTML = '';
  if (!tasks.length) {
    var nz = document.createElement('div'); nz.className = 'insight-none-sub';
    nz.textContent = '이번 주 등록된 업무가 없습니다.'; wrap.appendChild(nz); return;
  }
  var bz = _bucketize(tasks, layout);
  function section(label, goal, projects, isEtc) {
    var sec = document.createElement('div'); sec.className = 'rep-bucket' + (isEtc ? ' rep-bucket--etc' : '');
    var bh = document.createElement('div'); bh.className = 'rep-bucket-head';
    var lbl = document.createElement('span'); lbl.className = 'rep-bucket-label'; lbl.textContent = '[' + label + ']'; bh.appendChild(lbl);
    if (goal) { var g = document.createElement('span'); g.className = 'rep-bucket-goal'; g.textContent = goal; bh.appendChild(g); }
    if (isEtc) { var hint = document.createElement('span'); hint.className = 'rep-bucket-hint'; hint.textContent = '구성 편집에서 도전·개선·생존으로 옮겨주세요'; bh.appendChild(hint); }
    sec.appendChild(bh);
    projects.forEach(function (p) { sec.appendChild(_renderProjectGroup(p)); });
    wrap.appendChild(sec);
  }
  bz.buckets.forEach(function (bk) { if (bk.projects.length) section(bk.label, bk.goal, bk.projects, false); });
  if (bz.etc.length) section('기타', '', bz.etc, true);
}

/* 복사 — 파트장 메일 형식(담당자 제외, 레이아웃 순서) */
function _esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

/* 업무 본문 → 세부 항목 줄(선두 기호 제거, 빈 줄 제외) */
function _bodyItems(body) {
  return (body || '').split('\n')
    .map(function (x) { return x.trim().replace(/^[■□○◦●※*\-o]\s*/, '').trim(); })
    .filter(Boolean);
}

/* 복사 — 메일 형식 리치텍스트(HTML 불릿 계층) + plain text 동시. 담당자 제외. */
function _copyReport(tasks, layout, header, withPlan, btnId) {
  var bz = _bucketize(tasks, layout);
  var H = ['<div style="font-family:Pretendard,\'Malgun Gothic\',sans-serif;font-size:14px;line-height:1.6">'];
  var T = [];
  H.push('<p style="font-weight:700">' + _esc(header) + '</p>'); T.push(header);

  function emit(label, goal, projects) {
    H.push('<p style="font-weight:700">[' + _esc(label) + ']' + (goal ? ' ' + _esc(goal) : '') + '</p>');
    T.push('[' + label + ']' + (goal ? ' ' + goal : ''));
    H.push('<ul>');
    projects.forEach(function (p) {
      var seen = {}, list = [];
      p.tasks.forEach(function (t) { var s = (t.subject || '').trim(); if (s && !seen[s]) { seen[s] = 1; list.push(t); } });
      H.push('<li><span style="font-weight:700">' + _esc(p.tag) + '</span>');
      T.push('• ' + p.tag);
      H.push('<ul>');
      list.forEach(function (t) {
        var s = (t.subject || '').trim();
        var items = _bodyItems(t.body);
        H.push('<li>' + _esc(s));
        T.push('  o ' + s);
        if (items.length) {
          H.push('<ul>');
          items.forEach(function (ln) { H.push('<li>' + _esc(ln) + '</li>'); T.push('    ◦ ' + ln); });
          H.push('</ul>');
        }
        H.push('</li>');
      });
      H.push('</ul></li>');
    });
    H.push('</ul>');
    T.push('');
  }

  bz.buckets.forEach(function (bk) { if (bk.projects.length) emit(bk.label, bk.goal, bk.projects); });
  /* 기타(미분류)는 실적 복사에서 제외 — 화면에는 표시하되 메일엔 넣지 않음 */
  if (withPlan) {
    H.push('<p style="font-weight:700">📅 금주 계획</p><p>(다음 주 계획을 작성하세요)</p>');
    H.push('<p style="font-weight:700">📋 기타사항</p><p>특이사항 없음</p>');
    T.push('📅 금주 계획'); T.push('(다음 주 계획을 작성하세요)'); T.push('');
    T.push('📋 기타사항'); T.push('특이사항 없음');
  }
  H.push('</div>');
  _writeRich(H.join(''), T.join('\n'), btnId);
}

function _copyWeeklyMail() { _copyReport(_weeklyTasks, _weeklyLayout, '📋 전주 실적', true, 'weekly-copy'); }
/* 월간 리포트 복사 — 프로젝트별(도전/개선/생존 버킷 없음) */
function _copyMonthly() {
  var byTag = _projectMap(_monthlyTasks);
  var tags = Object.keys(byTag).sort(function (a, b) {
    if ((a === '기타') !== (b === '기타')) return a === '기타' ? 1 : -1;
    return (byTag[b].length - byTag[a].length) || a.localeCompare(b);
  });
  var header = '📋 ' + _fmtMonth(_monthlyMonth) + ' 실적';
  var H = ['<div style="font-family:Pretendard,\'Malgun Gothic\',sans-serif;font-size:14px;line-height:1.6">'];
  var T = [];
  H.push('<p style="font-weight:700">' + _esc(header) + '</p>'); T.push(header);
  H.push('<ul>');
  tags.forEach(function (tag) {
    var seen = {}, list = [];
    byTag[tag].forEach(function (t) { var s = (t.subject || '').trim(); if (s && !seen[s]) { seen[s] = 1; list.push(t); } });
    H.push('<li><span style="font-weight:700">' + _esc(tag) + '</span>'); T.push('• ' + tag);
    H.push('<ul>');
    list.forEach(function (t) {
      var s = (t.subject || '').trim(); var items = _bodyItems(t.body);
      H.push('<li>' + _esc(s)); T.push('  o ' + s);
      if (items.length) { H.push('<ul>'); items.forEach(function (ln) { H.push('<li>' + _esc(ln) + '</li>'); T.push('    ◦ ' + ln); }); H.push('</ul>'); }
      H.push('</li>');
    });
    H.push('</ul></li>');
  });
  H.push('</ul></div>');
  _writeRich(H.join(''), T.join('\n'), 'monthly-copy');
}

/* HTML+plain 동시 클립보드 쓰기(붙여넣는 곳이 리치면 불릿, plain이면 텍스트). */
function _writeRich(html, text, btnId) {
  var done = function () { var c = document.getElementById(btnId || 'weekly-copy'); if (c) { c.textContent = '복사됨 ✓'; setTimeout(function () { c.textContent = '복사'; }, 1500); } };
  var plain = function () { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done); else done(); };
  if (navigator.clipboard && window.ClipboardItem) {
    try {
      var item = new ClipboardItem({
        'text/html': new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' })
      });
      navigator.clipboard.write([item]).then(done, plain);
      return;
    } catch (e) { /* ClipboardItem 미지원 → plain fallback */ }
  }
  plain();
}

/* ── 구성 편집(대항목·목표·프로젝트 배정/순서) — 누구나 수정, 서버 저장 ── */
var _editLayout = null;

function bindWeeklyTools() {
  var copyBtn = document.getElementById('weekly-copy');
  if (copyBtn) copyBtn.addEventListener('click', _copyWeeklyMail);
  var gen = document.getElementById('weekly-generate');
  if (gen) gen.addEventListener('click', _generateWeekly);
  var regen = document.getElementById('weekly-regen');
  if (regen) regen.addEventListener('click', _generateWeekly);
  var mcopy = document.getElementById('monthly-copy');
  if (mcopy) mcopy.addEventListener('click', _copyMonthly);
  var msel = document.getElementById('monthly-select');
  if (msel) msel.addEventListener('change', function () { _monthlyMonth = msel.value; loadMonthly(); });
  /* 구성 편집 버튼(업무 현황·주간 보고·월간 양쪽) — 클래스로 일괄 바인딩 */
  document.querySelectorAll('.js-layout-edit').forEach(function (b) { b.addEventListener('click', openLayoutEditor); });
  var ov = document.getElementById('layout-modal');
  function hide() { if (ov) ov.hidden = true; }
  var close = document.getElementById('layout-modal-close'); if (close) close.addEventListener('click', hide);
  var cancel = document.getElementById('layout-cancel'); if (cancel) cancel.addEventListener('click', hide);
  if (ov) ov.addEventListener('click', function (ev) { if (ev.target === ov) hide(); });
  var save = document.getElementById('layout-save'); if (save) save.addEventListener('click', _saveLayout);
  bindCalendarNav();
}

function openLayoutEditor() {
  var base = (_weeklyLayout && _weeklyLayout.buckets) ? _weeklyLayout : { buckets: [] };
  _editLayout = JSON.parse(JSON.stringify(base));
  if (!_editLayout.buckets) _editLayout.buckets = [];
  var msg = document.getElementById('layout-msg'); if (msg) msg.textContent = '';
  _renderLayoutEditor();
  var ov = document.getElementById('layout-modal'); if (ov) ov.hidden = false;
}

function _allWeekTags() {
  var s = {};
  _weeklyTasks.forEach(function (t) { (t.tags && t.tags.length ? t.tags : ['기타']).forEach(function (tag) { s[tag] = 1; }); });
  return Object.keys(s);
}

function _swap(arr, i, j) { if (i < 0 || j < 0 || i >= arr.length || j >= arr.length) return; var t = arr[i]; arr[i] = arr[j]; arr[j] = t; }

function _leBtn(label, disabled, fn, extra) {
  var b = document.createElement('button'); b.type = 'button'; b.className = 'le-ctrl-btn ' + (extra || '');
  b.textContent = label; b.disabled = !!disabled;
  if (!disabled) b.addEventListener('click', fn);
  return b;
}

var _dragSrc = null;

function _clearDrop() {
  var els = document.querySelectorAll('.le-drop');
  for (var i = 0; i < els.length; i++) els[i].classList.remove('le-drop');
}

/* 드래그 이동: src(태그)를 toBucket 의 insertIdx 위치로(미배정→버킷 포함) */
function _moveTag(src, toBucket, insertIdx) {
  var tag = src.tag;
  if (src.b >= 0) { var arr = _editLayout.buckets[src.b].tags; var k = arr.indexOf(tag); if (k >= 0) arr.splice(k, 1); }
  var dst = _editLayout.buckets[toBucket].tags;
  var ex = dst.indexOf(tag); if (ex >= 0) { dst.splice(ex, 1); if (ex < insertIdx) insertIdx--; }
  if (insertIdx < 0) insertIdx = 0;
  if (insertIdx > dst.length) insertIdx = dst.length;
  dst.splice(insertIdx, 0, tag);
}

function _renderLayoutEditor() {
  var host = document.getElementById('layout-editor'); if (!host) return; host.innerHTML = '';
  var buckets = _editLayout.buckets;
  var assigned = {};
  buckets.forEach(function (b) { (b.tags || []).forEach(function (tg) { assigned[tg] = 1; }); });

  /* 드래그 가능한 프로젝트 칩(bi<0 = 미배정 풀) */
  function makeChip(tag, bi) {
    var chip = document.createElement('span'); chip.className = 'le-chip'; chip.draggable = true;
    var grip = document.createElement('span'); grip.className = 'le-grip'; grip.setAttribute('aria-hidden', 'true'); grip.textContent = '⠿'; chip.appendChild(grip);
    var nm = document.createElement('span'); nm.className = 'le-chip-nm'; nm.textContent = tag; chip.appendChild(nm);
    if (bi >= 0) {
      chip.appendChild(_leBtn('×', false, function () {
        var arr = buckets[bi].tags; var k = arr.indexOf(tag); if (k >= 0) arr.splice(k, 1); _renderLayoutEditor();
      }, 'le-mini le-x'));
    }
    chip.addEventListener('dragstart', function (e) {
      _dragSrc = { b: bi, tag: tag }; e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', tag); } catch (err) { /* IE 가드 */ }
      setTimeout(function () { chip.classList.add('dragging'); }, 0);
    });
    chip.addEventListener('dragend', function () { chip.classList.remove('dragging'); _clearDrop(); });
    return chip;
  }

  /* 드롭 영역(toBucket<0 = 미배정으로 빼기) */
  function dropZone(el, toBucket) {
    el.addEventListener('dragover', function (e) { e.preventDefault(); el.classList.add('le-drop'); });
    el.addEventListener('dragleave', function (e) { if (e.target === el) el.classList.remove('le-drop'); });
    el.addEventListener('drop', function (e) {
      e.preventDefault(); el.classList.remove('le-drop');
      if (!_dragSrc) return;
      if (toBucket < 0) {
        if (_dragSrc.b >= 0) { var arr = buckets[_dragSrc.b].tags; var k = arr.indexOf(_dragSrc.tag); if (k >= 0) arr.splice(k, 1); }
      } else {
        var tc = (e.target && e.target.closest) ? e.target.closest('.le-chip') : null;
        var idx = buckets[toBucket].tags.length;
        if (tc) { var ci = Array.prototype.indexOf.call(el.children, tc); if (ci >= 0) idx = ci; }
        _moveTag(_dragSrc, toBucket, idx);
      }
      _dragSrc = null; _renderLayoutEditor();
    });
  }

  /* 대항목/기타 카드 1개(bi<0 = 기타=미배정). 대항목 구성은 고정(추가/삭제/순서 없음). */
  function makeCard(label, tags, bi, goalVal, goalSetter, hint) {
    var card = document.createElement('div'); card.className = 'le-bucket' + (bi < 0 ? ' le-bucket-etc' : '');
    var hd = document.createElement('div'); hd.className = 'le-bucket-hd';
    var lbl = document.createElement('span'); lbl.className = 'le-label-fixed'; lbl.textContent = '[' + label + ']'; hd.appendChild(lbl);
    var cnt = document.createElement('span'); cnt.className = 'le-count'; cnt.textContent = tags.length + '개'; hd.appendChild(cnt);
    card.appendChild(hd);
    if (bi >= 0) {
      var goal = document.createElement('input'); goal.className = 'le-goal'; goal.value = goalVal || ''; goal.placeholder = '목표 문장(선택)';
      goal.addEventListener('input', function () { goalSetter(goal.value); });
      card.appendChild(goal);
    } else if (hint) {
      var h = document.createElement('div'); h.className = 'le-etc-hint'; h.textContent = hint; card.appendChild(h);
    }
    var chips = document.createElement('div'); chips.className = 'le-chips';
    tags.forEach(function (tag) { chips.appendChild(makeChip(tag, bi)); });
    if (!tags.length) { var em = document.createElement('div'); em.className = 'le-empty'; em.textContent = '여기로 프로젝트를 끌어다 놓으세요'; chips.appendChild(em); }
    dropZone(chips, bi);
    card.appendChild(chips);
    return card;
  }

  buckets.forEach(function (b, bi) {
    host.appendChild(makeCard(b.label || ('대항목 ' + (bi + 1)), b.tags || [], bi, b.goal, function (v) { b.goal = v; }));
  });

  var unassigned = _allWeekTags().filter(function (tg) { return !assigned[tg]; });
  host.appendChild(makeCard('기타', unassigned, -1, '', null,
    '여기 프로젝트는 보고서에서 [기타]로 묶입니다 · 위 대항목으로 드래그해 옮기세요'));
}

function _saveLayout() {
  var clean = {
    buckets: (_editLayout.buckets || [])
      .filter(function (b) { return (b.label || '').trim(); })
      .map(function (b) { return { label: (b.label || '').trim(), goal: (b.goal || '').trim(), tags: (b.tags || []).slice() }; })
  };
  var msg = document.getElementById('layout-msg'); if (msg) msg.textContent = '저장 중…';
  fetch('/api/dooray/layout', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layout: clean })
  }).then(function (r) { if (handle401(r)) return null; return r.json(); }).then(function (res) {
    if (!res) return;   /* 401 → 로그인 이동 중 */
    if (res && res.ok) {
      _weeklyLayout = res.layout || clean;
      if (msg) msg.textContent = '저장됨 ✓';
      _refreshDoorayViews();
      setTimeout(function () { var ov = document.getElementById('layout-modal'); if (ov) ov.hidden = true; }, 600);
    } else if (msg) { msg.textContent = (res && res.error) || '저장 실패'; }
  }).catch(function (e) { if (msg) msg.textContent = '저장 실패: ' + e.message; });
}

function _renderWeekSummary(tasks, cnt, hostId) {
  var host = document.getElementById(hostId || 'weekly-summary'); if (!host) return; host.innerHTML = '';
  var sh = document.createElement('div'); sh.className = 'week-summary-h'; sh.textContent = '이번 주 현황'; host.appendChild(sh);
  var box = document.createElement('div'); box.className = 'week-summary';
  var dwrap = document.createElement('div'); dwrap.className = 'chart-wrap chart-donut';
  var cv = document.createElement('canvas'); cv.id = 'week-status-chart'; dwrap.appendChild(cv);
  box.appendChild(dwrap);
  var tiles = document.createElement('div'); tiles.className = 'week-status-tiles';
  var defs = [['working', '진행', cnt.working || 0], ['closed', '완료', cnt.closed || 0], ['registered', '할 일', cnt.registered || 0]];
  defs.forEach(function (dd) {
    var b = document.createElement('button'); b.type = 'button'; b.className = 'status-tile status-' + dd[0];
    var v = document.createElement('span'); v.className = 'status-tile-val'; v.textContent = dd[2];
    var l = document.createElement('span'); l.className = 'status-tile-label'; l.textContent = dd[1] + ' · 담당자 보기';
    b.appendChild(v); b.appendChild(l);
    b.addEventListener('click', function () { _showAssignees(tasks, dd[0], dd[1], b, tiles); });
    tiles.appendChild(b);
  });
  box.appendChild(tiles);
  host.appendChild(box);
  var ap = document.createElement('div'); ap.className = 'week-assignee'; ap.id = 'week-assignee'; ap.hidden = true;
  host.appendChild(ap);
  var tot = (cnt.working || 0) + (cnt.closed || 0) + (cnt.registered || 0);
  renderChart('weekStatus', 'week-status-chart', {
    type: 'doughnut',
    data: { labels: ['진행', '완료', '할 일'], datasets: [{ data: [cnt.working || 0, cnt.closed || 0, cnt.registered || 0], backgroundColor: [C.blue, C.ok, C.gray], borderWidth: 0 }] },
    options: {
      animation: false,
      cutout: '62%',
      plugins: {
        legend: { display: true, position: 'bottom', labels: { color: C.ink, boxWidth: 10, padding: 10, font: { size: 11.5 } } },
        title: { display: false },
        tooltip: { callbacks: { label: function (it) { var pct = tot ? Math.round(it.parsed / tot * 100) : 0; return ' ' + it.label + ' ' + it.parsed + '건 (' + pct + '%)'; } } }
      }
    }
  });
}

function _showAssignees(tasks, cls, label, tileEl, tilesEl) {
  var ap = document.getElementById('week-assignee'); if (!ap) return;
  var wasActive = tileEl.classList.contains('active');
  tilesEl.querySelectorAll('.status-tile').forEach(function (x) { x.classList.remove('active'); });
  if (wasActive) { ap.hidden = true; return; }
  tileEl.classList.add('active');
  var subset = tasks.filter(function (t) { return (t.workflowClass || 'registered') === cls; });
  var by = {};
  subset.forEach(function (t) { (by[t.assignee] = by[t.assignee] || []).push(t); });
  var names = Object.keys(by).sort(function (a, b) { return by[b].length - by[a].length; });
  ap.innerHTML = '';
  var h = document.createElement('div'); h.className = 'week-assignee-head';
  h.textContent = label + ' ' + subset.length + '건 · 담당자 ' + names.length + '명';
  ap.appendChild(h);
  names.forEach(function (nm) {
    var row = document.createElement('div'); row.className = 'assignee-row';
    var who = document.createElement('span'); who.className = 'assignee-name'; who.textContent = nm + ' (' + by[nm].length + ')';
    row.appendChild(who);
    var box2 = document.createElement('div'); box2.className = 'assignee-tasks';
    by[nm].forEach(function (t) {
      var chip = document.createElement('button'); chip.type = 'button'; chip.className = 'task-chip-sm'; chip.textContent = t.subject;
      chip.addEventListener('click', function () { openTaskModal(t); });
      box2.appendChild(chip);
    });
    row.appendChild(box2);
    ap.appendChild(row);
  });
  if (!names.length) { var nz = document.createElement('div'); nz.className = 'insight-none-sub'; nz.textContent = '해당 상태의 업무가 없습니다.'; ap.appendChild(nz); }
  ap.hidden = false;
}

/* 인라인 마크다운(**굵게**, `코드`) — 외부유래 문자열은 textContent 로만 주입(XSS 안전) */
function _mdInline(text, parent) {
  var re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  var last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[2] !== undefined) { var bb = document.createElement('b'); bb.textContent = m[2]; parent.appendChild(bb); }
    else { var cc = document.createElement('code'); cc.textContent = m[3]; parent.appendChild(cc); }
    last = re.lastIndex;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

/* 마크다운/두레이 본문(■ 대제목 · ○◦ 중제목 · -*•o 항목 · ※ 비고)을 계층 DOM 으로 렌더 */
function renderMarkdown(text) {
  var root = document.createElement('div'); root.className = 'md';
  var list = null, listType = null;
  function flush() { if (list) { root.appendChild(list); list = null; listType = null; } }
  function pushLi(type, cls, content) {
    if (!list || listType !== type) { flush(); list = document.createElement(type); list.className = cls; listType = type; }
    var li = document.createElement('li'); _mdInline(content, li); list.appendChild(li);
  }
  (text || '').split('\n').forEach(function (raw) {
    var t = raw.trim();
    if (!t) { flush(); return; }
    var mH1 = /^(#{1,2}\s+|■\s*|□\s*)(.*)$/.exec(t);
    var mH2 = /^(#{3,}\s+|○\s*|◦\s*|●\s*)(.*)$/.exec(t);
    var mNote = /^(※|☞)\s*(.*)$/.exec(t);
    var mOl = /^(\d+)[.)]\s+(.*)$/.exec(t);
    var mLi = /^([-*•]|o|ㅇ)\s+(.*)$/.exec(t);
    if (mH1) { flush(); var h = document.createElement('div'); h.className = 'md-h'; _mdInline(mH1[2], h); root.appendChild(h); return; }
    if (mH2) { flush(); var h2 = document.createElement('div'); h2.className = 'md-h2'; _mdInline(mH2[2], h2); root.appendChild(h2); return; }
    if (mNote) { flush(); var nb = document.createElement('div'); nb.className = 'md-note'; _mdInline(mNote[2], nb); root.appendChild(nb); return; }
    if (mOl) { pushLi('ol', 'md-ol', mOl[2]); return; }
    if (mLi) { pushLi('ul', 'md-ul', mLi[2]); return; }
    flush(); var p = document.createElement('div'); p.className = 'md-p'; _mdInline(t, p); root.appendChild(p);
  });
  flush();
  return root;
}

/* 작성자별 아바타 색(이름 해시 → 고정 팔레트) — 두레이 프로필 사진 대용 */
var AVATAR_COLORS = [
  ['#e6edf6', '#2f4368'], ['#e9f2ec', '#27754f'], ['#f3eee6', '#9a6b1e'],
  ['#efe9f3', '#6e6597'], ['#fbeceb', '#b1473c'], ['#e5eef0', '#356b73'],
  ['#f1ece4', '#7a5a2e'], ['#eaeef3', '#4e5a72']
];
function _avatarColor(name) {
  var s = name || '';
  var h = 0;
  for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff; }
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function openTaskModal(t) {
  var ov = document.getElementById('task-modal'); if (!ov) return;
  document.getElementById('task-modal-title').textContent = t.subject || '업무';
  document.getElementById('task-modal-sub').textContent =
    ((t.tags && t.tags.length) ? '[' + t.tags.join('] [') + ']  ' : '') + (t.assignee || '-') + ' · ' + (t.status || '');
  var b = document.getElementById('task-modal-body'); b.innerHTML = '';
  if ((t.ai_summary || '').trim()) {
    var ai = document.createElement('div'); ai.className = 'task-ai';
    var ah = document.createElement('div'); ah.className = 'task-ai-h'; ah.textContent = '🤖 AI 요약';
    var ab = document.createElement('div'); ab.className = 'task-ai-b'; ab.textContent = t.ai_summary;   /* XSS: AI → textContent */
    ai.appendChild(ah); ai.appendChild(ab); b.appendChild(ai);
  }
  var sec1 = document.createElement('div'); sec1.className = 'task-sec';
  var h1 = document.createElement('div'); h1.className = 'task-sec-h';
  var h1t = document.createElement('span'); h1t.className = 'task-sec-h-t'; h1t.textContent = '업무 설명'; h1.appendChild(h1t);
  var who1 = (t.registrant || t.assignee || '').trim();
  var when1 = t.createdAt ? String(t.createdAt).slice(0, 16).replace('T', ' ') : '';
  var meta1txt = [who1, when1].filter(Boolean).join(' · ');
  if (meta1txt) { var h1m = document.createElement('span'); h1m.className = 'task-sec-meta'; h1m.textContent = meta1txt; h1.appendChild(h1m); }
  sec1.appendChild(h1);
  var bt = (t.body || '').trim();
  if (bt) { sec1.appendChild(renderMarkdown(bt)); }
  else { var n1 = document.createElement('p'); n1.className = 'task-body-line muted'; n1.textContent = '작성된 내용이 없습니다.'; sec1.appendChild(n1); }
  b.appendChild(sec1);
  var cs = t.comments || [];
  var sec2 = document.createElement('div'); sec2.className = 'task-sec';
  var h2 = document.createElement('div'); h2.className = 'task-sec-h'; h2.textContent = '히스토리 (' + cs.length + ')'; sec2.appendChild(h2);
  cs.forEach(function (c) {
    var author = (c.author || '').trim();
    var cm = document.createElement('div'); cm.className = 'task-comment';
    /* 아바타(이름 첫 글자) — 코멘트 간 시각적 구분 */
    var av = document.createElement('span'); av.className = 'task-comment-avatar';
    av.textContent = author ? author.charAt(0) : '·';   /* 기본색 원형 프사 + 성(첫 글자) */
    var main = document.createElement('div'); main.className = 'task-comment-main';
    var ch = document.createElement('div'); ch.className = 'task-comment-head';
    var nm = document.createElement('b'); nm.className = 'task-comment-author'; nm.textContent = author || '익명';
    ch.appendChild(nm);
    if (c.at) {
      var tm = document.createElement('span'); tm.className = 'task-comment-time';
      tm.textContent = String(c.at).slice(0, 16).replace('T', ' ');
      ch.appendChild(tm);
    }
    main.appendChild(ch);
    var bodyEl = renderMarkdown(c.text || ''); bodyEl.classList.add('task-comment-body');
    main.appendChild(bodyEl);
    cm.appendChild(av); cm.appendChild(main);
    sec2.appendChild(cm);
  });
  if (!cs.length) { var n2 = document.createElement('p'); n2.className = 'task-body-line muted'; n2.textContent = '히스토리가 없습니다.'; sec2.appendChild(n2); }
  b.appendChild(sec2);
  ov.hidden = false;
}

function closeTaskModal() { var ov = document.getElementById('task-modal'); if (ov) ov.hidden = true; }

function bindTaskModal() {
  var ov = document.getElementById('task-modal');
  var cb = document.getElementById('task-modal-close');
  if (cb) cb.addEventListener('click', closeTaskModal);
  if (ov) ov.addEventListener('click', function (ev) { if (ev.target === ov) closeTaskModal(); });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') closeTaskModal(); });
}

var ROUTES = ['dashboard', 'insights', 'alarms', 'uptime', 'tasks', 'weekly', 'monthly', 'calendar', 'host', 'cdn', 'database'];
var VIEW_META = {
  dashboard: { eyebrow: '개요', title: '대시보드' },
  insights:  { eyebrow: 'AWS', title: '운영 인사이트' },
  alarms:    { eyebrow: '모니터링', title: '알람' },
  uptime:    { eyebrow: '모니터링', title: '가동률·응답시간' },
  tasks:     { eyebrow: '업무 · Dooray', title: '업무 현황' },
  weekly:    { eyebrow: '업무 · Dooray', title: '주간 보고' },
  monthly:   { eyebrow: '업무 · Dooray', title: '월간 리포트' },
  calendar:  { eyebrow: '일정 · Google Calendar', title: '본부 일정' },
  host:      { eyebrow: '인프라', title: 'EC2 인스턴스' },
  cdn:       { eyebrow: '인프라', title: 'CloudFront CDN' },
  database:  { eyebrow: '데이터베이스', title: 'DB 성능' }
};
/* 각 라우트가 보유한 차트 key(뷰 떠날 때 destroy 대상) */
var ROUTE_CHARTS = {
  uptime: ['uptimeMs'],
  tasks: ['weekStatus'],   /* 업무 현황의 상태 도넛 */
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
  if (route === 'dashboard') return loadDashboard();
  if (route === 'insights') return loadInsights();
  if (route === 'alarms') return loadAlarms();
  if (route === 'uptime') return loadUptime();
  if (route === 'tasks') return loadTasks();
  if (route === 'weekly') return loadWeekly();
  if (route === 'monthly') return loadMonthly();
  if (route === 'calendar') return loadCalendar();
  if (route === 'host') return loadHost();
  if (route === 'cdn') return loadCdn();
  if (route === 'database') return loadDb();
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
  /* AWS 알람 상태 배지는 업무(업무 현황·주간보고·월간) 화면에선 무의미 → 숨김 */
  var sp = document.getElementById('status-pill');
  if (sp) sp.hidden = (route === 'weekly' || route === 'tasks' || route === 'monthly' || route === 'calendar');

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
/* 새로고침 시 현재 뷰에 로딩 표시(카드는 스피너, 개요는 AI/요약 갱신중 표기) */
function setViewLoading(route) {
  var panelId = {
    alarms: 'panel-alarms', uptime: 'panel-uptime', host: 'panel-host',
    cdn: 'panel-cdn', database: 'panel-db', insights: 'panel-insights', weekly: 'panel-weekly'
  }[route];
  if (panelId) {
    var card = document.getElementById(panelId);
    var bd = card && card.querySelector('.card-body');
    if (bd) setState(bd, 'loading');
  }
  if (route === 'dashboard') {
    setText('dash-insight-text', 'AI 분석 다시 실행 중…');
    ['sum-alarms-body', 'sum-uptime-body', 'sum-db-body', 'sum-host-body', 'sum-cdn-body', 'sum-insights-body']
      .forEach(function (id) { sumSet(id, null, '', '갱신 중…'); });
    ['trend-ec2-spark', 'trend-db-spark', 'trend-cdn-spark'].forEach(function (id) {
      var e = document.getElementById(id); if (e) e.innerHTML = '';
    });
  }
  if (route === 'insights') {
    var aiBody = document.getElementById('ins-ai-body');
    if (aiBody) aiBody.textContent = 'AI 분석 다시 실행 중…';
  }
}

var _refreshing = false;
function manualRefresh() {
  if (_refreshing) return;
  _refreshing = true;
  var btn = document.getElementById('refresh-btn');
  if (btn) { btn.classList.add('spinning'); btn.disabled = true; }
  var r = currentRoute || routeFromHash();
  setViewLoading(r);
  loadMeta();
  if (r !== 'alarms' && r !== 'dashboard') loadAlarms();   /* 상태배지 갱신(개요는 자체 로드) */
  /* 업무 현황·주간 보고는 Dooray 에서 실제 재수집(스냅샷이 하루 1회라 화면만 갱신되던 문제 해결).
     재수집은 수 초~수십 초 → 완료 후 뷰를 재조회한다. 실패해도 뷰 재조회는 진행. */
  var pre = (r === 'tasks' || r === 'weekly')
    ? fetch('/api/dooray/refresh', { method: 'POST', credentials: 'same-origin' })
        .then(function (resp) { handle401(resp); })   /* 만료 시 로그인 이동(영구 무반응 방지) */
        .catch(function () {})
    : Promise.resolve();
  var done = pre.then(function () {
    if (window.__loggingOut) return;   /* 401 리다이렉트 중이면 추가 로드 생략 */
    return loadRoute(r);               /* 활성 뷰 재조회(개요/인사이트는 AI 재생성 포함) */
  });
  var minSpin = new Promise(function (res) { setTimeout(res, 500); });   /* 최소 회전(깜빡임 방지) */
  Promise.all([Promise.resolve(done).catch(function () {}), minSpin]).then(function () {
    if (btn) { btn.classList.remove('spinning'); btn.disabled = false; }
    _refreshing = false;
  });
}

function bindRefresh() {
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  btn.addEventListener('click', manualRefresh);
}

/* ── 로그아웃 ───────────────────────────────────────────
 * 쿠키를 서버에서 만료시키고 로그인 화면으로 이동. 요청 성공/실패와 무관하게
 * (이미 만료됐을 수도 있으므로) 항상 /login 으로 보낸다. */
/* 사이드바 하단에 로그인된 공용 계정(아이디)을 표시한다. */
function loadAccount() {
  fetchJson('/api/auth/me')           /* 공통 헬퍼 — handle401·HTTP 에러 처리 일원화 */
    .then(function (d) {
      if (!d || !d.username) return;
      var name = document.getElementById('account-name');
      if (name) name.textContent = d.username;            /* textContent — XSS 안전 */
      var av = document.getElementById('account-avatar');
      if (av) av.textContent = d.username.charAt(0).toUpperCase();
    })
    .catch(function () {});             /* 미인증/일시 오류 시 계정만 비움(화면 영향 없음) */
}

function bindLogout() {
  var btn = document.getElementById('logout-btn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    btn.disabled = true;
    window.__loggingOut = true;   /* 진행 중 401 리다이렉트 중복 방지 */
    fetch('/api/auth/logout', { method: 'POST' })
      .then(function () { location.replace('/login'); })
      .catch(function () { location.replace('/login'); });
  });
}

/* ── 기간 버튼 바인딩 ──────────────────────────────────── */
/* 현재 기간 버튼은 가동률(uptime) 그룹 1세트만 존재(트래픽 메뉴 폐지). */
function bindPeriodButtons() {
  document.querySelectorAll('.period-btns').forEach(function (group) {
    var kind = group.getAttribute('data-period-group'); /* 'uptime' */
    group.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var p = parseInt(btn.getAttribute('data-period'), 10);
        if (kind === 'uptime') {
          uptimePeriod = p;
          markActive('uptime', p);
          loadUptime();
        }
      });
    });
  });
}

/* 같은 그룹의 모든 버튼 세트에서 active 동기화 */
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
  bindLogout();              /* 로그아웃 버튼 → 쿠키 만료 + /login 이동 */
  loadAccount();             /* 사이드바에 로그인 계정 표시 */
  bindChat();
  bindHostModal();           /* EC2 상세 모달 닫기/배경/ESC */
  bindDbModal();             /* RDS 상세 모달 닫기/배경/ESC */
  bindTaskModal();           /* 업무 상세 모달 닫기/배경/ESC */
  bindWeeklyTools();         /* 주간보고 복사·구성편집 */
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
    /* alarms: 위에서 갱신 / weekly: 하루 1회 갱신이라 폴링 불필요(차트 깜빡임·불필요 호출 방지) */
    else if (r !== 'alarms' && r !== 'weekly' && r !== 'tasks' && r !== 'monthly' && r !== 'calendar') loadRoute(r);
  }, POLL.view);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
