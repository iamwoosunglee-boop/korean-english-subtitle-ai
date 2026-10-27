# 맥락 기반 자막 번역 모듈 PRD

## Problem Statement

확정된 한국어 원어 전사본을 영어 자막으로 번역할 때 원문 cue를 하나씩
직역하면 한국어의 생략, 어순, 호칭, 문화적 함의와 앞뒤 대화가 사라진다.
한 문장이 여러 cue로 나뉜 경우에는 각 조각의 번역이 부자연스럽게 이어지고,
번역체와 직역체가 최종 결과에 남는다.

현재 전사 워크플로우에는 전체 영상 맥락, 출연자, 화자, 화면 문자, 실제
PTS가 있는 프레임과 확정된 원어 전사본이 있다. 번역은 이 증거를 활용하되
레거시 시스템의 고정 다중 에이전트, 반복 승인, 거대한 프롬프트와 파일
polling을 재현해서는 안 된다.

번역 결과는 단순한 표시용 자막이 아니다. 화자, 전체 에피소드 맥락,
장면 맥락과 원문 cue lineage를 보존하는 JSON 정본이어야 한다. ASS, SRT,
VTT 변환과 Subtitle for Woody 앱 변경은 별도 작업이다.

## Solution

같은 저장소 안에 전사 모듈과 독립된 번역 모듈을 둔다. 번역 모듈은 전사
모듈의 `RunStore`, ASR, 확인 세션과 내부 단계에 의존하지 않고 독립 검증이
가능한 source snapshot JSON과 evidence resolver만 입력으로 사용한다.

초기 프로필은 `ko-KR → en-US`다. 번역 에이전트는 전체 에피소드 맥락,
화자와 청자, 앞뒤 대화, 관련 프레임, 화면 문자와 이전에 확정한 용어를
함께 사용한다. 원문 cue 경계를 문장 경계로 간주하지 않고 여러 cue의
의미를 추론해 자연스러운 영어 표현을 만든 뒤 영어 자막으로 재분절한다.

번역 청크와 의미 묶음은 실행 중에만 존재한다. 최종 JSON의 영구 계층으로
만들지 않는다. 영구 산출물에는 앱과 사람이 실제로 사용하는 목표 cue,
원문 lineage, 화자, 맥락과 미해결 확인 항목만 남긴다.

하나의 orchestrator가 다음 상태 전이를 소유한다.

```text
prepare source-bound context and glossary drafts
  → source-wide terminology audit
  → independent missing-term review
  → ready assets
  → translate
  → deterministic validate
  → independent review
  → revise and review
      or focused specialist review
      or user confirmation
  → finalize
```

## Goals

1. 확정된 한국어 원문을 자연스러운 미국 영어 자막으로 번역한다.
2. 분절된 원문 cue를 독립적으로 직역하지 않고 전체 의미를 복원한다.
3. 목표 언어의 어순과 가독성에 맞게 cue 수와 내부 경계를 재구성한다.
4. 최종 JSON에 화자, 전체 맥락, 장면 맥락과 원문 lineage를 보존한다.
5. 구조 오류는 코드로, 의미·문체 오류는 독립 에이전트로 검증한다.
6. 에이전트가 책임 있게 해결할 수 없는 문제만 사용자에게 묻는다.
7. 변환기와 앱이 안정적으로 소비할 수 있는 versioned JSON을 제공한다.

## Domain Language

**source snapshot**:
번역 경계에서 독립적으로 검증할 수 있는 원어 전사 교환 JSON이다. 전사
모듈의 내부 파일 배치를 노출하지 않는다.

**원문 cue**:
안정된 ID, 시간 범위, 발화, 화자와 증거 참조를 가진 원어 항목이다. 문장
또는 번역 단위를 뜻하지 않는다.

**목표 cue**:
문맥으로 복원한 전체 의미를 목표 언어의 어순과 가독성에 맞게 재분절한
시간 자막이다. 하나 이상의 원문 cue를 참조한다.

**retained context**:
소비자에게도 필요한 전체 맥락 또는 장면별 사실이다. 원문 cue와 evidence
참조를 가지며 내부 chain-of-thought와 구분된다.

**미해결 번역 항목**:
번역, 추가 증거 조회, 검수와 수정 후에도 의미를 책임 있게 확정할 수 없어
사용자 결정이 필요한 항목이다.

