# hermes_agent — 프로젝트 지침

## UI 디자인 표준: StyleSeed (필수)

이 레포에서 만드는 **모든 UI**(운영 대시보드·페이지·컴포넌트·알림/이메일 등)는 **StyleSeed 디자인 시스템**을 따른다. UI를 새로 만들거나 고치기 전에 아래를 먼저 확인한다.

- **실무 SSOT(먼저 읽기):** `docs/styleseed/PROJECT-RULES.md` — StyleSeed 토큰 + 이 프로젝트 오버라이드를 한 장으로 정리.
- **원본 규칙:** `docs/styleseed/` — `DESIGN-LANGUAGE.md`(74 규칙) · `VISUAL-CRAFT.md` · `METHODOLOGY.md` · `PAGE-TYPES.md` · `APP-PLAYBOOKS.md` · `UX-WRITING.md` · `tokens/*.json`.
- **단일 브랜드 액센트 = 토스블루 `#3182F6`** (hover/pressed `#1B64DA`). StyleSeed 기본 보라(`#721FE5`)를 이 값으로 오버라이드. **나머지 토큰(중립·텍스트·상태·간격·라운드·그림자·모션)은 StyleSeed 값을 그대로 사용.**

### 깨면 안 되는 핵심 규율
- **단일 액센트 원칙:** 브랜드색은 화면당 ~3회(희소성). 상태색(success/destructive/warning/info)은 **점(dot)+작은 텍스트** 스케일로만 — 카드 전체를 빨갛게 칠하지 않는다.
- **순수 검정 금지:** `#FAFAFA` 배경 위 `#2A2A2A`급 텍스트. 회색 위계 최소 3단계.
- **플랫 표면:** 배경 그라데이션 금지(액센트 CTA에만 허용). 그림자 ≤8% 불투명도 + 헤어라인 보더.
- **간격 6px 베이스 · 카드 라운드 16px · 본문 14px.**
- **Progressive disclosure:** 상단 핵심 KPI 4–6개 + 드릴다운. 스피너 대신 **스켈레톤**. 빈 상태엔 **CTA 1개**.
- **UX 라이팅:** 버튼은 동작을 명명("확인/제출" ✗ → "2,400원 보내기/가입하기" ○). 에러는 비난 없이 "무엇+해결책". 한 개념 한 용어. 잡초 뽑기(군더더기 제거). 토스 8대 라이팅 원칙(`docs/styleseed/UX-WRITING.md §W8`).
- **모션:** `prefers-reduced-motion` 존중. Toss 기본 모션 시드 = **Spring**(fast 100/normal 200/slow 350ms).

> 위 규율에서 벗어나야 할 합당한 이유가 있으면 먼저 사용자에게 확인한다.
