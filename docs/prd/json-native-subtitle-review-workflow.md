# JSON 정본 기반 자막 편집·검수·번역 워크플로 PRD

- 문서 상태: 외부 재검토용 수정안
- 문서 개정: Revision 2
- 작성일: 2026-08-07
- 대상 제품: Transcription-Translation-Module, Subtitles for Woody(SFW)
- 초기 언어 프로필: `ko-KR → en-US`

이 문서는 전사, 인간 원어 검수, 번역 준비, AI 번역, AI 검수, 인간 번역 검수를 하나의 JSON 정본 흐름으로 연결하는 제품 요구사항과 양쪽 저장소가 공유할 실행 계약을 정의한다.

Revision 2는 외부 검토에서 지적된 다음 P0 사항을 확정한다.

1. 기존 실험 계약과 새 계약을 구분하기 위해 원어·번역 정본을 `source-transcript.v2`, `translation-document.v2`로 올린다.
2. 실제 자막 내용에 대한 `content_digest`와 전체 저장 레코드에 대한 `record_digest`를 분리한다.
3. 번역 수명주기, 사용자 결정 상태, AI 검수 상태를 서로 직교시킨다.
4. 초기 전사 맥락, 확정 source, 번역용 prepared context/glossary의 의존 방향을 단방향으로 고정한다.
5. SFW는 일반 `Subtitle/Paragraph`를 정본으로 쓰지 않고 `CanonicalSubtitleSession`을 정본으로 유지한다.
6. finalized source 구조 변경 뒤 기존 translation을 자동 rebase하지 않는다. 새 source revision과 새 translation을 만든다.
7. draft 저장 검증, draft 진단, finalization 검증을 분리한다.
8. context, glossary, review artifact의 최소 필드·scope·binding·충돌 규칙을 명시한다.
9. 구현을 계약 기반, SFW 왕복, 구조 편집, 번역 준비·AI 검수, 편의 기능 순서로 나눈다.

현재 `source-transcript.v1`, `translation-document.v1`은 이미 코드와 실제 fixture에 존재하므로 같은 버전명으로 구조를 교체하지 않는다. v1은 legacy read-only 입력과 마이그레이션 대상으로 유지하고, 신규 SFW 정본 편집과 신규 AI 워크플로는 v2만 사용한다.

### P0 Resolution Matrix

| Review item | 확정 결정 | 주요 섹션 |
|---|---|---|
| P0-1 Schema version | source/translation v2, v1 read-only migration | Schema Version and Migration Decision |
| P0-2 Digest cycle | semantic content digest와 full record digest 분리, RFC 8785 | Canonical JSON and Digest Rules |
| P0-3 State contradiction | lifecycle·decision 직교, review state 외부 계산 | State Model |
| P0-4 Dependency cycle | input context → source → prepared assets → translation | Artifact Dependency Graph |
| P0-5 SFW metadata loss | `CanonicalSubtitleSession`, full undo/dirty, OriginalText guard | SFW Canonical Session Architecture |
| P0-6 Mutation graph | finalized source immutable, MVP auto-rebase 제외 | Cue Mutation Rules |
| P0-7 Draft vs final validation | save validation·diagnostics·finalization validation 분리 | Validation Levels |
| P0-8 Asset/review contracts | stable IDs, scope, evidence, binding, coverage와 findings | Normative Context/Glossary/Review Contracts |

---

## Problem Statement

현재 자막 제작 과정에서는 단계별 정본이 일관되지 않는다.

```text
AI 전사 JSON
→ 사람이 ASS 수정
→ ASS에서 별도 번역 source 생성
→ AI 번역 JSON
→ ASS/SBV/CSV 변환
→ 사람이 SBV 재수정
```

이 흐름에서는 사람이 고친 내용이 원래 JSON 정본과 분리되고, 다음 단계가 어떤 파일을 최신으로 봐야 하는지 다시 판단해야 한다. ASS·SBV·CSV마다 cue 구조와 메타데이터 보존 범위가 달라 원어, 번역, 화자, lineage, 문맥과 검수 상태가 서로 어긋날 수 있다.

번역 검수도 CSV에서 원어와 번역어를 pair로 봐야 한다. SFW에는 이미 `Original text | Text` 형태의 번역 모드가 있지만 현재 translation JSON의 `source_cue_ids`를 이용해 원문을 파생하지 못한다. 원문 552개와 번역 505개처럼 cue 수가 다르면 행 번호나 TC 유사도로는 신뢰할 수 있는 pair를 만들 수 없다.

사람이 cue를 추가·삭제·분절·병합하거나 TC·화자·문장을 수정했을 때 stable cue ID, source lineage, note, context/evidence reference와 unresolved decision이 어떻게 변하는지도 공식 계약이 없다. JSON을 읽을 수 있더라도 편집 과정에서 메타데이터, undo 또는 dirty 상태가 누락되면 정본 왕복으로 볼 수 없다.

콘텐츠 맥락과 단어 기준도 한곳에 정리되지 않는다. 인물, 공식 영문명, 작품명, 음식명, 애칭, 말장난, 의미 번역을 하면 안 되는 음가, 인간이 직접 선택한 번역 등이 긴 context summary와 자유형 cue note에 섞여 있다. AI는 무엇이 반드시 지켜야 할 기준인지, 무엇이 참고 정보인지, 무엇이 사람의 승인된 선택인지 구분하기 어렵다.

실제 NOVA NOVA 콘텐츠 번역에서는 구조 검증과 여러 차례 AI 검수를 통과한 뒤에도 다음 유형이 남았다.

- 공식 영문명 불일치: `Kina`, `Chanel`, `Ki Bo-bae`
- 작품명 표기 불일치: `After School Exorcism Club`
- 음가의 의미 오역: 발성 연습용 `개팔아`를 `Sell dogs!`로 번역
- 관계 농담 손실: `너네 엄마 왜 저러냐?`를 고유 별명처럼 `Mama Yuna`로 처리
- 한 발화가 여러 target cue에 걸릴 때 구두점·대문자 연결 오류
- 영어 원어민 대화보다 직역체·설명체에 가까운 표현
- source context note에 있던 중요한 해석이 최종 번역에 반영되지 않은 사례

반대로 `LA's all-star team`처럼 원문보다 구체적이지만 사용자가 번역 과정에서 의식적으로 선택한 문장은 자동 오류로 취급하면 안 된다. 시스템은 AI의 임의 추가와 사람의 승인된 번역 결정을 구분하고, reviewer가 승인된 표현만 임의로 되돌리지 못하게 해야 한다. 해당 cue의 나머지 의미·문법·TC 검수까지 면제되는 것은 아니다.

기존 AI 실행은 번역 목적과 역할보다 다음 구조 조건을 강하게 강조했다.

- exact ownership와 lineage
- 화자·TC 보존
- 최대 2줄, 한 줄 42자, 20 CPS
- typed candidate와 zero validation violations
- translator와 reviewer 실행 분리

이 조건은 필요한 안전장치지만 좋은 번역의 완료 조건은 아니다. Translator는 사람이 바로 검수할 수 있는 자연스럽고 정확한 자막을 만드는 편집 책임보다 스키마를 통과하는 JSON 생성 책임으로 행동했고, reviewer는 실제 품질보다 검토 범위를 선언하는 데 성공할 수 있었다.

현재 Python 계약과 SFW 내부 모델에도 직접적인 불일치가 있다.

- 현재 translation v1에는 `draft` lifecycle이 없다.
- v1 target cue는 비어 있는 lineage를 허용하지 않는다.
- source/translation v1은 episode context를 inline으로 가진다.
- 현재 translation digest는 lifecycle status를 포함한다.
- SFW의 기존 row ID는 `Guid`지만 canonical cue ID는 opaque string이다.
- 기존 split·merge·undo·dirty detection은 canonical metadata를 알지 못한다.
- 기존 `OriginalText` 관련 명령은 원문 열을 직접 수정할 수 있다.

따라서 단순 JSON parser 추가로는 안전한 정본 왕복을 구현할 수 없다.

---

## Solution

모든 단계의 정본을 versioned JSON으로 고정하고, SFW가 v2 정본 JSON을 직접 열고 수정하고 같은 스키마로 저장한다.

```text
초기 content context
→ AI 전사 source-transcript.v2 draft
→ SFW 인간 원어 검수 및 finalized source revision
→ prepared content context + glossary
→ AI 번역 translation-document.v2 draft
→ 별도 translation-review.v1
→ SFW 인간 번역 검수 및 finalized translation revision
→ ASS / SBV / SRT / VTT / CSV export
```

ASS, SBV, SRT, VTT와 CSV는 정본이 아니다. 확정된 JSON에서 필요할 때 만드는 파생 산출물이다. 사람이 파생 파일을 수정해 정본으로 되돌리는 흐름은 기본 워크플로에서 제거한다.

SFW는 `source-transcript.v2`와 `translation-document.v2`를 직접 지원한다. 번역 JSON을 열면 `source_cue_ids`로 source JSON의 cue를 찾아 기존 번역 모드의 `Original text | Text` 열에 원어와 번역어를 pair로 보여준다. pair는 source ID만 사용하며 행 번호·TC 유사도 fallback을 사용하지 않는다.

SFW는 canonical JSON을 일반 `Subtitle/Paragraph`로 완전히 축약하지 않는다. 문서 상위 ref, opaque cue ID, lineage, context/evidence IDs, note, lifecycle과 decision 상태를 가진 `CanonicalSubtitleSession`을 정본으로 유지하고, 기존 Subtitle UI·waveform·export 모델은 여기서 파생한다.

콘텐츠 전체 맥락과 번역 기준은 `content-context.v1`, `translation-glossary.v1` 자산으로 분리한다. 초기 전사 맥락과 번역용 prepared context는 같은 자산 스키마의 서로 다른 revision으로 표현하되 source는 초기 맥락 revision만 참조하고, prepared context/glossary가 finalized source를 참조한다. source가 prepared asset을 다시 참조하지 않아 digest cycle을 만들지 않는다.

