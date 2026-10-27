# 이슈 #9~#15 인수 기준 감사

- 감사일: 2026-08-04
- 실제 run: `bdafc8c8-3a84-47cc-9e63-4671e6b642bc`
- 범위: 이슈 #9~#15의 모든 인수 기준
- 상태 표기: `PASS`, `IN PROGRESS`, `PENDING`

이 문서는 테스트 통과 여부가 아니라 각 이슈의 인수 기준과 실제 코드,
테스트, run artifact를 직접 대조한다. `IN PROGRESS`나 `PENDING`이 남아
있으면 실제 E2E 완료를 선언하지 않는다.

## #9 전사본 저장과 검증 상태를 안전하게 연결

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 최초 저장·수정 시 validation은 pending | PASS | `tests/unit/test_validation_lifecycle.py`, 실제 `state.json` |
| 기존 valid 수정 시 먼저 무효화 | PASS | `test_validation_lifecycle.py`의 valid 수정 회귀 테스트 |
| 저장 실패 시 이전 valid가 남지 않음 | PASS | 저장 실패 fixture 회귀 테스트 |
| validator만 valid 기록 | PASS | lifecycle API와 validator 테스트 |
| CLI와 artifact로 상태 전이 검증 | PASS | `tests/e2e/test_confirmation_consent.py`, `validation.json` fixture |

## #10 실제 영상에서 기초 전사와 참고 자료 준비

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| URL과 회차 맥락으로 미디어 준비 시작 | PASS | 실제 run `state.json`, `media.json` |
| ASR 자료만 저장하고 canonical cue를 만들지 않음 | PASS | 실제 `asr.json`, `tests/integration/test_media_asr.py` |
| 수동·자동 한국어 자막 모두 보존 | PASS | 실제 `media.json`의 수동 1·자동 3 트랙 |
| source timeline 2fps와 실제 PTS 연결 | PASS | 실제 frame 2,786개·1,392.814초, PTS manifest |
| 전체 decoded frame·추가 contact sheet·clip 없음 | PASS | 실제 `input/frames`와 `media.json` 계약 |
| 네트워크 없는 adapter fixture 검증 | PASS | `tests/integration/test_media_asr.py` |

## #11 5분 구간 초안을 최대 4개 병렬 작성

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 5분 core와 앞뒤 15초 자료 포함 | PASS | 실제 `window-drafts.json`, window 입력 artifact |
| 최대 동시성 4·완료 순서 독립 | PASS | `tests/integration/test_window_drafting.py` |
| 중복 문맥을 소유권 규칙으로 자르지 않음 | PASS | 실제 5개 구간 초안과 overlap cue |
| 발화·시각·선택적 화자·불명확 사항 표현 | PASS | 실제 구간 cue 112/107/68/54/34개 |
| 불명확 사항에 시각·필드·이유·후보 포함 | PASS | 실제 구간별 불명확 2/1/2/0/1개 |
| 고정 sleep 없는 병렬 경계 테스트 | PASS | event 기반 fixture 테스트 |

## #12 구간 초안으로 전체 전사본 작성

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 모든 구간 초안과 입력 artifact를 받음 | PASS | `whole-transcript.json`, integration test |
| 기존 frame manifest 프레임만 열람 | PASS | 실제 inspected frame ID 4개, 추가 추출 없음 |
| overlap 중복 정리·판단 불가 충돌 보존 | PASS | 전체 전사본 작성 결과와 불명확 6개 |
| 동시 발화 별도 cue·시간 중첩 허용 | PASS | 실제 overlap 유지, integration test |
| 생성 뒤 cue UUID 한 번 부여·유지 | PASS | 최초 345개 UUID, 답변·삭제 후 남은 343개 UUID 보존 |
| 미해결 항목만 이유와 함께 확인 대상으로 남김 | PASS | `confirmations.jsonl`, `confirmation-session.json` |

## #13 사용자 확인을 전체 전사본에 안전하게 반영

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 개별 항목보다 참여 동의를 먼저 요청 | PASS | 실제 accepted session, e2e test |
| 동의 전 item·answer 거부 | PASS | `tests/e2e/test_confirmation_consent.py` |
| answer만 해결하고 suggest는 변경하지 않음 | PASS | 실제 suggestion ordinal 7과 사용자 answer 기록 |
| 거절은 보존·재확인은 새 절차 | PASS | e2e lifecycle test |
| 잘못된 ID·중복·빈 답변·손상 기록 보호 | PASS | e2e 오류 회귀 테스트 |
| 답변 반영 시 validation 먼저 pending | PASS | 실제 `state.json`, lifecycle test |

## #14 선택적 pyannote 화자 구분 연결

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 전체 오디오 1회 실행·artifact 보존 | PASS | `diarization.json` status available, 598개 구간·5개 cluster, 적용 화자 339개와 대조 모순 없음 |
| 의존성 부재·실패·빈 결과를 기록하고 계속 | PASS | 최초 실패 `diarization.json`, protected run 성공 |
| agent 입력에 cluster 비신원·오류 가능성 제약 | PASS | video-transcription skill과 agent prompt |
| 같은 cluster의 전역 인물 매핑 금지 | PASS | `SpeakerEvidenceSegment`와 추론 계약 |
| 실제 화자를 cue별로 추론·불명확하면 비움 | PASS | #16, `speaker-inference.json`: 339 resolved·4 unresolved |
| pyannote 성공·실패 fixture가 같은 계약 검증 | PASS | `tests/integration/test_window_drafting.py` |

## #15 보호된 실제 영상 E2E 주행

| 인수 기준 | 상태 | 증거 |
| --- | --- | --- |
| 주행 중 source 쓰기 거부 | PASS | `tests/unit/test_protected_execution.py` |
| source fingerprint 불일치 시 실패 | PASS | 실제 `protected-run.json`, fingerprint 동일 |
| 결함 발견 시 같은 run에서 수정하지 않음 | PASS | 실패 run 보존 후 새 run으로 재실행 |
| 5분·15초·2fps·동시성 4 처리 | PASS | 실제 media·window artifact |
| help·잘못된 입력·fixture·실제 happy path 관찰 | PASS | help·잘못된 URL·fixture·실제 영상 전부 관찰 |
| 전체 전사본·확인·검증·종료 monitor 확인 | PASS | 전사본 344 cue valid, 확인 10건 완료, monitor 전부 종료 |

## 시정 조치 상태

- #16: 완료 — 339/4 화자 결정 적용(`speaker-inference.json`), 확인 표시에
  추론 화자·근거 포함, `start-confirmation` 진입 게이트 추가(전체 55 passed)
- #17: 완료 — 전체 오디오 화자 구분 성공(598 구간·5 cluster), 토큰 유출 없음,
  적용 화자와 대조 결과 모순 없음(오태우·박도현 cluster 공유는 남성 목소리 과병합 예상 범위)
- #18: 이 문서 — 전체 기준 PASS, 실제 E2E 완료 선언
- #19: 완료 — 시각 불명확 항목이 있으면 프레임 열람 없이 조립 불가(전체 56 passed)

현재 미완료 기준 없음. 실제 영상 E2E 전체 경로(보호 실행 → 증거 준비 →
구간 초안 → 전체 전사본 → 화자 추론 → 확인 → 검증 → 화자 구분) 검증 완료.
