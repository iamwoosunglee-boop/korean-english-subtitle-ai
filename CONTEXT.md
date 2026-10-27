# Transcription

한국어 영상에서 편집 가능한 원어 자막 초본을 만들고 검증하는 맥락이다.

## Language

**기초 전사**:
ASR이 오디오에서 만든 임시 발화 자료다. 전체 전사본의 cue 구조를 결정하지 않는다.
_Avoid_: ASR 정본, canonical cue

**구간 초안**:
한 구간의 기초 전사와 참고 자료를 함께 보고 작성한 임시 전사 결과다.
_Avoid_: 융합 결과, window fusion

**전체 전사본**:
모든 구간 초안을 한 영상의 시간순 전사로 정리한 사용자 확인 전 결과다.
_Avoid_: 병합 결과, canonical transcript, 최종 전사본

**최종 전사본**:
사용자 확인 수명주기가 끝나고 검증을 통과한 전사 결과다.
_Avoid_: finalized JSON, valid draft

**불명확 사항**:
구간 초안에서 판단하지 못해 대상 시각과 이유를 기록한 임시 작업 메모다.
_Avoid_: low-confidence cue

**화자 구분**:
pyannote가 비슷한 목소리로 판단한 시간 구간 묶음이다. 실제 인물 identity가 아니다.
_Avoid_: 화자 신원, speaker identity

**확인 절차**:
사용자 동의 뒤 전체 전사본의 불명확한 항목을 하나씩 묻고 답변을 반영하는 수명주기다.
_Avoid_: 자동 확정, confirmation recovery