AI review는 translation 문서 내부 status를 바꾸지 않는다. 별도 `translation-review.v1` artifact가 exact translation content, source, prepared context, glossary와 review profile에 binding된다. 사람만 translation revision을 finalized로 만들 수 있다.

---

## Goals

1. 전사부터 최종 번역까지 단계마다 정본 JSON을 명확히 한다.
2. SFW에서 원어와 번역어를 CSV처럼 나란히 보며 직접 검수하게 한다.
3. 원문과 번역 cue 수가 달라도 source lineage로 pair를 정확히 복원한다.
4. JSON 편집, undo/redo, dirty detection과 atomic save에서 canonical metadata를 보존한다.
5. 새 cue 추가, 삭제, 분절과 병합에서 ID·lineage·note·reference 상태를 명확히 갱신한다.
6. 콘텐츠 맥락과 번역 기준을 별도 자산으로 관리한다.
7. 번역 전에 prepared context와 glossary를 수집·확정하는 공식 단계를 둔다.
8. 사람의 선택 번역을 AI가 임의로 되돌리지 않게 하되 cue 전체 검수 면제로 만들지 않는다.
9. Translator와 Reviewer의 목적, 책임과 완료 조건을 실제 품질 중심으로 정의한다.
10. content digest, record digest, lifecycle, decision과 review binding을 순환 없이 관리한다.
11. legacy v1 데이터를 손실 없이 v2 프로젝트로 마이그레이션한다.
12. ASS·SBV·CSV 등 모든 전달 형식을 같은 최종 translation revision에서 생성한다.

---

## Non-Goals

- 별도 sidecar와 edit patch 포맷을 정본으로 도입하지 않는다.
- ASS·SBV 수정본을 일반적으로 JSON에 역추론하는 importer를 만들지 않는다.
- source 구조 변경 뒤 기존 translation을 자동 rebase하지 않는다.
- 범용 translation memory 또는 중앙 glossary 서비스를 만들지 않는다.
- SFW 전체 UI를 재설계하지 않는다.
- AI reviewer가 항상 별도 유료 모델이어야 한다고 강제하지 않는다.
- 인간 승인 없이 translation을 무인 최종화하지 않는다.

---

## Domain Language

**정본 revision**:

특정 `document_id` 또는 `asset_id`의 한 저장 revision이다. 각 revision은 unique `revision_id`, `content_digest`, `record_digest`를 가진다.

**content digest**:

실제 전사·번역 판단과 AI review validity에 영향을 주는 semantic content projection의 digest다. lifecycle, finalization, review artifact, locator path와 UI 상태는 포함하지 않는다.

**record digest**:

상대 경로, lifecycle, finalization metadata를 포함한 전체 저장 레코드의 무결성 digest다. `record_digest` 자기 자신만 제외한다.

**locator hint**:

참조 파일을 찾기 위한 상대 `path` 또는 evidence locator다. identity는 ID와 digest이며 path는 identity가 아니다.

**원어 문서**:

`source-transcript.v2` 형식의 한국어 전사 revision이다.

**번역 문서**:

`translation-document.v2` 형식의 영어 자막 revision이다.

**pair view**:

translation target cue 한 행에 대응하는 source cue 텍스트와 번역문을 나란히 보여주는 SFW 검수 화면이다.

**초기 맥락 revision**:

전사 전에 사용한 `content-context.v1` revision이며 `stage=input`이다.

**prepared 맥락 revision**:

finalized source를 기준으로 번역 전에 보완한 `content-context.v1` revision이며 `stage=prepared`다.

**단어집 자산**:

공식 영문명, 작품명, 용어, 애칭, 음가, 말장난과 사람 선택 번역을 담는 `translation-glossary.v1` revision이다.

**인간 선택 번역**:

직역과 다르더라도 사용자가 특정 표현·scope에서 의도적으로 승인한 번역 결정이다. 그 표현만 reviewer보다 우선하며 해당 cue의 다른 품질 영역은 계속 검수한다.

**effective review state**:

현재 translation, source, context, glossary와 review profile binding을 review artifact와 비교해 계산한 `not_run | passed | failed | stale` 상태다. translation JSON에 직접 저장하지 않는다.

**최종화**:

사람이 SFW에서 현재 revision의 정본 내용과 diagnostics를 확인하고 명시적으로 완료한 상태다.

---

## Artifact Dependency Graph

의존 방향은 다음과 같이 고정한다.

```text
content-context.v1 (stage=input)
        ↓
source-transcript.v2 revision
        ↓
content-context.v1 (stage=prepared)
translation-glossary.v1
        ↓
translation-document.v2 revision
        ↓
translation-review.v1
        ↓
human finalization record in translation revision
```

규칙:

- source는 전사 당시 사용한 `input_context_ref`만 provenance로 보존한다.
- prepared context와 glossary는 `prepared_from_source_ref`를 가진다.
- translation은 finalized source, prepared context와 glossary를 참조한다.
- source는 prepared context 또는 glossary를 참조하지 않는다.
- review는 translation content와 세 입력 digest에 binding된다.
- path는 locator hint이며 dependency identity에 사용하지 않는다.

---

## Schema Version and Migration Decision

### Canonical Versions

신규 canonical contract는 다음 버전을 사용한다.

```text
source-transcript.v2
translation-document.v2
content-context.v1
translation-glossary.v1
translation-review.v1
```

### Schema Evolution Policy

- 모든 canonical object schema는 `additionalProperties: false`를 사용한다.
- reader가 모르는 public field를 보존하지 못하는 현재 strict 계약에서는 field 추가도 schema version 변경으로 취급한다.
- 같은 schema version 안에서는 의미, requiredness, digest projection과 enum을 바꾸지 않는다.
- Python과 SFW는 지원 version을 명시적으로 나열하며 shape 추측으로 다른 version을 열지 않는다.

### Legacy v1 Policy

- `source-transcript.v1`, `translation-document.v1`은 legacy read-only 형식이다.
- Python은 v1을 계속 읽어 migration input으로 사용할 수 있다.
- SFW는 v1을 write-save하지 않는다.
- SFW에서 v1을 열면 읽기 전용 경고와 migration 안내를 보여준다.
- v1을 unknown v2 field 손실과 함께 덮어쓰지 않는다.

### Migration Command

계약 기반 단계에서 다음 동작을 제공한다.

```text
translation-cli migrate-project SOURCE_V1 [TRANSLATION_V1]
  --output-dir DIRECTORY
  [--evidence-root DIRECTORY]
```

동작:

1. source v1 inline context를 `content-context.v1 stage=input` revision으로 만든다.
2. source v1을 source v2 revision으로 변환한다.
3. translation v1이 있으면 inline context와 retained context를 prepared context fact로 옮기고 prepared context를 `ready`로 만든다.
4. translation v1에 맞는 empty 또는 추출된 glossary를 만들고 `ready`로 만든다. 빈 glossary도 명시적인 ready asset이다.
5. translation v1 cue와 lineage를 translation v2로 변환한다.
6. v1 `finalized`는 v2 `lifecycle_status=finalized`, decision clear와 `legacy_status_imported` acknowledgement로 변환한다.
7. v1 `needs_confirmation`은 v2 `lifecycle_status=draft`, decision needs_confirmation으로 변환한다.
8. 절대 file evidence base URI는 `--evidence-root`가 주어지고 digest가 검증된 경우 상대 URI로 rebase한다.
9. 입력 파일은 덮어쓰지 않는다.
10. 산출물, old/new digest와 경고를 migration receipt로 기록한다.

마이그레이션 없는 silent schema reinterpretation은 허용하지 않는다.

---

## Common Identity and Reference Contracts

### Document Reference

```json
{
  "document_id": "logical-document-id",
  "revision_id": "revision-uuid",
  "content_digest": "sha256",
  "path": "../source/source.json"
}
```

### Asset Reference

```json
{
  "asset_id": "logical-asset-id",
  "revision_id": "revision-uuid",
  "content_digest": "sha256",
  "path": "../assets/context.json"
}
```

규칙:

- ID와 `content_digest`가 권위 identity다.
- `path`는 owning JSON 위치 기준 상대 locator hint다.
- Save As는 새 위치 기준으로 path를 rebase한다.
- path 변경은 `record_digest`만 바꾸고 `content_digest`를 바꾸지 않는다.
- ID 또는 digest가 다르면 행 번호·TC 유사도로 다른 파일을 대체하지 않는다.
- path가 없거나 깨져도 사용자가 같은 identity 파일을 다시 지정할 수 있다.

### Revision Identity

모든 document와 asset revision은 다음을 가진다.

```text
document_id 또는 asset_id
revision_id
supersedes_revision_id (optional)
content_digest
record_digest
```

- `document_id`·`asset_id`는 논리적 자산을 식별한다.
- `revision_id`는 저장 revision을 식별한다.
- finalized document revision은 in-place 수정하지 않는다.
- ready asset revision도 in-place 수정하지 않는다.
- finalized/ready revision 편집을 시작하면 새 draft revision을 만들고 `supersedes_revision_id`로 이전 revision을 가리킨다.
- 이전 finalized/ready 파일은 보존한다.

### Common Evidence Manifest

Source, context와 glossary는 같은 evidence manifest shape를 재사용한다.

```json
{
  "evidence": {
    "base_uri": "./evidence/",
    "entries": [
      {
        "evidence_id": "official-name-source",
        "kind": "text",
        "media_type": "text/plain",
        "resource_digest": "sha256",
        "locator": "official-name.txt"
      }
    ]
  }
}
```

- cue, participant, fact와 glossary entry의 `evidence_ids`는 owning artifact의 evidence manifest를 참조한다.
- source cue 근거를 가리킬 때는 evidence ID 복사 대신 `scope_source_cue_ids` 또는 source cue ID를 사용한다.
- frame evidence는 `pts_ms`가 필수다.
- resource digest와 PTS는 content digest에 포함한다.
- base URI와 locator path는 record digest에만 포함한다.
- unknown evidence ID는 save validation 오류다.

---

## Canonical JSON and Digest Rules

### Canonicalization

Python과 C#은 RFC 8785 JSON Canonicalization Scheme(JCS)을 사용한다.

추가 프로필 규칙:

- encoding은 UTF-8이며 BOM을 사용하지 않는다.
- 모든 public string은 NFC 정규화되어야 한다.
- canonical contract의 숫자는 정수만 허용한다. TC는 millisecond integer다.
- optional field가 없으면 key를 생략한다. `null`은 canonical contract에서 허용하지 않는다.
- collection field는 schema에서 required로 두고 빈 경우 `[]`로 쓴다.
- object key ordering과 number rendering은 RFC 8785를 따른다.
- array ordering은 의미가 있으므로 입력 순서를 보존한다.
- digest는 canonical bytes의 SHA-256 lowercase hex다.
- timestamp는 UTC RFC 3339 string으로 저장하고 semantic content digest에서는 제외한다.

계약 단계에서 Python과 C#이 공유하는 golden canonical bytes와 digest vector를 제공한다. fixture만 공유하고 규칙을 각자 추측하는 방식은 허용하지 않는다.

### Source Content Digest Projection

`source-transcript.v2.content_digest`에는 다음을 포함한다.

- schema version
- source locale와 duration
- `input_context_ref`의 ID, revision ID와 content digest
- evidence entry의 stable identity, resource digest, kind, media type와 PTS
- source cues의 sequence, opaque cue ID, TC, text, speakers, content kind, cue note, screen text와 evidence IDs

다음은 제외한다.

- `content_digest`, `record_digest`
- document ID, revision ID와 supersedes revision ID
- lifecycle와 finalization record
- ref path, evidence base URI와 locator path

### Translation Content Digest Projection

`translation-document.v2.content_digest`에는 다음을 포함한다.

- schema version
- source·target locale
- source ref의 ID, revision ID와 content digest
- prepared context ref의 ID, revision ID와 content digest
- glossary ref의 ID, revision ID와 content digest
- target cues의 sequence, opaque cue ID, lineage, lineage status, TC, text, speakers, content kind, context/evidence IDs와 translation note
- open unresolved decisions
- decision status

다음은 제외한다.

- `content_digest`, `record_digest`
- document ID, revision ID와 supersedes revision ID
- lifecycle와 finalization record
- source/context/glossary path
- review artifact path 또는 effective review state
- SFW UI 열 표시, 선택 행, 재생 위치

### Asset Content Digest Projection

`content-context.v1.content_digest`에는 다음을 포함한다.

- schema version과 stage
- prepared stage인 경우 `prepared_from_source_ref`의 ID, revision ID와 content digest
- program, episode summary
- participants와 facts의 stable ID, scope, decision status, blocking flag와 evidence IDs
- evidence entries의 stable ID, resource digest, kind, media type와 PTS

`translation-glossary.v1.content_digest`에는 다음을 포함한다.

- schema version
- `prepared_from_source_ref`의 ID, revision ID와 content digest
- 모든 entry의 stable ID, source variants, preferred target, type, instruction, scope, decision status, blocking flag, case sensitivity, evidence IDs와 note
- evidence entries의 stable ID, resource digest, kind, media type와 PTS

두 asset content digest에서 다음은 제외한다.

- `content_digest`, `record_digest`
- asset ID, revision ID와 supersedes revision ID
- lifecycle status
- ref path와 UI 상태

### Review Content Digest Projection

`translation-review.v1.content_digest`에는 다음을 포함한다.

- binding
- coverage
- result
- findings와 resolution status

다음은 제외한다.

- `content_digest`, `record_digest`
- review ID와 `supersedes_review_id`
- reviewer execution ID, model, completed timestamp와 locator path

Review provenance는 record digest에는 포함되지만 candidate 또는 review semantic content digest를 바꾸지 않는다.

### Record Digest

`record_digest`는 저장 artifact 전체를 JCS로 직렬화하되 `record_digest` 자기 자신만 제외해 계산한다.

따라서 다음 변경은 content digest를 유지하고 record digest만 바꾼다.

- relative path rebasing
- lifecycle `draft → finalized`
- finalization actor·timestamp·acknowledgement 기록
- supersedes revision 기록

### Review Binding

AI review artifact는 다음 모두에 binding된다.

```text
translation_document_id
translation_revision_id
candidate_content_digest
source_document_id
source_revision_id
source_content_digest
prepared_context_asset_id
prepared_context_revision_id
prepared_context_content_digest
glossary_asset_id
glossary_revision_id
glossary_content_digest
review_profile_id
review_profile_version
```

review pass 기록 자체가 translation content digest를 바꾸지 않는다.

---

## Normative Source Contract: source-transcript.v2

필수 구조:

```json
{
  "schema_version": "source-transcript.v2",
  "document_id": "source-logical-id",
  "revision_id": "source-revision-id",
  "content_digest": "sha256",
  "record_digest": "sha256",
  "lifecycle_status": "draft",
  "source_locale": "ko-KR",
  "duration_ms": 1893770,
  "input_context_ref": {
    "asset_id": "episode-context",
    "revision_id": "input-context-revision",
    "content_digest": "sha256",
    "path": "../assets/context.input.json"
  },
  "evidence": {
    "base_uri": "../runs/run-id/",
    "entries": []
  },
  "cues": []
}
```

Source cue:

```json
{
  "sequence": 1,
  "cue_id": "opaque-source-id",
  "start_ms": 170,
  "end_ms": 1670,
  "text": "토크 라운지에 오신",
  "speakers": ["한지아"],
  "content_kind": "dialogue",
  "evidence_ids": []
}
```

Optional source cue fields:

```text
context_note
screen_text
```

규칙:

- `cue_id`는 opaque stable string이며 UUID일 필요가 없다.
- speakers는 draft와 finalized 모두 최소 1개다. 모르면 `unknown`을 명시한다.
- text는 canonical row 생성 시부터 nonblank다.
- start/end는 valid positive interval이어야 한다.
- 빈 임시 row를 저장하지 않는다. Add dialog가 필수 값을 받은 뒤 cue를 생성한다.
- evidence `base_uri`는 상대 URI를 기본으로 한다.
- evidence entry는 최소한 `evidence_id`, `kind`, `media_type`, `resource_digest`, `locator`를 가지며 frame이면 `pts_ms`도 가진다.
- `locator`는 `base_uri` 기준 상대 locator이며 source content digest에서 제외되고 record digest에는 포함된다.
- absolute file URI는 허용하되 nonportable warning을 표시하며 finalization 전에 선택적으로 rebase할 수 있다.

Source lifecycle은 `draft | finalized`다. finalized source revision은 다음 finalization record를 가진다.

```json
{
  "finalization": {
    "finalized_at": "2026-08-07T09:00:00Z",
    "finalized_by": "human-editor"
  }
}
```

Source lifecycle과 finalization record는 source content digest에서 제외되고 record digest에 포함된다.

---

## Normative Translation Contract: translation-document.v2

필수 구조:

```json
{
  "schema_version": "translation-document.v2",
  "document_id": "translation-logical-id",
  "revision_id": "translation-revision-id",
  "content_digest": "sha256",
  "record_digest": "sha256",
  "source_locale": "ko-KR",
  "target_locale": "en-US",
  "source_ref": {
    "document_id": "source-logical-id",
    "revision_id": "source-revision-id",
    "content_digest": "sha256",
    "path": "../source/source.json"
  },
  "prepared_context_ref": {
    "asset_id": "episode-context",
    "revision_id": "prepared-context-revision",
    "content_digest": "sha256",
    "path": "../assets/context.prepared.json"
  },
  "glossary_ref": {
    "asset_id": "episode-glossary",
    "revision_id": "glossary-revision",
    "content_digest": "sha256",
    "path": "../assets/glossary.json"
  },
  "lifecycle_status": "draft",
  "decision_status": "clear",
  "cues": [],
  "unresolved_decisions": []
}
```

Target cue:

```json
{
  "sequence": 1,
  "cue_id": "opaque-target-id",
  "source_cue_ids": ["opaque-source-id"],
  "lineage_status": "confirmed",
  "start_ms": 170,
  "end_ms": 3605,
  "text": "Welcome to Talk Lounge,",
  "speakers": ["한지아"],
  "content_kind": "dialogue",
  "context_ids": [],
  "evidence_ids": []
}
```

Optional target cue field:

```text
translation_note
```

Lineage status:

```text
unassigned
needs_confirmation
confirmed
```

관계:

- `unassigned`: `source_cue_ids=[]`여야 한다.
- `needs_confirmation`: `source_cue_ids`는 1개 이상이지만 provisional이다.
- `confirmed`: `source_cue_ids`는 1개 이상이고 finalization 가능 lineage다.
- draft save는 세 상태를 모두 허용한다.
- finalization은 모든 target cue가 `confirmed`여야 한다.

Unresolved decision:

```json
{
  "decision_id": "decision-id",
  "source_cue_ids": ["source-id"],
  "target_cue_ids": ["target-id"],
  "domain": "culture",
  "question": "애칭을 Rina로 유지할지 선택",
  "options": [
    {
      "option_id": "rina",
      "text": "Rina",
      "meaning": "실제 애칭",
      "risk": "공식 활동명과 다름"
    }
  ],
  "recommendation_id": "rina"
}
```

- `unresolved_decisions`에는 open decision만 둔다.
- 비어 있으면 `decision_status=clear`다.
- 하나 이상이면 `decision_status=needs_confirmation`이다.
- 두 필드가 불일치하면 save validation 오류다.
- 해결된 선택은 최종 cue text 또는 scoped glossary human selection에 반영하고 open list에서 제거한다.

Lifecycle status:

```text
draft
finalized
```

- Translator와 AI reviewer는 lifecycle을 finalized로 만들 수 없다.
- finalized revision 편집은 새 draft revision에서만 수행한다.

Finalization record는 lifecycle이 finalized일 때만 존재한다.

```json
{
  "finalization": {
    "finalized_at": "2026-08-07T09:00:00Z",
    "finalized_by": "human-editor",
    "review_acknowledgement": "passed",
    "finding_dispositions": []
  }
}
```

`review_acknowledgement`:

```text
passed
not_run_acknowledged
stale_acknowledged
failed_findings_disposed
legacy_status_imported
```