**translation document**:
목표 cue, 화자, retained context, lineage와 미해결 항목을 보존하는
versioned JSON이다. `needs_confirmation` 또는 `finalized` 상태를 가진다.

## User Stories

1. 번역 편집자로서 여러 원문 cue에 걸친 한 문장을 하나의 의미로 번역하고 싶다.
2. 번역 편집자로서 영어 문법에 맞게 목표 cue 수와 경계를 바꾸고 싶다.
3. 번역 편집자로서 목표 cue가 어떤 원문 cue에서 나왔는지 추적하고 싶다.
4. 번역 편집자로서 각 cue의 화자와 복수 화자를 보존하고 싶다.
5. 번역 편집자로서 프레임과 앞뒤 대화로 생략된 주어와 대상을 이해하고 싶다.
6. 영어 시청자로서 번역체가 아닌 자연스러운 미국 영어를 읽고 싶다.
7. 영어 시청자로서 원문에 없는 내용이 추가되거나 의미가 빠지지 않기를 원한다.
8. 영어 시청자로서 가사의 의미와 감정을 자연스러운 자막으로 읽고 싶다.
9. 검수자로서 원문 의미, 자연스러움, 문화와 분절을 독립적으로 검토하고 싶다.
10. 검수자로서 높은 위험이 있는 항목에만 추가 검수를 사용하고 싶다.
11. 사용자로서 에이전트가 해결할 수 있는 문제를 질문받지 않기를 원한다.
12. 사용자로서 어려운 질문에는 이유, 증거와 여러 완전한 대안을 받고 싶다.
13. 앱 개발자로서 번역 JSON에서 목표 cue, 화자와 맥락을 복원하고 싶다.
14. 운영자로서 누락, 잘못된 lineage와 시간 범위 오류를 자동 차단하고 싶다.

## Product Requirements

### Source Snapshot

- 번역은 standalone source snapshot만 입력으로 받는다.
- snapshot은 schema version, 문서 ID와 content digest, 원문 locale과
  duration을 제공해야 한다.
- 문서 ID는 같은 논리적 원문을 식별하고 content digest가 내용 revision을
  식별한다. digest는 정규화한 맥락, cue와 evidence metadata·resource
  digest를 포함한다.
- 현재 전사 run에서 export할 때 `RunState`와 `Transcript`가 모두
  `finalized`·`valid`여야 하고 모든 cue가 certain이며 non-null unique ID를
  가져야 한다.
- 모든 원문 cue는 필수 stable ID, 순서, 실제 시간 범위, 원문 text,
  content kind와 화자 목록을 가져야 한다.
- 화자는 unknown과 복수 화자를 표현할 수 있어야 한다. 현재 전사본의
  단일 speaker 문자열은 source snapshot 준비 단계에서 명시적인 화자
  label 목록으로 정규화한다.
- content kind와 프로그램·에피소드·참여자 정보가 없으면 `unknown`을
  명시할 수 있지만 사실을 임의로 생성할 수 없다.
- 프로그램, 에피소드와 참여자 맥락을 포함해야 한다.
- frame ID와 다른 증거 ID를 evidence manifest에 연결해야 한다.
- 번역 경계는 snapshot을 다시 검증하며 내부 `validation_status` 문자열을
  신뢰하지 않는다.
- 수정 ASS는 source snapshot을 준비하기 위한 임시 전달 수단일 뿐 정식
  번역 입력 계약이 아니다.

### Evidence

- JSON은 의미와 참조를 전달하고 프레임 자체는 외부 resource로 유지한다.
- evidence resolver는 frame ID를 실제 PTS와 읽을 수 있는 durable locator로
  해석해야 한다.
- versioned manifest entry는 unique evidence ID, kind, media type,
  resource digest, snapshot/package base URI 기준 locator와 frame인 경우
  `pts_ms`를 제공해야 한다.
- 번역 모듈이 전사 run의 상대 경로나 `RunStore`를 직접 탐색할 수 없다.
- 번역을 시작하기 전에 실제 참조된 evidence resource의 존재, 읽기 가능성,
  digest와 PTS 범위를 검사한다. 하나라도 실패하면 candidate를 만들지 않는다.
- source-supplied fact와 에이전트가 유지할 장면 맥락을 구분해야 한다.
- retained scene context는 관련 원문 cue와 evidence ID를 참조해야 한다.
- 내부 추론 과정, 원시 프롬프트와 chain-of-thought는 최종 JSON에 저장하지 않는다.

