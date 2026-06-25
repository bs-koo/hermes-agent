# StyleSeed — 이 프로젝트 적용 규칙 (PROJECT-RULES)

> 이 레포(`hermes_agent`)의 **모든 UI는 StyleSeed를 따른다.** 이 파일은 *StyleSeed 원본 토큰(`tokens/*.json`) + 이 프로젝트 오버라이드*를 한 장으로 합친 **실무 SSOT**다.
> 상세 판단 근거는 원본 참조: `DESIGN-LANGUAGE.md`(74 규칙) · `VISUAL-CRAFT.md` · `METHODOLOGY.md` · `PAGE-TYPES.md` · `APP-PLAYBOOKS.md` · `UX-WRITING.md`.
> 출처: https://github.com/bitjaru/styleseed · https://styleseed-demo.vercel.app/llms.txt

---

## 0. 이 프로젝트 오버라이드 (단 하나)

- **단일 브랜드 액센트 = 토스블루 `#3182F6`** (hover/pressed `#1B64DA`, tint `rgba(49,130,246,0.10)`).
  StyleSeed 기본 브랜드(보라 `#721FE5`)를 이 값으로 **교체**한다.
- **그 외 모든 토큰·규칙·방법론·UX 라이팅은 StyleSeed 원본 그대로.**

---

## 1. 색 — Color Discipline

원칙(VISUAL-CRAFT / METHODOLOGY §7): **단일 액센트(60-30-10)** · **순수검정 금지** · **상태색은 점+작은 텍스트로만** · **새 색을 만들지 않는다**(~13개 역할 팔레트). 액센트는 화면당 **3회 이내**(희소성).

### 브랜드 (오버라이드 적용)
| 역할 | 값 | 용도 |
|---|---|---|
| `--brand` | `#3182F6` | 단일 액센트 — 주요 CTA·활성·선택·링크 |
| `--brand-dark` | `#1B64DA` | hover/pressed |
| `--brand-tint` | `rgba(49,130,246,0.10)` | 선택된 행/항목 옅은 배경 |

### 표면 (StyleSeed `surface`/`semantic`)
| 역할 | 값 | 용도 |
|---|---|---|
| `--surface-page` | `#FAFAFA` | 페이지 배경(밝은 회색) |
| `--surface-card` | `#FFFFFF` | 카드 |
| `--surface-subtle` | `#FAFAF9` | 리스트 행·보조 카드 |
| `--surface-muted` | `#E8E6E1` | 진행바·비활성 컨트롤 |
| `--input-bg` | `#F3F3F5` | 입력 배경 |
| `--border` | `rgba(0,0,0,0.1)` | 헤어라인 보더 |

### 텍스트 (StyleSeed `text` — 순수검정 금지)
| 역할 | 값 | 용도 |
|---|---|---|
| `--text-strong` | `#2A2A2A` | 가장 강한 텍스트(`#030213`/순수검정 대신) |
| `--text-primary` | `#3C3C3C` | 제목·지표 숫자 |
| `--text-secondary` | `#6A6A6A` | 라벨·설명 |
| `--text-tertiary` | `#7A7A7A` | 보조·축 라벨 |
| `--text-disabled` | `#9B9B9B` | 비활성 |
| `--icon` | `#4A5568` | 기본 아이콘(blue-gray) |

### 상태색 (StyleSeed `semantic` — **점+작은 텍스트로만**)
| 역할 | 값 |
|---|---|
| `--success` | `#6B9B7A` |
| `--destructive` | `#D4183D` |
| `--warning` | `#D97706` |
| `--info` | `#3B82F6` |
| `--alert-badge` | `#FF4444` |

> 대시보드 의미 매핑: 정상=success / 위험·경보=destructive / 주의=warning / 정보=info.
> **카드 전체를 상태색으로 칠하지 말 것** — 6px 점 + 색 텍스트가 기본.

---