Failed review를 인간이 최종 disposition할 때는 다음을 기록한다.

```json
{
  "review_id": "review-id",
  "finding_id": "finding-id",
  "disposition": "human_accepted",
  "reason": "사용자가 현재 표현을 의도적으로 승인함"
}
```

- `finding_dispositions`는 finalization record 안에 저장한다.
- AI review artifact 자체는 immutable이며 finding resolution 때문에 in-place 수정하지 않는다.
- `legacy_status_imported`는 migration command만 사용할 수 있다.

---

## Normative Context Contract: content-context.v1

필수 구조:

```json
{
  "schema_version": "content-context.v1",
  "asset_id": "episode-context",
  "revision_id": "context-revision-id",
  "content_digest": "sha256",
  "record_digest": "sha256",
  "stage": "input",
  "lifecycle_status": "draft",
  "program": "토크라운지",
  "episode_summary": "...",
  "evidence": {
    "base_uri": "./evidence/",
    "entries": []
  },
  "participants": [],
  "facts": []
}
```

Stage:

```text
input
prepared
```

- `input` revision은 source 전사 전에 사용한다.
- `input` revision은 `prepared_from_source_ref`를 가지지 않는다.
- `prepared` revision은 finalized source를 기준으로 만들며 `prepared_from_source_ref`가 필수다.

Prepared ref:

```json
{
  "prepared_from_source_ref": {
    "document_id": "source-id",
    "revision_id": "source-revision",
    "content_digest": "sha256",
    "path": "../source/source.json"
  }
}
```

Participant:

```json
{
  "participant_id": "sera",
  "name_ko": "세라",
  "name_en": "Sera",
  "aliases": ["세라"],
  "role": "NOVA NOVA member",
  "evidence_ids": []
}
```

Fact:

```json
{
  "fact_id": "fact-id",
  "statement": "리나는 아리나의 실제 애칭이다.",
  "participant_ids": ["athena"],
  "scope_source_cue_ids": [],
  "evidence_ids": [],
  "decision_status": "human_confirmed",
  "blocking": false
}
```

Fact decision status:

```text
verified
human_confirmed
unresolved
```

Scope:

- `scope_source_cue_ids=[]`는 회차 전체 global scope다.
- nonempty list는 해당 source cue 범위에만 적용한다.

Ready 규칙:

- lifecycle은 `draft | ready`다.
- unresolved fact는 ready asset에 남을 수 있다.
- `unresolved + blocking=true`가 있으면 ready로 전환할 수 없다.
- unresolved fact는 translator/reviewer가 사실 추가의 근거로 사용할 수 없다.
- 삭제되는 participant/fact를 다른 fact 또는 target context ID가 참조하면 ready validation을 통과할 수 없다.

---

## Normative Glossary Contract: translation-glossary.v1

필수 구조:

```json
{
  "schema_version": "translation-glossary.v1",
  "asset_id": "episode-glossary",
  "revision_id": "glossary-revision-id",
  "content_digest": "sha256",
  "record_digest": "sha256",
  "lifecycle_status": "draft",
  "prepared_from_source_ref": {
    "document_id": "source-id",
    "revision_id": "source-revision",
    "content_digest": "sha256",
    "path": "../source/source.json"
  },
  "evidence": {
    "base_uri": "./evidence/",
    "entries": []
  },
  "entries": []
}
```

Entry:

```json
{
  "entry_id": "sera-name",
  "source_variants": ["세라"],
  "preferred_target": "Sera",
  "type": "person_name",
  "instruction": "Use the official stage name.",
  "scope_source_cue_ids": [],
  "decision_status": "verified",
  "blocking": false,
  "case_sensitive": true,
  "evidence_ids": [],
  "note": "Official English spelling"
}
```

Decision status:

```text
verified
human_selected
unresolved
```

지원 type:

```text
person_name
nickname
work_title
song_title
organization
place
food
technical_term
phonetic_expression
wordplay
contextual_translation
```

Scope:

- `scope_source_cue_ids=[]`는 global scope다.
- cue-specific entry가 global entry보다 우선한다.
- 같은 scope specificity에서는 `human_selected`가 `verified`보다 우선한다.
- 같은 우선순위에서 서로 다른 preferred target 또는 instruction이 충돌하면 ready validation 오류다.
- `human_selected`는 지정된 preferred target 또는 instruction에만 우선한다. 해당 cue의 의미, 문법, 자연스러움, TC와 다른 용어 검수를 면제하지 않는다.

Matching:

- source variant는 NFC exact substring으로 매칭한다.
- `case_sensitive=false`인 경우 Unicode case-fold 후 exact substring으로 매칭한다.
- stemming, 복수형 추론과 fuzzy matching은 자동 enforcement에 사용하지 않는다.
- 변형이 필요하면 `source_variants`에 명시한다.
- glossary lint는 자동 수정이 아니라 후보와 위반을 보고한다.

Ready 규칙:

- lifecycle은 `draft | ready`다.
- `unresolved + blocking=true` entry가 있으면 ready로 전환할 수 없다.
- unresolved nonblocking entry는 AI가 확정 기준으로 사용할 수 없다.
- source revision이 바뀌면 glossary는 새 source를 기준으로 다시 준비해야 한다.
- `preferred_target`은 `verified`와 `human_selected` entry에서 필수다.
- `unresolved` entry는 아직 선택지가 없으면 `preferred_target`을 생략할 수 있다.

예시:

```json
{
  "entry_id": "gae-pala",
  "source_variants": ["개팔아", "개 팔아"],
  "preferred_target": "Gae-pala",
  "type": "phonetic_expression",
  "instruction": "Do not translate semantically.",
  "scope_source_cue_ids": ["source-352", "source-358"],
  "decision_status": "human_selected",
  "blocking": true,
  "case_sensitive": true,
  "evidence_ids": [],
  "note": "발성 연습용 음가"
}
```

```json
{
  "entry_id": "city-as-la",
  "source_variants": ["저희 도시"],
  "preferred_target": "LA",
  "type": "contextual_translation",
  "instruction": "Use the user-selected specific city in this scope.",
  "scope_source_cue_ids": ["source-242"],
  "decision_status": "human_selected",
  "blocking": false,
  "case_sensitive": true,
  "evidence_ids": [],
  "note": "사용자가 번역 과정에서 선택함"
}
```

---

## Normative Review Contract: translation-review.v1

AI review는 별도 artifact다.

```json
{
  "schema_version": "translation-review.v1",
  "review_id": "review-id",
  "content_digest": "sha256",
  "record_digest": "sha256",
  "binding": {
    "translation_document_id": "translation-id",
    "translation_revision_id": "translation-revision",
    "candidate_content_digest": "sha256",
    "source_document_id": "source-id",
    "source_revision_id": "source-revision",
    "source_content_digest": "sha256",
    "prepared_context_asset_id": "context-id",
    "prepared_context_revision_id": "context-revision",
    "prepared_context_content_digest": "sha256",
    "glossary_asset_id": "glossary-id",
    "glossary_revision_id": "glossary-revision",
    "glossary_content_digest": "sha256",
    "review_profile": {
      "profile_id": "en-us-editorial-review",
      "version": "2.0"
    }
  },
  "coverage": {
    "source_cue_ids": ["source-1", "source-2"],
    "target_cue_ids": ["target-1", "target-2"],
    "target_boundary_pairs": [
      ["target-1", "target-2"]
    ],
    "glossary_entry_ids": ["sera-name"]
  },
  "result": "failed",
  "findings": [
    {
      "finding_id": "finding-id",
      "affected_source_cue_ids": ["source-1", "source-2"],
      "affected_target_cue_ids": ["target-1", "target-2"],
      "domain": "naturalness",
      "severity": "major",
      "blocking": true,
      "problem": "한 발화가 cue 경계에서 문법적으로 끊긴다.",
      "required_change": "앞 cue 종결과 다음 cue 시작을 하나의 자연스러운 문장으로 수정한다.",
      "resolution_status": "open"
    }
  ],
  "provenance": {
    "review_kind": "independent_ai",
    "reviewer_execution_ids": ["execution-id"],
    "model": "model-id",
    "completed_at": "2026-08-07T09:00:00Z"
  }
}
```

Binding은 translation/source/context/glossary identity와 review profile을 모두 가진다.

Review artifact는 immutable이다. 같은 binding/profile에 새 review를 발행할 때는 top-level optional `supersedes_review_id`로 이전 authoritative review를 가리킨다. 같은 binding/profile에서 superseded되지 않은 review가 둘 이상이면 effective review state는 `conflict` diagnostic이며 finalization을 차단한다.

Coverage는 current binding의 required source cue, target cue, 모든 인접 target boundary와 적용 glossary entry를 명시한다.

Finding은 exact affected source/target ID, domain, severity, blocking, problem, required change와 resolution status를 가진다.

Finding의 stored `resolution_status`는 review 생성 시 `open`이다. Review artifact는 immutable이다.

Effective resolution은 다음처럼 계산한다.

```text
open
resolved_by_revision
human_accepted
```

- current candidate가 새 revision으로 바뀌고 해당 finding이 current review에 재발하지 않으면 `resolved_by_revision`이다.
- finalization record가 exact review/finding ID의 disposition을 가지면 `human_accepted`다.
- effective resolution은 old review artifact bytes를 바꾸지 않는다.

Provenance:

```json
{
  "review_kind": "independent_ai",
  "reviewer_execution_ids": ["execution-id"],
  "model": "model-id",
  "completed_at": "2026-08-07T09:00:00Z"
}
```

Review kind:

```text
independent_ai
isolated_self_review
human_review
```

Result:

- `passed`: required coverage가 완전하고 open blocking finding이 없다.
- `failed`: open blocking finding이 하나 이상이거나 required coverage가 불완전하다.
- artifact의 result와 findings/coverage가 불일치하면 validation 오류다.

Effective review state 계산:

- `not_run`: 현재 binding과 관련된 review artifact가 없다.
- `passed`: 현재 binding과 정확히 일치하는 passed artifact가 있다.
- `failed`: 현재 binding과 정확히 일치하는 failed artifact가 있다.
- `stale`: 이전 review artifact는 있으나 현재 binding과 일치하지 않는다.
- 동일 binding/profile에서 authoritative review가 둘 이상 충돌하면 저장 enum이 아니라 `review_conflict` diagnostic으로 계산하고 finalization을 차단한다.

