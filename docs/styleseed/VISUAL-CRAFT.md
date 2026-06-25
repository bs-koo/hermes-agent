# Visual Craft — research-backed rules for beautiful, coherent UI

DESIGN-LANGUAGE.md tells you the **rules**. METHODOLOGY.md tells you **why** they
exist. This file tells you the **craft** — the concrete, numeric decisions that
separate "designed by one mind" from "assembled from parts": how to make a
component look intentional, how to space it, which type to pick for which kind of
app, and — most importantly — how to keep the *whole* UI **coherent**.

Every rule here is grounded in primary design literature (sources at the bottom).
Numbers are defaults, not dogma — but don't deviate without a reason.

> **Read this:** before scaffolding a new product surface, and whenever a UI
> "looks off" but you can't say why. Pair with **APP-PLAYBOOKS.md** (domain bias)
> and **PAGE-TYPES.md** (screen bias).
>
> **Priority:** The 11 Golden Rules (CLAUDE.md) and DESIGN-LANGUAGE.md win every
> conflict. This file refines taste *within* those rules; it never overrides them.

---

## §C0 — The Coherence Laws (read this first)

The single biggest reason AI-generated UI looks "off" isn't ugly components — it's
**incoherence**: a sharp-cornered dialog next to pill-shaped buttons, two competing
accent colors, icons from three families, shadows lit from different directions. A
UI reads as professional when it feels **shaped by one deliberate mind**
([UX Collective], [Tubik]).

**The meta-law: for each design axis below, choose exactly ONE value or family, encode
it as a token, and apply it everywhere.** Coherence is not "every screen is identical"
— it's that the same decisions repeat. The user's instinct — *"if the corners are
sharp, everything must be sharp"* — is exactly right, and it generalizes to every row
of this table.

| Axis | Pick ONE, system-wide | Failure mode when mixed |
|---|---|---|
| **Corner / radius** | One personality: **sharp 0–4px** · **soft 8–12px** · **pill 9999px**. Card, button, input, modal, image, avatar all obey it. | Sharp dialog + rounded buttons = "two products glued together." The #1 tell of un-designed UI. |
| **Shadow** | One scale, one light source (**above-left**), one hue tint. A modal and a card use the *same* family, different tiers. | Mixed light directions / some-black-some-tinted = "scene with two suns." |
| **Accent color** | **One** primary accent for interactive emphasis (+ semantic red/green/amber). One CTA color across the whole app. | Multiple accents → nothing reads as "the" action; hierarchy collapses. |
| **Spacing unit** | One base grid: **8px** (4px allowed as a half-step for icon↔label). Every margin/padding/gap is a multiple. | Off-grid values (7, 13, 19px) read as "sloppy" without users knowing why. |
| **Icon style** | One family, one fill mode (all outline **or** all filled), one stroke weight (e.g. **2px @ 24px**). | Mixing Material + Feather + emoji, or 1.5px and 2px strokes, looks "out of place." |
| **Type scale** | One modular scale, ≤2 font families, one weight ramp. | Arbitrary sizes destroy rhythm; >2 families looks amateur. |
| **Motion / easing** | One duration set (~150/200/300ms) + one easing family; same enter/exit logic everywhere. | Some snappy, some sluggish = feels like different apps. |
| **Border** | One hairline weight (**1px**) and one low-contrast neutral border token. | Mixed 1px/2px borders and random grays look unintentional. |
| **State layers** | One opacity ramp for hover/focus/pressed on *all* interactive elements. | Buttons darken, links underline, cards scale — feedback feels random. |
| **Control height** | Buttons, inputs, selects share a height set (e.g. **40px** default). | A 44px input next to a 32px button breaks the baseline. |

**Treat a mixed axis as a lint error, not a style choice.** When in doubt, copy the
decision already made elsewhere in the product rather than inventing a new one.

---

## §C1 — Spacing & rhythm