## 2. 타이포그래피 (`tokens/typography.json`)

- **폰트:** `Pretendard, Inter, -apple-system, system-ui, sans-serif` / mono: `JetBrains Mono, Fira Code, monospace`.
- **본문 베이스 14px.** 스케일(px): 2xs10 · xs11 · sm12 · caption13 · base14 · body15 · md16 · subhead17 · lg18 · xl20 · 2xl24 · 3xl30 · 4xl36 · 5xl48.
- **굵기:** 400 / 500 / 600 / 700.
- **행간:** display(36–48) 1.0~1.2 · heading(18–24) 1.35 · body(14–17) 1.5 · caption(10–13) 1.5~1.65.
- **자간:** display `-0.02em`(타이트) · heading `-0.01em` · body `0` · 대문자 캡션 `+0.05~0.1em`.
- **큰 숫자가 주인공:** 핵심 지표는 크고 굵게 + `tabular-nums`(자리수 고정).

---

## 3. 간격 (`tokens/spacing.json` — 6px 베이스)

스케일(px): 6·12·18·24·30·36·48·60·72. 권장 용도:
- 페이지 좌우 `24px` · 섹션 간 `24px` · 카드 패딩 `24px` · 그리드 갭 `16px` · 요소 갭 `12px` · 아이콘 갭 `8px`.
- **넉넉한 여백**으로 위계를 만든다(빽빽함 금지).

## 4. 라운드 (`tokens/radii.json` — base 0.625rem=10px)

- 버튼·입력 `8px` · **카드 `16px`(rounded-2xl)** · 뱃지/아이콘컨테이너 `10px` · 아바타·pill `full`.

## 5. 그림자 (`tokens/shadows.json` — ≤8% 불투명도)

```
--shadow-card:     0 1px 3px rgba(0,0,0,0.04)
--shadow-button:   0 1px 3px rgba(0,0,0,0.06)
--shadow-cardHover:0 2px 4px rgba(0,0,0,0.08)
--shadow-elevated: 0 4px 12px rgba(0,0,0,0.08)
--shadow-modal:    0 8px 24px rgba(0,0,0,0.12)
```
표면은 **그림자/톤으로 분리**(굵은 보더 금지). 플랫 표면 + 헤어라인.

## 6. 모션 (`tokens/motion.json`)

- **지속:** fast `100ms` · normal `200ms` · slow `350ms`.
- **이징:** default `cubic-bezier(.4,0,.2,1)` · out `(0,0,.2,1)` · in `(.4,0,1,1)` · spring `(.34,1.56,.64,1)`.
- **용도:** hover=fast+default · enter=normal+out · exit=fast+in · 놀이감=slow+spring.
- **시드(이 프로젝트 기본 = Toss → Spring):** Spring(통통·CTA) / Silk(대시보드·모달) / Snap(키보드·커맨드) / Float(마케팅) / Pulse(라이브 인디케이터).
- **`prefers-reduced-motion: reduce`** 시 애니메이션 비활성/축약 필수. 패럴랙스·무한루프·스크롤 연동 타임라인 금지.

---

## 7. 레이아웃 · 정보구조 (METHODOLOGY)

- **Progressive disclosure:** 상단 above-the-fold = 핵심 KPI **4–6개** + 한 줄 브리핑. 나머지는 한 번의 클릭 뒤로.
- **2단 밀도:** 요약(숫자+캡션) → 클릭 시 상세 차트/표. 표의 모든 행은 상세로 드릴다운.
- **사이드바 240–280px**로 세로 공간 절약(필요 시).
- **품질 승수:** 스피너 대신 **레이아웃을 닮은 스켈레톤** · 빈 상태엔 **CTA 1개 + 안내**(빈 박스/"데이터 없음" 금지) · 일관된 마이크로인터랙션.
- **원자적 컴포넌트**(atoms→molecules→organisms)로 반복 UI 추출.