AI review 없이 human finalization은 가능하다. 다만 not_run/stale은 명시적 acknowledgement가 필요하다. failed 상태에서는 open blocking finding 각각이 새 revision으로 해결되거나 `human_accepted` disposition을 받아야 한다. blanket bypass는 허용하지 않는다.

---

## State Model

### Stored State

Source:

```text
lifecycle_status: draft | finalized
```

Translation:

```text
lifecycle_status: draft | finalized
decision_status: clear | needs_confirmation
```

Assets:

```text
lifecycle_status: draft | ready
```

AI review state는 translation에 저장하지 않고 review artifact와 현재 binding으로 계산한다.

### Transition Table

| Event | Lifecycle | Decision | Effective Review | Result |
|---|---|---|---|---|
| Translator candidate assembly | `draft` | open decision 유무에 따라 결정 | `not_run` | editor-ready draft 생성 |
| Unresolved decision 추가 | `draft` | `needs_confirmation` | 기존 review가 있으면 `stale` | finalization 차단 |
| Unresolved decision 해결 | `draft` | open list가 비면 `clear` | candidate가 바뀌므로 `stale` | 재검수 가능 |
| Human metadata/text/TC edit | `draft` | 기존 상태 유지 또는 새 decision 생성 | `stale` | content digest 갱신 |
| AI review pass | 문서 상태 변화 없음 | 변화 없음 | `passed` | review artifact 생성 |
| AI review fail | 문서 상태 변화 없음 | 변화 없음 | `failed` | finding 수정 필요 |
| Human finalize + passed review | `finalized` | `clear` | `passed` | finalization record 생성 |
| Human finalize + no review | `finalized` | `clear` | `not_run` | explicit acknowledgement 필수 |
| Human finalize + stale review | `finalized` | `clear` | `stale` | explicit acknowledgement 필수 |
| Human finalize + failed review | `finalized` | `clear` | `failed` | 모든 blocking finding disposition 필수 |
| Finalized revision 편집 시작 | 새 revision `draft` | 이전 content 기준 | old review는 새 revision에서 `stale` | old finalized file 보존 |
| Source/context/glossary identity 변경 | 저장 lifecycle 자동 변경 없음 | 변화 없음 | `stale` 또는 missing ref | finalization validation 차단 |

### Review Stale Matrix

| Change | Translation content digest | Review state | Pair view | Finalization |
|---|---:|---|---|---|
| source/context/glossary path만 변경 | 유지 | 유지 | identity 확인 후 정상 | 가능 |
| source content digest 변경 | 기존 translation은 old ref 유지 | old binding 기준 역사적 유효, 새 source와는 stale | old source가 없으면 unresolved ref | 새 source 기준 finalization 불가 |
| prepared context digest 변경 | ref를 업데이트하기 전 기존 binding 유지 | 새 asset 기준으로는 stale | pair 자체는 가능 | 새 asset 사용 시 재검수/ack 필요 |
| glossary digest 변경 | 동일 | 새 glossary 기준 stale | pair 가능 | 새 glossary 사용 시 재검수/ack 필요 |
| target text/TC/speaker/lineage/note 변경 | 변경 | stale | 정상 | 재검증 필요 |
| lifecycle/finalization만 변경 | 유지 | 유지 | 정상 | record digest만 변경 |
| review artifact 추가 | 유지 | passed/failed 계산 | 정상 | policy에 따라 가능 |

`stale`은 한 종류의 저장 enum으로 뭉치지 않는다. UI diagnostics는 최소한 다음을 구분한다.

```text
missing_source_ref
source_identity_mismatch
missing_context_ref
context_identity_mismatch
missing_glossary_ref
glossary_identity_mismatch
review_not_run
review_failed
review_stale
review_conflict
nonportable_evidence_locator
```

---

## SFW Canonical Session Architecture

SFW canonical JSON 편집은 일반 `SubtitleFormat` load/save만으로 구현하지 않는다.

```text
CanonicalSubtitleSession
├─ canonical document/asset refs and lifecycle
├─ canonical cue rows
│  ├─ opaque cue ID
│  ├─ source lineage and lineage status
│  ├─ speakers/content kind
│  ├─ context/evidence IDs
│  └─ notes
├─ source pair projection
├─ full canonical undo snapshots
├─ dirty/diagnostic state
└─ derived Subtitle/SubtitleLineViewModel projection
```

규칙:

- SFW 내부 `Guid`는 UI row identity로만 사용한다.
- canonical `cue_id`는 별도 opaque string 필드로 보존한다.
- `Paragraph.Id` 또는 `SubtitleLineViewModel.Id`를 canonical cue ID로 재사용하지 않는다.
- document-level refs, lifecycle과 finalization은 session에 보존한다.
- canonical JSON save는 전용 codec과 mutation service를 사용한다.
- 일반 Subtitle/Paragraph save 경로는 ASS·SBV 등 export projection에 사용한다.

### Undo/Redo

- undo snapshot은 cue text/TC뿐 아니라 canonical cue ID, lineage, lineage status, speakers, content kind, context/evidence IDs, notes, refs, lifecycle과 unresolved decisions를 포함한다.
- metadata-only 수정도 undo entry를 생성한다.
- undo/redo 후 canonical content digest와 diagnostics를 다시 계산한다.

### Dirty Detection

- canonical session dirty detection은 전체 canonical record projection을 기준으로 한다.
- cue ID, lineage, context/evidence IDs, note, refs와 lifecycle 변경도 dirty로 처리한다.
- UI 열 표시, 선택 행과 영상 재생 위치는 dirty에 포함하지 않는다.

### OriginalText Safety

canonical translation mode에서 `OriginalText`는 source lineage로 계산한 read-only projection이다.

- 원문 textbox는 read-only다.
- copy/swap/merge/bilingual command처럼 `OriginalText`를 직접 바꾸는 기존 명령은 canonical translation mode에서 비활성화하거나 target-only 동작으로 우회한다.
- target split·merge 후 OriginalText는 source lineage에서 다시 계산한다.
- projection text는 translation JSON에 저장하지 않는다.

### Atomic Save

1. current session을 canonical artifact로 직렬화한다.
2. save validation을 수행한다.
3. content/record digest를 계산한다.
4. 임시 파일에 쓴다.
5. fsync 및 atomic replace를 수행한다.
6. 실패 시 기존 파일을 보존한다.

Save As는 ref path와 relative evidence locator를 새 위치 기준으로 rebase하고 record digest를 갱신한다. semantic identity가 같으면 content digest는 유지한다.

---

## SFW Pair View

- translation target cue 하나가 SFW 편집 행 하나다.
- `Text`는 target cue의 영어 text다.
- `OriginalText`는 target cue의 `source_cue_ids`로 source cue를 조회해 source 순서대로 줄바꿈 결합한 파생 값이다.
- pair 매칭은 source cue ID만 사용한다.
- 여러 source cue → target 1개: 원어 열에 모든 source text를 순서대로 표시한다.
- source 1개 → target 여러 개: 각 target 행에 같은 source text를 반복 표시한다.
- N:M은 각 target cue의 own lineage만 표시한다.
- source context note는 lineage source cue의 note를 순서대로 파생해 별도 선택 열에 표시한다.
- target translation note는 별도 선택 열에 표시한다.
- source ID가 없거나 source ref가 깨지면 추정 매칭하지 않고 mapping error를 표시한다.

기본 열:

```text
sequence | start | end | duration | speaker | original text | translation
```

선택 열:

```text
cue_id
source_cue_ids
lineage_status
content_kind
source_context_note
translation_note
screen_text
context_ids
evidence_ids
```

문서 정보 영역:

```text
schema version
document/revision ID
content/record digest
lifecycle/decision status
source/context/glossary refs
effective review state
finalization record
```

---

## Field Visibility and Seek

- cue field 열을 표시·숨김할 수 있다.
- source와 translation 문서 종류별로 열 설정을 기억한다.
- 숨긴 필드도 저장할 때 그대로 보존한다.
- 검색은 보이는 text뿐 아니라 hidden canonical cue metadata를 포함한다.
- 검색 결과를 선택하면 해당 row를 선택하고 영상 위치를 cue start로 이동한다.
- document-level ref와 digest도 별도 검색 대상이 될 수 있다.
- 배열은 grid에서 구분자로 축약 표시하고 metadata editor에서 전체 편집한다.
- 계산 field인 sequence, content digest와 record digest는 read-only다.

---

## Cue Mutation Rules

### Source Revision Policy

MVP는 source revision rebase map을 제공하지 않는다.

- finalized source revision은 immutable이다.
- finalized source를 수정하면 새 draft revision을 만든다.
- old source revision과 이를 참조하는 translation은 그대로 보존한다.
- 새 source revision을 기준으로 prepared context, glossary와 translation을 새로 만든다.
- 기존 translation의 source lineage를 새 source ID로 자동 rebase하지 않는다.
- source 구조 변경 뒤 기존 translation을 새 source 기준으로 finalization할 수 없다.

이 정책은 split·merge·delete 시 translation, glossary scope, context fact scope, unresolved와 review finding 전체를 자동 재작성하는 복잡성을 MVP에서 피한다.

### Add Source Cue

- Add dialog가 start, end, text, speaker와 content kind를 받은 뒤 row를 생성한다.
- blank text 또는 invalid TC의 임시 source row를 canonical file에 저장하지 않는다.
- 새 opaque source cue ID를 생성한다.
- sequence를 전체 재계산한다.
- context note, screen text와 evidence IDs는 빈 값으로 시작한다.

### Add Target Cue

일반 add와 split을 구분한다.

- 일반 add는 새 target ID를 생성한다.
- text, TC, speaker와 content kind는 add dialog에서 입력한다.
- `source_cue_ids=[]`, `lineage_status=unassigned`로 시작한다.
- 인접 cue lineage를 조용히 복사하지 않는다.
- 사용자가 source cue selector로 lineage를 지정하고 확인하면 `confirmed`가 된다.
- draft 저장은 가능하지만 finalization은 차단한다.