### Prepared Context and Glossary

- prepared context와 glossary는 정확한 finalized source digest에 귀속되는
  draft로 새로 만든다.
- source revision이 달라지면 같은 에피소드라도 기존 ready context나
  glossary를 복제·재바인딩하지 않는다. 공식 prepare 단계부터 다시 시작한다.
- 작업자는 finalized source 전체, input context와 evidence를 읽고 인명,
  작품명, 조직명, 프로그램명, 반복 표현과 명시된 영문 표기를 조사한다.
- 확정 가능한 항목은 근거와 함께 glossary에 기록하고, 근거가 부족한
  preferred target을 임의로 만들지 않는다.
- 별도 검수자는 source 전체의 용어 후보와 완성된 glossary를 대조해 누락을
  찾는다. 기존 entry 간 충돌 검사만으로 이 검수를 대신할 수 없다.
- 빈 glossary는 source-wide 조사와 독립 누락 검수 모두 후보가 없다고
  판정한 경우에만 ready가 될 수 있다.
- context와 glossary 보완 및 누락 검수가 끝난 뒤에만 ready 전이와 번역을
  시작한다.

### Translation

- 원문 cue 경계를 번역 문장 경계로 취급하지 않는다.
- 여러 cue의 의미 연결은 에이전트가 맥락으로 추론한다.
- semantic chunk 전체의 자연스러운 target 문장을 먼저 완성한 뒤 timing과
  화자 소유권에 맞춰 목표 cue로 재분절한다.
- semantic chunk 범위는 source digest에 묶인 하나의 plan으로 한 번만
  확정한다. 번역가와 검수자는 같은 chunk ID와 source cue 소유 범위를
  사용하며 검수 단계에서 범위를 다시 합치거나 나누지 않는다.
- 대화는 사실, 관계, 감정과 말투를 보존하며 자연스러운 미국 영어로
  현지화한다.
- 원문에 없는 정보를 추가하거나 의미를 누락·반전하지 않는다.
- 이미 영어인 가사는 보존한다.
- 한국어 가사는 부를 수 있는 각색이 아니라 의미와 감정을 전달하는
  자연스러운 영어 자막으로 번역한다.
- 화면 문자는 대사가 아닌 번역 근거다. 화면 문자 자체의 독립 번역은
  별도 요구가 생길 때 추가한다.
- 번역 모듈이 display-ready 목표 cue의 text, timing과 lineage를 소유한다.
- 목표 cue는 참조 원문 cue들의 시간 envelope 안에서 재분절할 수 있다.
- canonical validator가 `en-US` 자막 가독성 기준을 검증한다. 구체 CPS와 줄 길이
  값은 구현 시 하나의 고정 설정으로 정한다.

### Validation and Review

- deterministic validator가 통과한 candidate만 에이전트 검수를 받는다.
- validator는 schema, identity, lineage, coverage, order, timing,
  readability와 document state를 검사한다.
- 독립 검수자는 원문, 관련 evidence와 번역 candidate를 검토한다.
- 검수 finding은 affected ID, severity, domain tag, blocking 여부와
  실행 가능한 수정 요청을 가져야 한다.
- affected ID는 source cue, target cue 또는 retained context 종류를
  명시해야 하며 finding은 검토한 candidate digest에 귀속된다.
- 높은 위험의 blocking domain tag만 focused specialist review를 실행한다.
  specialist는 고정된 상시 에이전트가 아니라 필요한 검수 capability다.
- focused review는 blocker를 직접 해제하거나 문서를 finalized로 만들 수
  없다. 수정 candidate는 deterministic validation과 독립 검수를 다시 받는다.
- 일반 문체 문제는 최대 2회, 의미·문화 문제는 최대 3회 수정한다.
- 실행 가능한 새 근거나 수정 전략이 없으면 상한 전에도 사용자에게
  escalation한다.
- 동일 표현을 반복 생성하는 시도는 유효한 수정으로 계산하지 않는다.

### User Confirmation

- 질문은 원문 전체 발화와 현재 최선의 번역을 표시해야 한다.
- 왜 어려운지와 판단에 사용한 evidence를 설명해야 한다.
- 최소 2개, 최대 4개의 완전한 번역 대안을 제공해야 한다.
- 각 대안은 전달 의미, 장점과 오역·문화적 위험을 설명해야 한다.
- 추천안과 추천 이유를 제공해야 한다.
- 사용자가 후보 또는 직접 입력한 답을 선택할 수 있어야 한다.
- 미해결 항목은 stable ID, source/target scope, evidence ID와 각 대안의
  stable option ID를 가져야 한다.
