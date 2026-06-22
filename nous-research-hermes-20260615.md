# Nous Research Hermes 리서치 리포트

> 조사일: 2026-06-15 | 모드: 꼼꼼 | 소스: 11개 참조

## 요약

"Hermes(헤르메스)"는 AI 랩 **Nous Research**가 만든 이름으로, 실제로는 **두 가지 다른 대상**을 가리킨다.
(1) **Hermes 모델 계열** — Meta Llama 3.1을 파인튜닝한 오픈웨이트 LLM(Hermes 3 등),
(2) **Hermes Agent** — 그 위에서 동작하는 별도의 **자율 에이전트 제품**("self-improving AI agent").
따라서 "헤르메스 에이전트"는 맥락에 따라 ① Hermes 모델의 에이전트적 활용(함수 호출·툴 사용)을 뜻하거나, ② 제품으로서의 *Hermes Agent*를 직접 가리킨다. 두 개념은 같은 랩에서 나왔지만 **모델 ≠ 에이전트 제품**으로 구분된다.

## 주요 발견

- **Hermes 3 (모델)**: Llama 3.1을 **8B / 70B / 405B** 세 크기로 풀(full-parameter) 파인튜닝한 오픈웨이트 LLM. 2024년 8월 공개. "neutrally-aligned generalist instruct and tool use model"로 소개됨. ([공식](https://nousresearch.com/hermes3), [기술보고서 2408.11857](https://arxiv.org/abs/2408.11857))
- **405B의 의의**: Hermes 3 405B는 **Llama 3.1 405B를 처음으로 공개적으로 풀파라미터 파인튜닝**한 모델로, 지시 이행·툴 사용 신뢰성에 대한 통제력을 확보했다고 평가됨. ([Lambda](https://lambda.ai/blog/unveiling-hermes-3-the-first-fine-tuned-llama-3.1-405b-model-is-on-lambdas-cloud))
- **에이전트적 핵심 역량**: 시스템 프롬프트 정밀 준수, 온디맨드 **유효 JSON 생성**, **구조화된 XML 형식의 함수 호출**, 긴 컨텍스트(128K) 유지, 멀티턴·롤플레이·내적 독백. → 파이프라인/에이전트에 바로 투입 가능. ([HF 모델카드](https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B))
- **함수 호출 전용 자산**: `hermes-function-calling` 데이터셋·리포지토리로 function-calling / json-mode / agentic json-mode / structured extraction을 학습. ([데이터셋](https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1), [repo](https://github.com/NousResearch/Hermes-Function-Calling))
- **Hermes Agent (제품)**: "The self-improving AI agent built by Nous Research", **내장 학습 루프(built-in learning loop)**를 가진 유일한 에이전트라고 표방. 경험에서 스킬을 만들고 개선, 세션 간 영속 메모리, 200+ 모델 지원(OpenRouter·Nous Portal·OpenAI), 멀티플랫폼(Telegram/Discord/Slack/WhatsApp/Signal/CLI), cron 자동화, **서브에이전트 스폰**, MCP 연동, `execute_code` 기반 프로그래매틱 툴 콜. ([Agent 문서](https://hermes-agent.nousresearch.com/docs/), [Agent repo](https://github.com/nousresearch/hermes-agent))
- **모델 계열 흐름**: Hermes 2 → OpenHermes → **Hermes 3** → **DeepHermes-3**(추론 강화 preview). ([DeepHermes-3](https://huggingface.co/NousResearch/DeepHermes-3-Llama-3-8B-Preview))

## 상세 분석

### 1. Hermes "모델" (LLM 계열) — 질문에서 선택한 대상

Hermes 3는 Nous Research가 **Meta의 Llama 3.1을 파인튜닝**해 만든 오픈웨이트 생성형 LLM이다. 베이스 모델의 언어 능력(128K 컨텍스트, 다국어, 강한 추론)을 그대로 가져오면서, **지시 이행·툴 사용·구조화 출력**을 강화해 "파이프라인에서 실제로 쓸모 있는" 모델로 만든 것이 핵심이다. 학습 데이터는 **주로 합성(synthetic) 응답**으로 구성된다. ([공식](https://nousresearch.com/hermes3))

기술 보고서(*Hermes 3 Technical Report*, Ryan Teknium·Jeffrey Quesnelle·Chen Guang)는 Hermes 3를 "강한 추론·창의 능력을 가진 중립 정렬(neutrally-aligned) 범용 instruct·tool-use 모델"로 규정하고, **405B 변형이 공개 벤치마크에서 오픈웨이트 모델 중 최상위급** 성능을 낸다고 보고한다. ([arXiv 2408.11857](https://arxiv.org/abs/2408.11857)) 외부 분석(Nathan Lambert)도 Hermes 3를 오픈웨이트 프런티어 분류 맥락에서 다룬다. ([interconnects.ai](https://www.interconnects.ai/p/nous-hermes-3))

가중치는 **Llama 3 라이선스** 하에 HuggingFace에서 공개되며, DeepInfra·OpenRouter 등 API로도 제공된다. ([OpenRouter](https://openrouter.ai/nousresearch))

### 2. Hermes "Agent" (자율 에이전트 제품) — "헤르메스 에이전트"의 직접 대상

"헤르메스 에이전트"라는 표현을 글자 그대로 받으면, Nous Research가 별도로 만든 제품 **Hermes Agent**가 정확한 대상이다. 이는 코딩 코파일럿이나 챗봇 래퍼가 아니라 **"오래 돌릴수록 더 유능해지는 자율 에이전트"**로, 다음이 차별점이다. ([Agent 문서](https://hermes-agent.nousresearch.com/docs/))

- **닫힌 학습 루프(closed learning loop)**: 사용 중 스스로 스킬을 생성·개선하고, LLM 요약 기반 영속 메모리를 세션 간 유지("self-improving").
- **모델 독립**: 자체 모델이 아니라 200+ LLM 엔드포인트를 골라 쓰는 **운영 시스템**(Nous 모델은 Nous Portal로 사용 가능).
- **배포 유연성**: 로컬·Docker·SSH·서버리스(Daytona·Modal) 등.
- **인터페이스**: 20+ 메신저 통합 + 터미널(슬래시 커맨드 자동완성), cron 스케줄러, 서브에이전트 병렬 실행, 웹 검색·이미지 생성·TTS·브라우저 자동화·MCP.
- **툴 콜 방식**: `execute_code`를 통한 프로그래매틱 툴 콜로 다단계 파이프라인을 단일 추론 호출로 압축.

> 즉 **모델 Hermes**가 "엔진"이라면, **Hermes Agent**는 그 엔진(또는 다른 모델)을 얹어 돌리는 "자율 운영 프레임워크/제품"이다. 둘은 같은 랩(Nous Research) 소속이지만 계층이 다르다. 제3자 생태계(메모리 시스템 `mnemosyne`, 샌드박스 `hermesclaw` 등)도 Hermes Agent 주변에 형성돼 있다. ([Agent repo](https://github.com/nousresearch/hermes-agent))

### 3. 우리 프로젝트(oh-my-gx)와의 관계 — 없음

이번 작업 맥락(oh-my-gx, Sisyphus, OMC)의 에이전트 로스터에는 "Hermes"가 **없다**. 그리스 신화 이름을 쓰는 에이전트는 `Prometheus`·`Momus`·`Metis`·`Sisyphus`·`Oracle` 등이며, "헤르메스 에이전트"는 그중 하나가 아니라 **외부(Nous Research)의 모델/제품**을 가리키는 것으로 확인된다.

### 4. 조사 한계 (투명성)

- Phase 0 arXiv API를 `all:Hermes 3 Nous`로 호출했으나, "HERMES"가 **입자물리 실험명**과 동일해 물리학 논문들이 매칭되었다(빗나감). Hermes 3 기술 보고서(**arXiv 2408.11857**)는 WebSearch로 확보해 [abstract 페이지](https://arxiv.org/abs/2408.11857)에서 저자·정의를 직접 검증했다. 세부 벤치마크 수치는 abstract 범위라 PDF 전문 확인이 필요(❓ 정량 수치 일부 미수집).
- Hermes Agent의 정확한 출시/버전 타임라인은 문서·repo 기준 v0.2.x대로 보이나, 정식 릴리스 일자는 명시 출처를 추가 확인하지 못함(❓).

## 출처

1. [Hermes 3 — Nous Research 공식](https://nousresearch.com/hermes3) — 모델 정의·크기·역량
2. [Hermes-3-Llama-3.1-8B · Hugging Face](https://huggingface.co/NousResearch/Hermes-3-Llama-3.1-8B) — 모델카드(함수호출·JSON·시스템프롬프트 준수)
3. [Hermes 3 Technical Report (arXiv 2408.11857)](https://arxiv.org/abs/2408.11857) — Teknium·Quesnelle·Guang, "neutrally-aligned generalist instruct and tool use model"
4. [Unveiling Hermes 3 (Lambda)](https://lambda.ai/blog/unveiling-hermes-3-the-first-fine-tuned-llama-3.1-405b-model-is-on-lambdas-cloud) — 405B 첫 공개 풀파인튜닝
5. [On Nous Hermes 3 (interconnects.ai, Nathan Lambert)](https://www.interconnects.ai/p/nous-hermes-3) — 오픈웨이트 프런티어 분류 분석
6. [hermes-function-calling-v1 데이터셋 (HF)](https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1) — 함수호출·structured output 학습 데이터
7. [NousResearch/Hermes-Function-Calling (GitHub)](https://github.com/NousResearch/Hermes-Function-Calling) — 함수호출 구현
8. [Hermes Agent 문서](https://hermes-agent.nousresearch.com/docs/) — 자율 에이전트 제품 정의·기능
9. [nousresearch/hermes-agent (GitHub)](https://github.com/nousresearch/hermes-agent) — "self-improving AI agent", 학습 루프
10. [DeepHermes-3-Llama-3-8B-Preview (HF)](https://huggingface.co/NousResearch/DeepHermes-3-Llama-3-8B-Preview) — 추론 강화 후속 변형
11. [Nous Research API/Models (OpenRouter)](https://openrouter.ai/nousresearch) — API 제공 경로