### Split Source Cue

- 앞 cue는 기존 source ID를 유지한다.
- 뒤 cue는 새 source ID를 가진다.
- text와 TC는 SFW split 결과를 사용한다.
- speaker, content kind, context note, screen text와 evidence IDs를 양쪽에 복사한다.
- split 뒤 metadata를 사용자가 수정할 수 있다.

### Split Target Cue

- 앞 cue는 기존 target ID를 유지한다.
- 뒤 cue는 새 target ID를 가진다.
- text와 TC는 SFW split 결과를 사용한다.
- speakers, content kind, context/evidence IDs와 translation note를 양쪽에 복사한다.
- 기존 lineage가 source 1개이고 `confirmed`이면 양쪽 모두 같은 lineage를 `confirmed`로 복사한다.
- 기존 lineage가 source 여러 개이면 양쪽에 provisional full lineage를 복사하고 두 cue 모두 `needs_confirmation`으로 둔다.
- 사용자가 각 cue lineage를 확인하거나 좁히기 전에는 finalization할 수 없다.
- 기존 cue를 참조하던 open unresolved decision은 두 target ID를 모두 참조하도록 갱신한다.

### Merge Source Cues

- 인접 source cue만 병합한다.
- 첫 cue ID를 유지하고 나머지 ID를 제거한다.
- TC는 첫 시작부터 마지막 종료까지 사용한다.
- speakers, evidence IDs는 source 순서 보존 중복 제거 합집합으로 만든다.
- distinct context notes는 source 순서대로 newline 결합하고 중복 제거한다.
- distinct screen text도 source 순서대로 newline 결합하고 중복 제거한다.
- content kind가 다르면 병합을 차단한다.

### Merge Target Cues

- 인접 target cue만 병합한다.
- 첫 target ID를 유지한다.
- source cue IDs, speakers, context/evidence IDs는 순서 보존 중복 제거 합집합으로 만든다.
- content kind가 다르면 병합을 차단한다.
- 모든 input lineage가 confirmed면 merged lineage도 confirmed다.
- 하나라도 needs_confirmation이면 merged lineage는 needs_confirmation이다.
- 모든 input lineage가 unassigned이면 merged lineage는 unassigned이고 source cue IDs는 빈 값이다.
- unassigned cue와 known lineage cue가 섞이면 known source IDs를 provisional union으로 유지하고 merged lineage를 needs_confirmation으로 둔다. 누락된 부분을 확인하기 전에는 finalization할 수 없다.
- translation note가 모두 비어 있으면 비운다.
- 하나만 있으면 유지한다.
- distinct nonempty note가 여러 개면 target 순서대로 newline 결합하고 중복 제거한다.
- open unresolved decision의 여러 target ID는 retained first target ID로 치환한다.

### Delete Target Cue

- cue 삭제 뒤 sequence를 재계산한다.
- 해당 target을 참조하는 open unresolved decision이 있으면 삭제를 차단하거나 decision을 함께 제거할지 사용자에게 명시적으로 묻는다.
- review finding은 별도 artifact에 남고 content digest mismatch로 stale이 된다. old review artifact를 재작성하지 않는다.

### Delete Source Cue

- source draft revision 안에서만 수행한다.
- cue 삭제 뒤 sequence를 재계산한다.
- evidence entry 자체는 다른 cue가 참조할 수 있으므로 자동 삭제하지 않는다.
- prepared asset이나 translation이 이미 존재하는 finalized source revision을 in-place delete하지 않는다.

---

## Validation Levels

하나의 validator로 draft save와 finalization을 동시에 강제하지 않는다.

### 1. Schema / Save Validation

모든 저장에서 차단한다.

- JSON schema version과 required field
- NFC string과 canonical number
- unique ID와 contiguous sequence
- valid TC interval과 ordering
- nonblank cue text와 valid speakers/content kind
- lineage status와 source cue IDs의 내부 일관성
- known context/evidence reference
- decision status와 open decision list 일관성
- digest 계산 가능성
- unknown field와 unsupported schema

Draft translation은 다음을 허용한다.

- `lineage_status=unassigned`
- provisional `needs_confirmation`
- source coverage 부족
- open unresolved decisions
- output profile warning

### 2. Draft Diagnostics

저장을 허용하되 UI에서 표시한다.

- lineage unassigned 또는 needs confirmation
- source coverage 부족·중복·순서 문제
- source/context/glossary missing 또는 identity mismatch
- review not run/failed/stale
- context/glossary unresolved nonblocking item
- line count, line length와 CPS 경고
- cross-cue punctuation/quotation 후보
- glossary mismatch 후보
- nonportable evidence locator

### 3. Finalization Validation

human finalization을 차단한다.

Source:

- 모든 cue가 schema/save validation 통과
- evidence reference 무결성
- input context identity 확인 또는 명시적 missing-context acknowledgement 정책
- open blocking source issue 없음

Translation:

- source/prepared context/glossary ref identity가 모두 해결됨
- 모든 target cue lineage가 nonempty `confirmed`
- 모든 translatable source cue가 최소 한 target cue에 coverage됨
- source lineage order와 timing envelope 규칙 통과
- open unresolved decision 없음
- context/evidence reference 무결성
- output profile의 blocking rule 통과
- current effective review state에 맞는 finalization acknowledgement 존재
- `review_conflict` diagnostic 없음

Lineage 없는 target draft를 저장할 수 있지만 finalization할 수 없다는 계약을 이 세 단계로 구현한다.

---

## Translation Preparation Stage

입력:

- finalized source revision
- source가 사용한 input context revision
- 기존 prepared context/glossary가 있으면 해당 revision

산출물:

- `content-context.v1 stage=prepared`
- `translation-glossary.v1`

수집 대상:

- 출연자, 별칭과 관계
- 공식 영문 활동명
- 프로그램, 작품과 곡 제목
- 회사, 조직과 장소
- 음식과 문화 고유어
- 전문 용어
- 말장난과 농담의 전제
- 의미 번역을 하면 안 되는 음가·발성
- cue context note의 번역 지침 후보
- 사용자가 이미 선택한 번역

AI는 source와 input context를 읽어 draft asset을 만든다. 공식 표기가 있는 항목은 가능한 한 공식 자료로 확인한다. 확인되지 않은 항목과 선택이 필요한 표현만 사용자에게 제시한다.

Ready gate:

- prepared context와 glossary 모두 finalized source identity에 binding되어야 한다.
- blocking unresolved item이 없어야 한다.
- 같은 scope의 glossary conflict가 없어야 한다.
- dangling participant/fact/entry scope가 없어야 한다.
- empty glossary도 ready 상태를 명시할 수 있다.

Translator와 reviewer는 같은 source, prepared context와 glossary revision을 사용한다.

---

## Translator Role and Completion Criteria

Translator의 목적은 스키마를 통과하는 직역 초안이 아니라 사람이 SFW에서 바로 검수할 수 있는 editor-ready en-US subtitle draft를 만드는 것이다.

Translator는 다음 순서로 작업한다.

1. owned source cue와 앞뒤 문맥을 읽고 cue 경계와 무관하게 전체 발화를 복원한다.
2. 화자, 청자, 생략된 주어·목적어·지시 대상, 성별과 관계를 확인한다.
3. prepared context와 glossary의 verified·human-selected 결정을 적용한다.
4. human-selected 표현은 지정된 scope와 표현에만 적용하고 cue 나머지는 독립적으로 번역한다.
5. 원문 사실·감정·농담과 말투를 보존한 자연스러운 en-US 문장을 작성한다.
6. 영어 어순과 읽기 속도에 맞게 target cue를 재분절한다.
7. source lineage와 speaker를 정확히 부여한다.
8. target 자막만 처음부터 끝까지 연속해서 읽고 직역체, 번역체, 직접화법, 대명사와 cue 경계 문장 연결을 스스로 수정한다.
9. 책임 있게 결정할 수 없는 항목만 unresolved decision으로 남긴다.
10. save validation을 통과한 `draft`를 반환한다.

완료 보고에는 최소한 다음이 포함된다.

- owned source와 emitted target count
- glossary 적용/위반 수
- source context note 점검 수
- referent/speaker 점검 완료 여부
- target-only continuity self-read 완료 여부
- unassigned/needs-confirmation lineage 수
- unresolved decision 수
- save validation 결과와 draft diagnostics 요약

구조 validator 통과만으로 translation 완료로 간주하지 않는다.

---

## Reviewer Role and Completion Criteria

Reviewer의 목적은 translator가 선언한 구조 준수를 확인하는 것이 아니라 실제 자막 결함을 발견하는 것이다.

Reviewer는 source, candidate, prepared context와 glossary를 함께 받는다. Translator의 내부 rationale은 받지 않아도 되지만 human-selected glossary decision은 반드시 받는다.

Reviewer는 semantic chunk별로 다음 영역을 확인한다.

1. 의미 누락, 추가, 반전과 강도 변화
2. 화자, 청자, 주어·목적어·지시 대상과 성별
3. 이름, 작품명, 문화 표현과 glossary 준수
4. 미국 영어의 자연스러움과 캐릭터 말투
5. 하나의 발화가 여러 cue로 나뉜 경우의 문장 연결
6. 분절, TC와 가독성

Finding은 exact affected source/target ID, problem, required change, domain, severity와 blocking 여부를 가진다.

Review coverage 요건:

- chunk review union이 모든 target cue를 포함해야 한다.
- 모든 target cue와 다음 cue 사이 boundary를 한 번 이상 검사해야 한다.
- source coverage manifest가 있어야 한다.
- glossary entry 적용 범위를 검사해야 한다.
- 변경된 chunk는 current digest 기준으로 다시 검수해야 한다.
- 마지막 global audit은 이름·용어 일관성과 chunk 경계 continuity에 집중한다.

검토한 ID 목록만 제출하는 것으로 clean review를 증명할 수 없다.

