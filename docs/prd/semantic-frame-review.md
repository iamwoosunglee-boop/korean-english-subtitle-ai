# 화면 의미 검토와 에이전트 위임 PRD

## Problem Statement

원어 자막 초본의 최종 검토는 저장된 PTS 프레임에서 화면에 실제로 표시된
한국어 문구를 발견하고, 같은 시점의 cue와 대조해 `screen_text` 누락과 오기를
고치는 단계다. 그러나 기존 구현은 이 사용자 목적을 `frame_ids`와 `cue_ids`
목록의 완주 여부로 바꿨다.

구간 검토자는 비교 내역 없이 `evidence_reviewed=true`를 반환할 수 있었고,
전체 전사본 작성자는 이미 불명확하다고 보고된 항목에만 프레임을 열었다. 최종
검토자는 assignment의 ID 목록을 그대로 반환하면 exact coverage를 통과했다.
프레임에서 무엇을 읽었고 어느 cue와 비교했는지는 결과 계약에 없었다.

실제 위임 프롬프트도 사용자 목적과 이전 실패를 설명하기보다 입력 파일, 출력
schema와 성공 조건을 중심으로 작성됐다. 에이전트는 주어진 지시를 따랐지만,
성공 조건 자체가 의미 검토가 아닌 ID 완주였기 때문에 화면 문구를 놓치고도
검토 완료로 인정됐다.

이 결함은 에이전트의 불성실이 아니라 제품 요구사항과 위임 계약의 실패다.
boolean, ID 목록, contact sheet 완주 같은 대리 지표가 사용자가 원하는 실제
결과를 대신하지 못하도록 제품 경계를 바꿔야 한다.

## Solution

최종 검토 결과에 모든 assigned frame의 typed semantic observation을 요구한다.
각 observation은 프레임 ID, 프레임에서 완전하게 읽은 모든 화면 문구, 실제로
대조한 같은 segment의 cue ID를 보존한다. observation의 순서는 assignment의
프레임 순서와 정확히 같아야 한다.

빈 화면 문구 목록은 해당 프레임을 읽을 수 있는 해상도로 직접 확인한 뒤 문구가
없다고 판단한 결과다. “보지 않음”, “contact sheet에서 안 보임”, “ID만
반환함”을 빈 목록으로 표현할 수 없다.

모든 에이전트 위임은 schema나 명령보다 먼저 사용자 목적, 실행 맥락, 이 단계가
막아야 하는 이전 실패, 금지된 대리 지표, 필수 증거 행동, 검증 가능한 산출물과
관찰 가능한 종료 조건을 제공한다.

## Goals

1. 저장 프레임의 모든 화면 문구를 의미 단위로 검토한다.
2. ID 완주와 실제 의미 검토를 구분한다.
3. 프레임 관찰을 cue 비교와 추적 가능하게 연결한다.
4. 이전 단계의 누락이 unresolved로 보고되지 않아도 최종 검토에서 발견한다.
5. 에이전트가 사용자 목적과 실패 맥락을 이해한 상태에서 작업하게 한다.
6. 검토 완료를 자기신고가 아니라 machine-consumed evidence로 판단한다.

## Non-Goals

- OCR 엔진을 새로 도입하지 않는다.
- 새 프레임, 영상 clip 또는 오디오를 추출하지 않는다.
- 원본 영상·오디오를 열거나 재생하지 않는다.
- 사람이 최종 원어 자막을 확정하는 책임을 대체하지 않는다.
- 자연어 chain-of-thought나 원시 모델 응답을 저장하지 않는다.

## User Stories

1. 원어 자막 제작자로서 최종 검토가 모든 저장 프레임의 화면 문구를 실제로
   확인하길 원한다. 그래야 화면 자막 누락을 구조 검증 통과로 숨기지 않는다.
2. 원어 자막 제작자로서 화면 문구가 어느 cue와 대조됐는지 알고 싶다. 그래야
   검토 결과를 다시 추적할 수 있다.
3. 원어 자막 제작자로서 화면 문구가 없는 프레임도 직접 확인됐음을 보장받고
   싶다. 그래야 빈 값이 미검토와 혼동되지 않는다.
4. 운영자로서 프레임 누락·중복·재정렬·외부 ID가 검토를 통과하지 않길 원한다.
5. 운영자로서 contact sheet와 ID 목록이 의미 검토의 증거로 오용되지 않길
   원한다.
6. 에이전트 작업 설계자로서 각 하위 작업에 사용자 목적과 이전 실패 맥락을
   전달하고 싶다. 그래야 에이전트가 잘못 정의된 국소 성공 조건만 최적화하지
   않는다.

## Product Requirements

### Frame Observation Contract

- 각 review segment 결과는 assigned frame마다 정확히 하나의 observation을
  포함해야 한다.
- observation은 `frame_id`, `visible_screen_text`, `compared_cue_ids`를 가진다.
- `visible_screen_text`의 각 값은 화면에 동시에 표시된 하나의 완전한 문구다.
  일부 발췌, 생략 부호 또는 “텍스트 있음” 같은 설명은 허용하지 않는다.
- 문구가 없음을 직접 확인한 경우에만 `visible_screen_text=[]`를 허용한다.
- `compared_cue_ids`는 해당 프레임에서 실제로 대조한 같은 segment의 cue만
  포함한다. segment에 cue가 없는 프레임은 빈 목록을 허용한다.