- 답은 document ID, current candidate digest와 미해결 항목 ID를 참조하고
  option ID 또는 non-empty 직접 입력 중 정확히 하나를 제공해야 한다.
- stale, unknown, duplicate, empty 또는 잘못된 답은 현재 published
  document를 변경하지 않는다.
- 적용한 답은 deterministic validation과 독립 검수를 다시 통과해야 한다.

### Translation Document

- 상태는 `needs_confirmation` 또는 `finalized`다.
- 번역 candidate와 review finding은 실행 중에만 존재한다. canonical
  output으로 publish되는 것은 두 상태의 translation document뿐이다.
- 각 문서는 stable document ID, source digest와 자신의 content digest를
  가져야 한다.
- `needs_confirmation`은 하나 이상의 미해결 항목을 가져야 한다.
- `finalized`는 미해결 항목과 blocking finding을 가질 수 없다.
- deterministic validation과 independent review는 정확한 candidate digest에
  귀속되며 candidate가 바뀌면 이전 결과로 finalize할 수 없다.
- finalized 문서는 immutable하다. source digest가 바뀌면 새 translation
  document를 만든다.
- source document ID, digest, source locale과 target locale을 기록해야 한다.
- 전체 프로그램·에피소드·참여자 맥락을 보존해야 한다.
- retained scene context는 관련 source/evidence를 참조해야 한다.
- 각 목표 cue는 stable ID, 원문 cue ID 목록, sequence, timing, text,
  화자 목록, content kind와 context ID를 가져야 한다.
- 같은 translation document의 수정 과정에서 변경되지 않은 목표 cue ID는
  안정적으로 유지해야 한다.

### Lineage Invariants

- 모든 source cue 참조는 존재해야 한다.
- 모든 timing은 integer millisecond half-open interval
  `[start_ms, end_ms)`이다.
- 한 목표 cue 안의 source cue 참조는 중복되지 않고 원문 순서를 따라야 한다.
- 모든 source cue는 하나 이상의 목표 cue에 참조돼야 한다.
- 한 source cue를 여러 목표 cue가 참조하는 정상 재분절은 허용한다.
- 목표 cue timing은 참조 source cue의 최소 start와 최대 end로 이루어진
  envelope를 벗어날 수 없다.
- 겹치는 발화와 interleaved crosstalk를 허용한다. 관련 없는 발화의 병합은
  구조 규칙이 아니라 의미 검수에서 차단한다.
- 목표 cue sequence는 contiguous하며 기본 정렬은 start, end와 sequence
  순서다. cue가 참조하는 retained context ID 목록은 비어 있을 수 있다.

## Implementation Decisions

- 같은 저장소의 독립 번역 모듈로 구현한다.
- versioned JSON과 evidence resolver가 전사·번역의 deep boundary다.
- 초기 profile은 `ko-KR → en-US`다.
- 청크와 의미 묶음은 실행 중 추론하며 최종 schema에 저장하지 않는다.
- 하나의 orchestrator가 candidate revision과 상태 전이를 소유한다.
- 고정 에이전트 graph 대신 기본 검수와 위험 기반 capability를 사용한다.
- TaT와 gcommon-pro-translation은 원칙과 실패 사례만 참고하고 코드,
  prompt와 orchestration을 복사하지 않는다.

## Acceptance Criteria

1. 불완전하거나 잘못된 source snapshot은 번역을 시작할 수 없다.
2. 현재 전사 run을 독립 source snapshot과 evidence manifest로 준비할 수 있다.
3. 여러 원문 cue의 한 문장을 자연스러운 영어 표현으로 번역할 수 있다.
4. 원문 cue 수와 다른 수의 목표 cue로 재분절할 수 있다.
5. 목표 cue의 화자, retained context와 lineage가 JSON round-trip 후 보존된다.
6. unknown과 복수 화자, 겹치는 발화를 표현할 수 있다.
7. 목표 cue lineage 누락, 잘못된 ID, 순서와 timing 오류를 차단한다.
8. 한 source cue의 여러 target cue 참조는 정상 재분절로 허용한다.
9. 이미 영어인 가사는 보존되고 한국어 가사는 의미 중심으로 번역된다.
10. 평범한 문장은 기본 검수 후 전문 검수 없이 통과한다.
11. 높은 위험의 blocking finding만 focused specialist review로 전달된다.
12. 실행 가능한 수정이 있는 동안 승인된 2/3회 상한 안에서 재검수한다.
13. 해결 전략이 없거나 상한에 도달하면 사용자 확인으로 전환한다.
14. 사용자 질문은 어려운 이유, evidence, 2~4개 대안, 장점·위험과 추천을 포함한다.
15. 사용자 답을 적용한 결과는 validation과 독립 검수를 다시 받는다.
16. 미해결 항목이나 blocking finding이 있으면 finalized가 될 수 없다.
17. source digest가 달라지면 기존 translation document를 현재 결과로
    받아들이지 않는다.