별도 reviewer 실행이 가능하면 translator와 다른 컨텍스트를 사용한다. 사용량 제약이 있을 때는 분리된 self-review 컨텍스트를 허용하되 provenance를 `isolated_self_review`로 표시한다. 최종 권한은 사람에게 있다.

---

## End-to-End Workflow

### 1. 콘텐츠 시작

- 사용자가 URL과 회차 정보를 제공한다.
- `content-context.v1 stage=input` draft를 만든다.
- 알려진 출연자, 프로그램, 회차와 화자 식별 참고 정보를 기록한다.

### 2. AI 전사

- input context ref를 가진 `source-transcript.v2` draft를 생성한다.
- save validation을 수행한다.

### 3. SFW 인간 원어 검수

- SFW canonical source session으로 source v2를 연다.
- text, TC, speaker, note와 screen text를 수정한다.
- 필요하면 add/delete/split/merge를 수행한다.
- metadata-aware undo/redo와 dirty detection을 제공한다.
- finalization validation 후 사람이 source revision을 finalized로 만든다.

### 4. 번역 준비

- finalized source를 기준으로 prepared context와 glossary draft를 만든다.
- blocking unresolved 선택만 사용자에게 묻는다.
- 두 asset을 ready로 만든다.

### 5. AI 번역

- source, prepared context와 glossary를 semantic chunk 단위로 전달한다.
- Translator가 editor-ready target draft를 만든다.
- deterministic assembly는 translation v2 `draft`를 만든다. finalized를 부여하지 않는다.

### 6. AI 검수

- current binding 기준 chunk review와 global audit을 수행한다.
- `translation-review.v1` artifact를 생성한다.
- translation lifecycle은 바꾸지 않는다.

### 7. SFW 인간 번역 검수

- SFW canonical translation session으로 translation v2를 연다.
- source lineage 기반 pair view를 표시한다.
- target text, TC, speaker, lineage와 note를 편집한다.
- add/delete/split/merge를 수행할 수 있다.
- 변경하면 기존 review는 effective stale이 된다.
- finalization validation과 review acknowledgement 후 사람이 finalized revision을 만든다.

### 8. 전달 형식 생성

- finalized translation revision에서 ASS, SBV, SRT, VTT와 CSV를 생성한다.
- 검수 CSV의 source text도 source lineage로 파생한다.
- 모든 export는 같은 content digest에서 생성됐음을 receipt에 기록한다.

---

## User Stories

1. 원어 편집자로서 source v2 JSON을 SFW에서 바로 열고 같은 스키마로 저장하고 싶다.
2. 번역 편집자로서 translation v2 하나를 열어 원어와 번역어를 나란히 보고 싶다.
3. 번역 편집자로서 552 source와 505 target을 source ID로 정확히 pair하고 싶다.
4. 번역 편집자로서 여러 source→한 target, 한 source→여러 target, N:M 대응을 확인하고 싶다.
5. 번역 편집자로서 OriginalText가 source JSON에서 파생되고 실수로 수정되지 않길 원한다.
6. 편집자로서 cue ID, lineage, note와 evidence 열을 필요할 때만 표시하고 싶다.
7. 편집자로서 숨겨진 metadata를 검색해 row와 영상 위치로 이동하고 싶다.
8. 편집자로서 metadata-only 변경도 저장 필요 상태와 undo에 포함되길 원한다.
9. 편집자로서 canonical cue ID가 SFW 내부 Guid와 섞이지 않길 원한다.
10. 편집자로서 새 source cue를 유효한 TC·text와 함께 추가하고 싶다.
11. 편집자로서 새 target cue를 추가한 뒤 source lineage를 명시적으로 지정하고 싶다.
12. 편집자로서 target split 시 single-source lineage는 안전하게 복사되고 multi-source lineage는 확인 대상으로 표시되길 원한다.
13. 편집자로서 target merge 시 lineage, speakers, notes와 context/evidence IDs가 결정적으로 합쳐지길 원한다.
14. 편집자로서 finalized revision을 고칠 때 이전 파일을 덮어쓰지 않고 새 revision을 만들고 싶다.
15. 운영자로서 source가 바뀌면 기존 translation이 어느 source revision에 묶였는지 알고 싶다.
16. 운영자로서 path 변경과 semantic content 변경을 다른 digest로 구분하고 싶다.
17. 운영자로서 Save As 뒤 relative ref가 자동 rebase되길 원한다.
18. 운영자로서 absolute evidence locator가 프로젝트 이동성을 깨면 경고받고 싶다.
19. 번역 준비 담당자로서 initial context와 prepared context를 구분하고 싶다.
20. 번역 준비 담당자로서 glossary entry마다 stable ID, scope와 decision status를 기록하고 싶다.
21. 사용자로서 `LA`처럼 내가 선택한 번역을 특정 scope에서 보존하고 싶다.
22. Reviewer로서 human-selected 표현만 존중하고 cue의 다른 오류는 계속 검수하고 싶다.
23. Translator로서 전체 발화를 복원한 뒤 자연스러운 영어로 재분절하고 싶다.
24. Translator로서 고유명사, 음가와 관계 정보를 같은 prepared asset에서 받고 싶다.
25. Reviewer로서 chunk별 실제 finding과 required change를 반환하고 싶다.
26. 운영자로서 AI review가 translation JSON digest를 순환 변경하지 않길 원한다.
27. 사용자로서 AI review 없이도 경고를 확인하고 인간 finalization할 수 있길 원한다.
28. 사용자로서 failed review의 blocking finding은 개별 disposition 없이 blanket 무시되지 않길 원한다.
29. 개발자로서 Python과 C#이 같은 JCS golden digest vector를 통과하길 원한다.
30. 개발자로서 v1 데이터를 명시적 migration command로 v2에 옮기고 싶다.
31. 품질 담당자로서 NOVA NOVA 오류 유형을 회귀 fixture로 사용하고 싶다.
32. 운영자로서 final JSON 하나에서 ASS·SBV·CSV를 일관되게 생성하고 싶다.

---

## Implementation Phases

하나의 구현 이슈로 전체 범위를 동시에 시작하지 않는다.

### Phase 0 — Contract Foundation

- source v2, translation v2, context v1, glossary v1, review v1 JSON Schema
- normative full examples
- JCS canonicalization profile
- Python/C# golden digest vectors
- lifecycle/decision transition tests
- dependency and stale matrix tests
- v1→v2 migration command와 receipt
- evidence locator portability rule

Gate: Phase 0 schema와 golden vectors가 승인되기 전 SFW write-save 구현 이슈를 시작하지 않는다.

### Phase 1 — SFW Canonical Translation Review MVP

- source v2 read-only load
- translation v2 canonical session read/write
- source ref resolution과 pair projection
- target text, TC, speaker, lineage와 note 편집
- metadata-aware undo/redo와 dirty detection
- canonical OriginalText mutation guard
- atomic save, Save As ref rebasing
- human finalization과 review acknowledgement
- ASS/SBV/CSV 기본 export

이 단계에서는 source 구조 편집과 context/glossary UI를 요구하지 않는다.

### Phase 2 — Source Round-Trip and Cue Mutation

- source v2 canonical read/write
- source/target add, delete, split, merge
- mutation metadata 규칙
- finalized revision copy-on-edit
- finalization diagnostics

### Phase 3 — Translation Preparation and AI Review

- prepared context/glossary draft·ready workflow
- glossary conflict와 matching lint
- translator R&R 적용
- review artifact, coverage와 global audit
- review effective state 표시

### Phase 4 — Advanced Review Convenience

- 선택 열 UI
- hidden metadata search와 video seek
- context/glossary 상세 editor
- 대규모 fixture 성능 최적화
- 추가 export profile

---

## Acceptance Criteria

### Phase 0

1. 신규 canonical schema는 source v2와 translation v2를 사용한다.
2. legacy v1과 v2를 schema version으로 명확히 구분한다.
3. migration command가 NOVA NOVA v1 fixture를 v2 프로젝트로 변환하고 입력을 보존한다.
4. content digest와 record digest가 독립적으로 계산된다.
5. lifecycle 또는 path만 바꾸면 content digest는 유지되고 record digest만 바뀐다.
6. cue text·TC·speaker·lineage·note 또는 source/context/glossary identity를 바꾸면 translation content digest가 바뀐다.
7. Python과 C#이 같은 JCS golden vector bytes와 SHA-256을 생성한다.
8. source→prepared context/glossary→translation 의존성에 digest cycle이 없다.
9. review artifact가 candidate/source/context/glossary/profile binding을 모두 가진다.
10. not_run/passed/failed/stale effective review state와 review_conflict diagnostic이 transition table과 일치한다.
11. absolute evidence base URI migration/rebase가 digest 검증 후 상대 URI로 변환된다.

### Phase 1

12. SFW가 source v2를 읽고 translation v2 pair source로 사용할 수 있다.
13. SFW가 translation v2를 열고 같은 schema로 atomic save할 수 있다.
14. source 552 / target 505 fixture의 모든 target row가 source ID 기반으로 pair된다.
15. N:1, 1:N, N:M pair projection을 지원한다.
16. translation JSON에 source text 복사본을 저장하지 않는다.
17. canonical opaque cue ID가 UI Guid round-trip과 무관하게 보존된다.
18. target text, TC, speaker, lineage, note 수정이 undo/redo된다.
19. metadata-only 수정이 dirty 상태를 만든다.
20. canonical translation mode에서 OriginalText를 바꾸는 기존 명령이 차단되거나 projection 재계산으로 우회된다.
21. Save As 뒤 relative source/context/glossary ref가 새 위치에 맞게 rebase된다.
22. Save As path 변경 전후 content digest가 같다.
23. lineage unassigned target은 draft 저장 가능하지만 finalization 불가다.
24. 사람이 명시적으로 완료해야 lifecycle이 finalized가 된다.
25. AI review not_run/stale 상태 finalization에는 명시적 acknowledgement가 필요하다.
26. failed review blocking finding은 개별 disposition 없이는 finalization할 수 없다.
27. JSON에서 생성한 ASS·SBV·CSV의 target TC와 text가 동일하다.
28. unsupported schema를 field loss와 함께 덮어쓰지 않는다.
29. save validation 실패 시 기존 JSON이 손상되지 않는다.