**CR-1 · Snap everything to one scale.** Use `{2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96}`px.
No arbitrary values (no 13px, no 7px). 8px base; 4px only as a half-step for
icon↔text and tightly stacked small text. *Why: a constrained scale forces
deliberate, repeatable layout; 8px divides cleanly across 1x/1.5x/2x/3x densities.*
([Refactoring UI], [Material 3 Spacing], [IBM Carbon 2x Grid])

**CR-2 · Proximity = relatedness.** The space *around* a group must be **≥ 2×** the
space *within* it. Uniform spacing everywhere is the #1 beginner failure — it
destroys all grouping signal. *Why: Gestalt — the eye reads tight items as one unit,
loose items as separate.* ([Refactoring UI "Avoid ambiguous spacing"])

**CR-3 · The form spacing ladder.** label→input **4–8px** · field→field **12–16px** ·
section→section **24–32px** mobile / **32–48px** desktop. Each tier ~doubles the last,
so hierarchy is unambiguous. ([Designary], [Atlassian Spacing])

**CR-4 · Card padding by size.** compact **12–16px** · standard **16–24px** · large/marketing
**24–32px** · hero **48–64px+**. And the outer margin between cards must be **≥** the
card's inner padding, so each card reads as self-contained. ([Refactoring UI], [UX Lab])

**CR-5 · Start over-spaced, then reduce.** Designers under-space by default; "ample"
usually turns out to be "just enough." When unsure, go one step *up* the scale.
([Refactoring UI "Start with too much white space"])

**CR-6 · Separate with the lightest tool first:** whitespace → background tint →
shadow → border (last resort). Don't put a border on everything. *Why: borders
everywhere read as busy/cluttered; space and tone separate without noise.* ([Refactoring UI])

**CR-7 · Measure (line length) 50–75 characters**, ~66 ideal; `max-width: 65ch` (≈680–720px).
Never exceed 80ch — wider copy gets skipped ~41% more (Baymard) and breaks the
return sweep. Dashboards go edge-to-edge, but any text column inside still obeys 75ch.
([Baymard Line Length], [Butterick])

**CR-8 · Data-table density tiers.** row height **32–40px** compact · **48px** default ·
**56px** spacious. Default to 48px and expose a density toggle for power users. Each
density step changes control height by exactly **4px** (Material density 0 / −1 / −2 / −3).
([Pencil&Paper], [Material Density])

**CR-9 · Don't enforce a strict pixel baseline grid on responsive web** — it assumes
fixed heights it doesn't have. Repetition of the spacing scale creates rhythm; a
baseline grid does not. ([Imperavi Vertical Rhythm])

**CR-10 · Everything lines up to something.** Establish a small set of shared
left/right edges (a 12-col grid, 16px gutters) and align all content to them. Apply
*optical* (not pixel) alignment for weighted glyphs: nudge arrow/play icons, trim
the icon-side padding of icon-buttons, center type by cap-height. ([IBM Carbon], [Liferay Optical Alignment])

---

## §C2 — Typography

**CT-1 · Base body = 16px;** build a 5–7 step modular scale, e.g. `12 · 14 · 16 · 20 · 24 · 32 · 48`.
*Why: a shared multiplier makes sizes harmonize like musical intervals; 16px is the
WCAG-practical body minimum.* ([Material 3 Type], [FontFYI])

**CT-2 · Ratio by density.** dense product UI/dashboards **1.125–1.2** · general default **1.25** ·
content/editorial **1.333** · marketing/display **1.5–1.618**. Run *two* scales in one
system if needed (tight UI scale + loose editorial scale). ([Cieden Type Scales])

**CT-3 · Max 2 typefaces** — or one superfamily (e.g. IBM Plex Sans/Serif/Mono). Pair
by **contrast**: geometric/grotesque heading + humanist body. Reach for a serif display
only to signal **editorial / trust / luxury**. ([EightShapes], [Pangram Pangram])

**CT-4 · Tabular numerals are mandatory** (`font-variant-numeric: tabular-nums`) for
tables, money, dashboards, timers — applied to the *whole* numeric grid, never
selectively. Proportional figures inline in prose; lining figures in UI. Pick a font
with disambiguated `0/O` and `1/l/I` for anything numeric or financial.
([MyFonts Figures], [TypeType Numerals])