18. 최종 JSON만으로 목표 cue, 화자, 전체 맥락과 장면 맥락을 복원할 수 있다.
19. source revision이 달라지면 기존 ready context나 glossary를 복제하거나
    source identity만 바꿔 재사용할 수 없다.
20. glossary는 finalized source 전체의 용어 조사와 독립 누락 검수를 거친
    뒤에만 ready가 될 수 있다.
21. 작업자와 검수자는 cue별 번역을 이어 붙이지 않고 semantic chunk 전체를
    번역·재조립해 의미와 자연스러움을 확인한다.
22. 번역과 검수의 source cue 소유 범위는 하나의 source-bound chunk plan에서
    파생되며 서로 다르면 review harness가 거부한다.
19. stale하거나 잘못된 사용자 답과 evidence preflight 실패는 기존 published
    document를 변경하지 않는다.

## Testing Decisions

- schema와 state transition은 machine-consumed field만 검증한다.
- source boundary, evidence resolution, lineage, coverage, timing과 stale digest를
  deterministic test로 검증한다.
- 여러 source cue를 다른 수의 target cue로 재분절하는 fixture를 사용한다.
- unknown·복수 화자, 겹치는 발화, 가사와 화면 문자 evidence를 검증한다.
- 기본 검수, focused review, 수정과 사용자 escalation 상태 전이를 검증한다.
- async 상태 검증은 fixed sleep이나 polling 없이 정확한 결과 신호를 기다린다.
- 프롬프트 문구, 설명문과 chain-of-thought를 테스트로 고정하지 않는다.
- 기존 전사 동작에 영향을 주는 변경이 있으면 관련 회귀 테스트를 실행한다.

## Manual QA

구조 검증 뒤 위험 기반 표본을 사람이 직접 확인한다.

- 여러 cue에 걸친 한 문장
- 겹치는 발화와 복수 화자
- 이미 영어인 가사와 한국어 가사
- 문화적 표현과 호칭
- 화면 문자 또는 프레임이 해석에 필요한 발화
- 평범한 대화

critical omission, invention, meaning reversal 또는 wrong-speaker 오류는
한 건도 허용하지 않는다. 번역체, 어색한 분절과 문화적 부자연스러움이
발견되면 해당 범위를 수정·재검수한다.

## Out of Scope

- ASS를 정식 번역 입력으로 지원
- ASS, SRT, VTT와 다른 표시 형식 변환
- Subtitle for Woody 앱 변경
- 화면 문자 자체의 독립 번역
- singable lyric adaptation과 더빙용 립싱크 최적화
- `ko-KR → en-US` 외 실제 언어 profile
- 번역 메모리 데이터베이스
- crash resume와 checkpoint orchestration
- candidate·review revision history 저장
- 원시 모델 응답, prompt와 chain-of-thought 보관
- 레거시 코드 마이그레이션
- 원어 전사본 편집
- 배포와 원격 provider SDK 통합

## Delivery Sequence

1. source snapshot, evidence resolver와 translation document 계약을 구현한다.
2. deterministic validator와 lineage invariant를 구현한다.
3. translate, review, focused review, revise와 escalation 상태 전이를 구현한다.
4. 사용자 답 적용과 finalization gate를 구현한다.
5. agent workflow 경계와 실제 사용 surface를 구현한다.
6. 자동 검증과 대표 fixture 기반 manual QA를 완료한다.

## Release QA Note

사용자가 수정한 노바노바 원어 전사본을 전달하면 별도 release QA로
전체 영상 번역을 실행한다. 이 실제 데이터 준비는 번역 모듈 구현의 선행
조건이나 ASS 입력 요구가 아니다.