### Phase 2

30. source/target add가 unique opaque ID를 만든다.
31. 일반 target add는 lineage를 자동 복사하지 않고 unassigned로 시작한다.
32. source single cue lineage target split은 confirmed lineage를 양쪽에 복사한다.
33. multi-source lineage target split은 양쪽을 needs_confirmation으로 만든다.
34. target merge는 source lineage와 metadata를 순서 보존 union으로 만든다.
35. distinct translation note merge 규칙이 결정적으로 적용된다.
36. source merge는 context note와 screen text를 순서대로 결합한다.
37. content kind conflict merge는 차단된다.
38. unresolved decision ref가 split/merge/delete 규칙에 따라 갱신 또는 차단된다.
39. finalized revision 편집은 새 draft revision과 supersedes ref를 만든다.
40. finalized source 구조 변경 뒤 old translation을 새 source에 자동 rebase하지 않는다.

### Phase 3

41. prepared context와 glossary가 finalized source identity를 참조한다.
42. blocking unresolved item이 있으면 asset ready가 차단된다.
43. cue-specific glossary가 global entry보다 우선한다.
44. 같은 scope에서 human_selected가 verified보다 우선한다.
45. human-selected `LA` fixture는 지정 표현에서 reviewer가 임의로 되돌리지 않는다.
46. 같은 cue의 다른 의미·문법·TC 오류는 계속 finding 대상이다.
47. `Sera`, `Chanelle`, `Ki Bo Bae`, 작품명 fixture가 glossary 기준으로 일관되게 적용된다.
48. `개팔아` semantic translation candidate가 lint 또는 reviewer finding으로 차단된다.
49. 관계 농담의 화자·청자·관계 손실이 finding으로 반환된다.
50. review coverage union이 모든 target cue와 인접 boundary를 포함한다.
51. review result가 reviewed-ID 선언만으로 passed가 되지 않는다.
52. target 수정 후 old review가 stale로 계산된다.

### Phase 4 and Performance

53. cue metadata 열을 표시·숨김할 수 있고 문서 종류별로 설정을 기억한다.
54. 숨겨진 cue ID, lineage와 note를 검색해 row와 video start로 이동한다.
55. reference Apple Silicon release build에서 552/505 fixture open과 pair projection이 2초 이내다.
56. 같은 fixture의 hidden metadata search 결과가 200ms 이내에 표시된다.
57. 같은 fixture의 atomic save가 1초 이내다.
58. 성능 측정은 cold/warm 조건과 기준 장비를 receipt에 기록한다.

---

## Testing Decisions

좋은 테스트는 내부 클래스 구조보다 사용자가 보는 canonical JSON 왕복, pair 표시, mutation 결과, state transition과 export 일치를 검증한다.

### Shared Contract Tests

- valid/invalid v2 source와 translation
- valid/invalid context, glossary와 review
- JCS golden canonical bytes와 digest
- null/omitted/empty-array behavior
- relative path가 content digest에서 제외되는지
- lifecycle/finalization이 content digest에서 제외되는지
- source/context/glossary identity가 candidate content digest에 포함되는지

### Migration Tests

- v1 source inline context→input context asset
- v1 translation retained context→prepared context fact
- status mapping
- opaque ID와 lineage 보존
- absolute evidence URI rebase
- input non-overwrite와 receipt

### SFW Canonical Session Tests

- load→save full field preservation
- document-level ref와 status 보존
- metadata-only dirty detection
- metadata undo/redo
- OriginalText mutation command guard
- atomic save failure safety
- Save As relative ref rebasing

### Pair View Tests

- 1:1, N:1, 1:N, N:M projection
- source cue 순서대로 OriginalText 결합
- source context note 파생
- missing source, ID mismatch와 digest mismatch
- target-only degraded mode와 finalization 차단

### Mutation Tests

- source/target add
- source/target split
- source/target merge
- delete와 resequence
- first ID preservation과 new ID uniqueness
- lineage status transition
- translation/context note conflict rule
- unresolved decision reference handling
- finalized revision copy-on-edit

### State and Review Tests

- lifecycle/decision orthogonality
- not_run/passed/failed/stale 계산과 review_conflict 차단
- AI review artifact가 translation content digest를 바꾸지 않는지
- no-review acknowledgement
- failed finding disposition
- source/context/glossary mismatch matrix

### Translation Quality Regression Tests

실제 NOVA NOVA 오류 유형을 fixture로 사용한다.

- `Kina` variant
- `Chanel` variant
- `Ki Bo-bae` variant
- 작품명 하이픈
- `개팔아 → Sell dogs!`
- `Mama Yuna` 관계 손실
- source note 미반영
- cue 경계 period + lowercase continuation
- 직역체·설명체 후보
- human-selected `LA`를 AI invention과 구분

문구 전체를 하나의 정답으로 고정하지 않고 오류 유형과 required behavior를 검증한다.

### Export Tests

- JSON→ASS/SBV/SRT/VTT/CSV
- target cue count, TC와 text equivalence
- CSV source text lineage projection
- export receipt의 source translation content digest

---

## Error Handling

- source/context/glossary path가 없으면 identity를 표시하고 재지정 기능을 제공한다.
- path가 가리키는 ID/digest가 다르면 자동 대체하지 않는다.
- translation은 target-only degraded mode로 열 수 있지만 pair review와 finalization은 ref 해결 전 차단한다.
- unknown source cue ID는 row mapping error로 표시한다.
- unsupported schema version은 write-save를 차단한다.
- mutation invariant 위반은 적용 전에 이유를 보여준다.
- finalized revision을 직접 덮어쓰지 않는다.
- save validation 실패는 기존 파일을 보존한다.
- review stale은 번역 text가 자동으로 틀렸다는 뜻이 아니라 현재 binding에 대한 review 보증이 없다는 뜻으로 표시한다.

---

## Out of Scope

- source revision map과 translation 자동 rebase
- 별도 sidecar/edit patch 정본
- ASS·SBV 일반 reverse importer
- 앱 내부 장기 편집 이력 데이터베이스
- 실시간 공동 편집과 권한 관리
- 웹 기반 자막 편집기
- 범용 translation memory
- 중앙 glossary DB
- row/TC 유사도 기반 source-translation 자동 정렬
- source와 translation을 한 화면에서 양방향 동시 수정
- unknown future schema field의 임의 편집
- 줄바꿈 정책 수치의 최종 재설계
- ko-KR→en-US 이외 언어의 실제 출시
- 외부 사실 진위의 완전 자동 보증

---

## Implementation Module Boundaries

1. **Canonical Contracts**
   - v2 source/translation, v1 context/glossary/review schema
   - identity, JCS와 digest projection
2. **Project Migration**
   - v1→v2 conversion, evidence locator rebase와 receipt
3. **SFW Canonical Session**
   - canonical state, projection, undo/dirty와 atomic save
4. **SFW Pair Projection**
   - source ID 기반 OriginalText와 source note 파생
5. **SFW Mutation Service**
   - add/delete/split/merge와 metadata 규칙
6. **Translation Preparation**
   - prepared context/glossary 수집, conflict와 ready gate
7. **Translation Workflow**
   - translator 책임, candidate assembly와 decision handling
8. **Review Workflow**
   - review binding, coverage, finding과 effective state
9. **Validation and Export**
   - save diagnostics/finalization과 derived formats

Python과 C# 사이에 공용 runtime library를 만들지 않는다. 두 구현은 같은 JSON Schema, normative examples와 golden digest vectors를 공유한다.

---

## Further Notes

### Existing Capabilities to Reuse

SFW에는 이미 다음 기반이 있다.

- 원문과 번역문을 나란히 보여주는 번역 모드
- `OriginalText`와 `Text` 화면
- cue split, merge와 add 동작
- 화자와 ASS metadata 편집
- 여러 JSON subtitle format parser 구조
- ASS·SBV·SRT 등 다수 형식 변환

다만 기존 `Paragraph`, `SubtitleLineViewModel`, undo hash와 OriginalText command는 canonical metadata를 모르므로 단순 field 추가만으로 충분하지 않다. canonical session이 정본을 소유하고 기존 모델은 projection으로 사용한다.

### Review of Prior External Feedback

- relative path는 locator로만 사용하며 ID와 digest를 identity로 고정했다.
- multi-source target split의 lineage 전체 복사는 provisional이며 confirmation 전 finalization을 막는다.
- human-selected는 선택된 표현에만 우선하며 cue 전체 검수 면제가 아니다.
- AI review 없이 human finalization을 허용하되 acknowledgement와 finding disposition을 요구한다.
- review는 별도 artifact로 분리해 digest cycle을 제거했다.
- source 구조 편집 뒤 자동 rebase를 MVP에서 제외했다.
- draft save와 finalization validation을 분리했다.
- SFW는 canonical session, metadata-aware undo/dirty와 OriginalText guard를 요구한다.
- evidence absolute base URI 이동성 문제를 migration과 diagnostics에 포함했다.
- 구현을 단계별 gate로 나눴다.

### Remaining External Review Focus

다음 재검토에서는 특히 아래를 확인한다.

1. v2로 올리는 결정과 v1 migration 범위가 충분한가.
2. RFC 8785 + semantic projection으로 Python/C# digest가 안정적으로 일치하는가.
3. translation content digest에 unresolved decision과 decision status를 포함하는 것이 적절한가.
4. finalized revision copy-on-edit가 실제 SFW 사용성에 과도한 마찰을 만들지 않는가.
5. source 자동 rebase를 제외한 정책이 현재 운영 규모에서 수용 가능한가.
6. target merge에서 unassigned/provisional lineage 처리 규칙이 더 단순해질 수 있는가.
7. context/glossary unresolved blocking 규칙이 번역 시작을 과도하게 막지 않는가.
8. review artifact 한 개의 coverage union이 chunk별 provenance를 충분히 보존하는가.
9. Phase 1 범위가 실제로 작은 vertical slice인지, 여전히 분해가 필요한가.
10. 성능 예산 2초/200ms/1초가 기준 장비와 fixture에서 현실적인가.