## 8. UX 라이팅 (UX-WRITING — 한국어 체크리스트)

- **버튼 = 동작 명명:** "확인/제출/OK" ✗ → **"~하기"**("가입하기", "2,400원 보내기"). 내비게이션만 "확인/다음".
- **두 버튼:** 파괴적 액션에 "확인/취소" ✗ → "삭제/유지", "닫기/다음에"(부정어 줄이기).
- **에러:** 비난·전문용어 없이 **"무엇이 일어났나 + 어떻게 고치나"**. "잘못된 입력" ✗ → "이미 가입된 이메일이에요 — 로그인해 보세요". 필드 옆에 표시. 못 고치는 실패엔 재시도 제공.
- **빈/로딩/성공:** 빈 상태는 설명+초대. 느린 로딩은 무슨 일인지. 성공은 구체적으로("2,400원 보냈어요").
- **명료·간결:** 잡초 뽑기("이미 보유하고 계신"→"보유 중인"), 한 문장 한 메시지, 핵심어 앞에, 시스템 말투 금지.
- **관점·말투:** "고객님의 계좌"→"내 계좌". 해요체/합쇼체 섞지 말고 하나로. 돈·에러엔 느낌표·농담 금지.

## 9. CSS 변수 스니펫 (복붙용 — 토스블루 액센트 반영)

```css
:root {
  /* 브랜드(오버라이드) */
  --brand:#3182F6; --brand-dark:#1B64DA; --brand-tint:rgba(49,130,246,.10);
  /* 표면 */
  --surface-page:#FAFAFA; --surface-card:#FFFFFF; --surface-subtle:#FAFAF9;
  --surface-muted:#E8E6E1; --input-bg:#F3F3F5; --border:rgba(0,0,0,.1);
  /* 텍스트(순수검정 금지) */
  --text-strong:#2A2A2A; --text-primary:#3C3C3C; --text-secondary:#6A6A6A;
  --text-tertiary:#7A7A7A; --text-disabled:#9B9B9B; --icon:#4A5568;
  /* 상태(점+텍스트로만) */
  --success:#6B9B7A; --destructive:#D4183D; --warning:#D97706; --info:#3B82F6;
  /* 그림자(≤8%) */
  --shadow-card:0 1px 3px rgba(0,0,0,.04); --shadow-cardHover:0 2px 4px rgba(0,0,0,.08);
  --shadow-elevated:0 4px 12px rgba(0,0,0,.08); --shadow-modal:0 8px 24px rgba(0,0,0,.12);
  /* 라운드 */
  --r-btn:8px; --r-card:16px; --r-badge:10px; --r-full:9999px;
  /* 모션 */
  --dur-fast:100ms; --dur-normal:200ms; --dur-slow:350ms;
  --ease:cubic-bezier(.4,0,.2,1); --ease-spring:cubic-bezier(.34,1.56,.64,1);
  /* 타이포 */
  --font:Pretendard,Inter,-apple-system,system-ui,sans-serif;
}
@media (prefers-reduced-motion: reduce){ *{animation:none!important;transition:none!important} }
```

## 10. 안티패턴 (StyleSeed가 잡아내는 "AI 티")

- 다중 액센트 / 액센트 남발(화면당 30×) → ✗ (단일, 3× 이내)
- 상태색으로 카드 전체 칠하기 → ✗ (점+텍스트)
- 배경 그라데이션(카드·헤더) → ✗ (플랫, CTA에만 그라데이션 허용)
- 순수 검정(#000) on 흰색 → ✗ (#2A2A2A on #FAFAFA)
- 회색 위계 없음(전부 같은 진회색) → ✗ (최소 3단계)
- 스피너만 / 빈 박스 / "데이터 없음" → ✗ (스켈레톤 · CTA 있는 빈 상태)
- 버튼 "제출/확인", 에러 "잘못된 입력" → ✗ (동작 명명 · 친절한 에러)