**CT-5 · Build hierarchy with weight, not just size.** 400 body · 500–600 labels/subheads ·
700 headings. Ship 3 weights (add 800–900 for marketing display only). **Never go
below 400** for body/UI — de-emphasize with color or size instead. ([Refactoring UI], [Fontfabric Weight])

**CT-6 · Line-height tightens as type grows.** body **1.5** (long-form 1.5–1.7) ·
headings 1.2–1.35 · display/hero **1.0–1.2** · captions 1.4–1.6. *Why: big type already
reads as a shape and needs less leading; small text needs more air.* ([Butterick], [Material 3 Type])

**CT-7 · Letter-spacing.** display **−0.01 to −0.03em** (never past −0.03) · UPPERCASE
labels **+0.05 to +0.12em** · body **0** (leave the font's default alone). Express in
`em` so it scales with size. ([Butterick], [DesignYourWay])

**CT-8 · Optical sizing.** Text-optical faces ≤19px, Display-optical ≥20px (the SF
Text ≤19pt / SF Display ≥20pt split). ([Apple HIG Typography])

### Type recipe by app type

Use this as the **type half** of the domain bias in APP-PLAYBOOKS.md.

| App type | Typeface character | Hero | Body | Numerals | Weights | Signature |
|---|---|---|---|---|---|---|
| **Fintech** | Restrained neo-grotesque, low contrast (Inter, IBM Plex, Söhne) | 36–48 | 15–16 | **Tabular lining (mandatory)** | 400 / 500 / 600–700 KPIs | Aligned money columns, disambiguated 0/O |
| **SaaS dashboard** | Humanist screen-sans (Inter) | 28–36 | 14 (13 dense) | Tabular in tables | 400 / 500 / 600, scale 1.2 | Weight-driven hierarchy, tight density |
| **E-commerce** | Friendly humanist + optional brand display | 32–48 | 16 | Tabular for price/grid | 400 / 600 price+CTA | Bold confident price, warm body |
| **Social** | System humanist (SF Pro / Roboto) | weight-up ~20–28 | 15–17 | Proportional | 400 / 600 | Bolder-not-bigger titles, native feel |
| **Content / editorial** | High-contrast serif display + humanist body | 40–64 | 18–21 | Oldstyle/proportional in prose | 400 / 700 | Serif authority, 60–75ch measure |
| **Health** | Warm humanist sans, open counters | 32–44 | 16–18 | Proportional (tabular for vitals) | 400 / 600 | Generous air, line-height 1.6 |
| **Dev tools** | Grotesque + matched mono superfamily (Geist, Plex) | 32–48 | 14–16 (code 13–14 mono) | Tabular / mono | 400 / 500 / 600 | Mono for code/logs, Swiss precision |
| **Marketing** | Expressive oversized display + clean sans | 64–120+ | 18 | Lining proportional | 400 / 700–900 | Hero dominates, loose 1.5–1.618 scale |

---

## §C3 — Component craft

### Depth & elevation
**CC-1 · Layered shadows, never one hard shadow.** Stack 3–6 `box-shadow`s with
increasing offset/blur and decreasing opacity (8–20% per layer, lower at higher
elevation). One light source, **above and slightly left**, vertical offset ≈ **2×**
horizontal — every shadow on the page shares it. **Tint the shadow toward the surface
hue**, never pure `rgba(0,0,0)`. *Why: real penumbra is a gradient; one black shadow
looks like a cutout sticker.* ([Josh Comeau Shadows], [Tobias Ahlin])

**CC-2 · Two shadow intents:** small/tight = element sits *near* the page (buttons,
inputs); large/blurry = element *floats toward* the user (modals, popovers, dragged
cards). Shadow size encodes z-distance. In dark mode, prefer **tonal elevation**
(lighter surface) over shadow, which nearly disappears on dark. ([Refactoring UI], [Material 3 Elevation])

### Border radius
**CC-3 · Radius is a token,** not a magic number. Define a scale (`4 / 8 / 12 / 16 / full`)
and pick **one personality** (§C0). Bigger components get bigger radius proportionally.

**CC-4 · The nested-radius law:** `inner radius = outer radius − padding`. Implement
with `calc()`, clamp ≥0. *Why: concentric corners must share a center, or the inner
corner "bulges" past the outer arc.* Apple's Liquid Glass formalizes this. ([Cloud Four], [Material 3 Shape])

### Buttons
**CC-5 · Heights 36 / 40 / 44–48px;** touch target ≥ **44px** (iOS) / **48dp** (Android).
Horizontal padding ≈ **2× vertical**, on grid. Icon+label gap **8px** (4px dense). ([Justinmind], [Apple HIG])

**CC-6 · One primary button per view.** Hierarchy by **contrast** (filled → outline →
ghost), never by size. Destructive is red but **never** more prominent than the
primary. All buttons in a row share one height. ([Cieden Button Hierarchy], [Carbon])

### Cards
**CC-7 · Padding 16 / 24 / 32px** equal on all sides; radius obeys the system
personality and the nested law for inner media/buttons. **Border XOR shadow, never
both** — flat/dense UI uses a 1px border (elevation 0); standalone/floating uses a soft
shadow (elevation 1). Hover on an interactive card = raise **one** elevation tier over
~200ms, not a scale jump. ([Material 3 Elevation], [Refactoring UI])

### Inputs & forms
**CC-8 · Input height = button height** (default 40px; 44–48 touch). Label **above** the
field (top-aligned scans fastest); **labels never vanish** (no placeholder-as-label).
Focus ring **≥2px, 3:1 contrast**, visible on light *and* dark (WCAG 2.2). Errors
**never color alone**: red border + icon + a message saying what's wrong and how to fix
it. Rhythm: 8px label→field, 4px field→helper, 16–24px between fields. ([UX Collective Text Fields], [WCAG 2.2])

### Icons
**CC-9 · One family, one fill mode, one stroke weight** (typically **2px @ 24px**).
Optically size and center (tall/wide glyphs get nudged), align to text via
`currentColor` at ~1em–1.25em. Semantics: **outline = default/inactive, filled =
active/selected** — and if you use both, make filled optically lighter so weights
match. ([Material 3 Icons], [Dutchicon])

### States & micro-detail
**CC-10 · One state-layer ramp everywhere** (Material 3 canonical: hover **8%**, focus
**10%**, pressed **10%**, dragged **16%**; disabled = 38% content / 12% container) — a
translucent overlay of the on-color, one state at a time. Transitions **150–200ms**
for hover/press, ~300ms for larger moves, one shared easing; animate transform &
opacity, not layout. *"Polished" = layered low-opacity shadow + crisp focus ring +
real hover/active/disabled states + on-grid spacing. "Flat/unfinished" = one hard
shadow, no focus state, color-only errors, off-grid spacing.* ([Material 3 States])

---

## §C4 — Color & dark mode

**CL-1 · Build every color as a ramp,** 9–11 steps (`50 → 950`), **500 = base**. Author
in **HSL / OKLCH / LCH, never raw hex** — and **raise chroma at the extremes** (L<20%,
L>90%) to fight wash-out. *Why: hue/saturation/lightness map to human vision; hex
doesn't.* ([Refactoring UI Color], [UX Bootcamp HSL])

**CL-2 · One accent + a full grey ramp.** Greys (8–10 steps) carry text/surfaces/borders.
Add only **4 semantic hues** — success/warning/error/info — used strictly by meaning,
never decoration. Prefer role-named **semantic tokens** (`primary`, `surface`, `error`)
so themes swap automatically. ([Refactoring UI], [Apple HIG Color])

**CL-3 · Tint your greys.** Give the neutral ramp a **5–15% tint** toward the brand hue
(or a deliberate warm/cool bias), consistent across all steps. **No pure `#000` text** —
use the 900/950 neutral. *Why: pure grey looks lifeless and clashes with the accent.*
([Refactoring UI neutrals])

**CL-4 · Contrast floors (WCAG 2.2 AA):** body **≥4.5:1**, large text (≥24px / ≥18.66px
bold) **≥3:1**, UI components & graphics **≥3:1**. AAA = 7:1 / 4.5:1. **Never convey
info by color alone** — pair with text/icon/shape. ([WCAG 2.2], [WebAIM])

**CL-5 · Dark mode is not an invert.** Base = **#121212** (not `#000`); raise elevation
with white overlays (~5% @1dp, 8% @2dp, 12% @8dp, 16% @24dp). **Desaturate accents
~25%** (Material: primary tone 40→80). Re-derive each role from the tonal palette.
([Material Dark Theme], [Material 3 Color])

---

## §C5 — Data visualization

**CD-1 · Three palette families:** **categorical** (distinct hues, unordered — cap usable
at **~6–8**; beyond that, group "Other" or use small multiples) · **sequential** (one hue,
light→dark = low→high) · **diverging** (two hues meeting at a neutral midpoint, for ± around
a reference). ([IBM Carbon Data-viz], [ColorBrewer])

**CD-2 · Choose the chart from the data question** (FT Visual Vocabulary): deviation,
correlation, ranking, distribution, change-over-time, part-to-whole, magnitude,
spatial, flow. Don't use a pie for a trend. ([FT Visual Vocabulary])

**CD-3 · Maximize data-ink / cut chartjunk:** kill 3D, gradients, shadows, heavy
gridlines, borders. **Direct-label instead of legends** (also helps color-blind users
and cuts eye travel). ([Tufte data-ink], [NN/g Clutter])

**CD-4 · Number craft.** Tabular numerals, **right-aligned** numbers (decimals stack),
left-aligned text labels. A single KPI = **one big bold number + ▲/▼ delta + inline
sparkline**; a big number beats a chart when the answer is one value. KPIs 4-per-row,
≤2 rows. Series **≥3:1 vs background**; colorblind-safe; always offer **"View as data
table."** ([Five Rules for Tables], [Tufte sparklines])

---

## §C6 — 2025–2026 trends: durable vs fad

**Durable** (adopt): design tokens for every value · **bento grids** (mixed-size cards,
8pt gaps) for overview/dashboard surfaces · **big type** as a hierarchy tool · matured,
*subtle* glassmorphism (Apple Liquid Glass — translucency + real-time lensing, not
heavy blur) · **soft layered shadows** · **curated information density** (Linear/Notion/
Stripe — pack more per screen but let hierarchy carry it) · transparent, controllable
**AI UX**.

**Fad / use sparingly** (brand-expression only): neumorphism (fails contrast) ·
neubrutalism (poor for dense/utility UI) · kinetic/morphing type in product chrome ·
hiding info behind clicks in the name of minimalism · low-contrast "calm" palettes
that drop below the 4.5:1 floor.

**AI-product UX specifics:** stream output with explicit **listening → thinking →
doing** states + a **Stop** button; place **citations inline next to the claim** with
meaningful labels; use **neutral, non-anthropomorphic** copy + a "verify outputs"
disclaimer near the input; never present step-by-step "reasoning" as ground truth.
([NN/g Explainable AI], [Envato Trends], [MyDesigner Density])

---

## Sources

Refactoring UI (Wathan & Schoger) · Material Design 3 (type / spacing / elevation /
shape / color / state layers / dark theme) · Apple Human Interface Guidelines
(typography / color / layout / materials) · IBM Carbon (2x grid / spacing / data-viz
palettes) · WCAG 2.2 (W3C) & WebAIM (contrast) · Butterick's *Practical Typography* ·
FT Visual Vocabulary & Edward Tufte (data-ink, sparklines) · Nielsen Norman Group
(chart clutter, explainable AI) · Josh W. Comeau & Tobias Ahlin (CSS shadows) ·
Cloud Four (nested radius) · Baymard Institute (line length) · Atlassian Design,
Shopify Polaris, EightShapes, Cieden, Designary (spacing & type systems).

*Numbers are sourced defaults; treat the structural laws (one-choice-per-axis,
layered shadows, nested radius, proximity, contrast floors) as load-bearing and the
exact figures as sensible starting points.*