- observation의 frame ID 순서는 segment의 assigned frame ID 순서와 정확히
  일치해야 한다.
- 누락, 중복, 재정렬, 외부 frame ID와 외부 cue ID는 결과 파싱 또는 적용 전에
  실패한다.
- accepted final-review result와 receipt digest는 observation을 포함한다.

### Semantic Comparison

- reviewer는 각 프레임을 문구를 읽을 수 있는 해상도로 확인한다.
- contact sheet는 후보 위치 탐색에만 사용할 수 있다. 문구의 부재나 정확한
  철자를 확정하는 최종 증거로 사용할 수 없다.
- reviewer는 관찰한 화면 문구를 해당 시각의 현재 cue `screen_text`와 비교한다.
- 명백한 불일치는 complete correction으로 반환한다.
- 직접 증거가 충돌하거나 판독할 수 없으면 불일치를 성공으로 숨기지 않고
  사용자 확인 또는 실패 경로로 보낸다.
- ID 목록 반환, `evidence_reviewed` boolean, 도구 호출 수, contact sheet 생성,
  “모든 프레임을 봤다”는 자연어 보고는 의미 검토 증거가 아니다.

### Agent Delegation Contract

모든 drafter, assembler와 final reviewer prompt는 다음 순서의 정보를 제공한다.

1. **Purpose**: 사용자가 얻어야 하는 실제 품질 결과.
2. **Run context**: 회차 맥락, 제공된 증거와 현재 단계의 입력 상태.
3. **Failure context**: 이 단계가 막아야 하는 구체적인 이전 실패.
4. **Forbidden proxies**: 완료로 간주하면 안 되는 boolean, ID 목록, thumbnail
   완주와 자기신고.
5. **Required evidence actions**: 어떤 저장 증거를 어떻게 의미 단위로 비교할지.
6. **Machine-checkable deliverable**: typed observation, correction과 unresolved
   결과.
7. **Observable stop condition**: 모든 의미 관찰이 계약을 통과한 상태.

프롬프트는 필드 채우기나 명령 실행만 전달해서는 안 된다. 하위 에이전트가
상위 사용자 요청과 결함의 원인을 모른 채 국소 schema만 최적화하지 않도록
목적과 맥락을 작업 본문에 포함한다.

## Implementation Decisions

- 기존 final-review assignment의 5분 segment와 digest binding을 유지한다.
- `ReviewSegmentResult`에 ordered frame observation을 추가한다.
- frame observation은 별도 top-level artifact를 만들지 않고 accepted
  final-review result 안에 보존한다.
- observation 모델은 프레임별 사실과 cue 연결만 보존하고 내부 추론은 저장하지
  않는다.
- exact ID coverage 검사는 유지하되 의미 observation 검사와 함께 적용한다.
- 기존 run artifact는 새 계약으로 소급 승인하지 않는다. 새 review assignment와
  result로 다시 검토해야 한다.
- 전사 스킬은 위임 프롬프트에 목적·맥락·실패·금지된 우회·산출물·종료 조건을
  포함하도록 명시한다.

## Testing Decisions

- 모델 테스트는 ID-only 결과가 거부되는지 검증한다.
- observation의 누락, 중복, 재정렬, 외부 frame/cue를 각각 검증한다.
- 통합 테스트는 observation이 없는 exact ID 결과가 receipt를 만들지 못하는지
  검증한다.
- 정상 통합·CLI fixture는 모든 assigned frame에 typed observation을 제공한다.
- result 저장 후 observation 변조가 receipt digest 검증에서 거부되는지
  검증한다.
- 테스트는 자연어 프롬프트 전체를 고정하지 않는다. machine-consumed 필드와
  stage order만 검증한다.
- 실제 저장 프레임을 사용하는 최소 드라이버에서 ID-only 실패와 observation
  포함 성공을 직접 확인한다.

## Acceptance Criteria

1. exact frame/cue ID만 반환한 result는 거부된다.
2. 모든 assigned frame에는 순서가 일치하는 observation이 필요하다.
3. foreign cue를 비교했다고 주장한 observation은 거부된다.
4. 화면 문구의 빈 목록은 명시적인 관찰 결과로만 표현된다.
5. accepted result와 receipt가 frame observations에 digest-bound된다.
6. reviewer prompt가 사용자 목적과 이전 누락 실패를 먼저 설명한다.
7. contact sheet와 자기신고가 의미 검토의 대리 지표가 아님을 명시한다.
8. 관련 모델·통합·CLI·스킬 테스트가 통과한다.
9. 실제 결과 계약을 호출한 수동 QA에서 ID-only 경로가 실패한다.

## Rollout

1. typed frame observation과 모델 검증을 추가한다.
2. 모든 final-review producer와 fixture를 새 계약으로 전환한다.
3. 전사 스킬의 위임 계약을 갱신한다.
4. 회귀·통합·CLI 검증을 실행한다.
5. 진행 중인 실제 run은 새 assignment부터 다시 최종 검토한다.

## Further Notes

이 PRD의 핵심은 “검토를 더 많이 하라”가 아니다. 사용자가 원하는 의미 품질과
시스템이 검증하는 성공 조건을 같은 것으로 만드는 것이다. 에이전트가 지시대로
행동해도 사용자 목적을 우회할 수 없는 산출물 경계가 필요하다.
