---
name: video-transcription
description: Creates a Korean source-language transcript draft from a YouTube URL through protected evidence preparation, parallel window drafts, whole-transcript assembly, consented confirmation, and validation.
---

# Video Transcription
## Boundaries
- Accept only a public `youtube.com` or `youtu.be` URL.
- Never run media preparation or ASR before the user supplies episode context or explicitly says `없음`.
- Keep every artifact below one run ID. Admit only the exact automatic `ko-orig` track as optional lexical subtitle evidence; `ko`, `ko-en`, manual, translated, duplicate, malformed, and unknown variants have no speech authority. Treat ASR, admitted `ko-orig`, PTS frames, VAD speech presence, and optional pyannote clusters as evidence; none alone determines cue structure or speaker identity.
- When a proper name, team name, lyric, or external fact remains genuinely uncertain after those sources are checked, create a typed `WebLookupRequest`, use web search, and append the query, URL, excerpt, and relation to `web-lookups.json`. Web hits are supporting evidence only: they never replace video evidence or source truth, and any conflict stays unresolved for user confirmation.
- Write cue speakers as ordered ASCII-pipe labels such as `루미|한지아`. When a cue contains sequential turns, put each turn on its own speech line in the same order as the speaker labels. Multiple labels on one speech line mean the speakers share that line; do not use arrows, middle dots, overlap annotations, or explanatory text in `speaker`.
- When transferring a user-edited rough-cut source into a later main edit, move `context_note` with each transferred cue. Copy it to every target when one cue is split, join notes in source order when cues merge, and record rough-only contexts as explicitly not transferred. Before publication, compare the non-empty source notes with transferred plus explicitly excluded notes; a missing note blocks the merge.
- Do not extract extra frames or clips, rerun selected ASR, or modify source code during a protected run.
- Never run `open`, QuickTime, `afplay`, `ffplay`, browser or GUI playback, host audio, or new media extraction. Review only stored evidence.

## Operation events
Use the project-local operation-event tool when available; observability never changes command order, gates, retry rules, or transcription outcome.
Use only workflow-supplied `content_id` (the job ID), `session_id`, task/parent-task IDs, attempt ID/number, retry lineage, and stage cycle; add the `run_id` returned by `start`. Never invent or infer identity.
Emit `senpi.operation.v1` events in source-sequence order. Use `stage=transcription`, UTC/event fields supplied by the extension, structured `reason_code`, and digest-bound `EvidenceRef` values for stored artifacts only.
Never include prompts, model or subtitle prose, answers, terminal output, tool payloads, raw responses, error messages, or completion inferred from idleness/artifact presence.

```transcription-operation-policy
{"schema_version":"transcription-operation-policy.v1","event_schema":"senpi.operation.v1","matrix_workflow":"transcription","identity_fields":["content_id","run_id","session_id","task_id","attempt_id"],"boundaries":["context_gate","preparation","window_drafting","assembly","confirmation","validation","success","failure","cancellation","retry","resume","provider_usage"],"completion_authority":"explicit_terminal_event_only","detail_fields":["reason_code","evidence"],"forbidden_payload_fields":["error","message","model_text","payload","prompt","raw_payload","response","speech","subtitle_text","terminal_output","tool_payload"],"extension_unavailable":{"availability":"unavailable","reason_code":"operation_events_unavailable","blocks_transcription":false}}
```

- Context gate: after `start`, emit the root attempt `running`; while no answer exists emit `waiting/context_required`. Resume that same attempt only after context or `없음`.
- Preparation, each window, assembly, final review, and validation: emit a supplied child attempt `running`, then `succeeded` with exact artifact refs or `failed` with a typed reason and any existing failure artifact refs.
- Confirmation: emit `waiting/confirmation_consent_required` with whole-transcript/confirmation refs before asking. Accepted consent resumes the same attempt as `running`; decline/cancel emits `cancelled/confirmation_declined` and preserves the transcript.
- Retry: terminalize the failed attempt first; the new supplied attempt uses incremented `attempt_number` and exact `retry_of_attempt_id`. Resume means `waiting -> running` on the same attempt with a reason.
- Usage: after each provider response, emit only provider-reported deltas tied to its response ID and digest evidence. Unknown token/cost values are null with explicit availability; never allocate session totals.
- Success: emit the root attempt `succeeded` only after the current transcript is `finalized` and validation is `valid`. On typed failure or operator cancellation, emit the exact attempt terminal event; never infer either from silence.
- If the extension/tool is absent, record the policy's `extension_unavailable` object in the orchestration coverage record and continue normally. Do not edit generated transcription artifacts to simulate events.

## Start and context gate
1. Run `uv run transcription-cli start "$URL" --runs-dir "$RUNS_DIR"`.
2. Save `run_id`, emit the context-gate events above, and report `waiting_context`.
3. Ask who appears, including names, clothing, and appearance; tell the user `없음` is valid.
4. Stop. Do not continue until the user answers.

## Protected evidence preparation
After context, emit preparation `running`, then run:
```sh
uv run transcription-cli protected-run "$RUN_ID" \
  --context "$ANSWER" \
  --youtube-browser chrome \
  --runs-dir "$RUNS_DIR"
```
This requests only automatic `ko-orig`, records explicit optional unavailability when it does not exist, stores PTS-linked 2fps frames, runs evidence-only ASR, creates the canonical policy, exact subtitle inventory and one reusable policy/audio-bound VAD artifact, runs optional pyannote, and verifies unchanged source fingerprints.
Chrome must already be logged into YouTube. `yt-dlp` reads that browser session directly; never export, copy, log, or persist cookie values in run artifacts.
Stop on a typed nonzero error, emit failure, and never hot-fix the same run. Read `media.json`, `visual-evidence-policy.json`, `subtitle-inventory.json`, `vad.json`, `asr.json`, optional `diarization.json`, and referenced files; never edit generated artifacts directly. `evidence-status` is read-only and reports current, stale, invalid, or absent authority plus reuse/invalidation facts; it never repairs a run.

## Window drafts and whole transcript
Use the typed Python interfaces with the same `RunStore` and run ID:
Every drafter and reviewer spawn prompt must lead with the user-visible purpose, this run's episode/evidence context, the prior failure this stage prevents, forbidden proxy-success signals, required evidence actions, machine-checkable deliverable, and observable stop condition. Do not delegate only schema fields or commands. An ID list, legacy `evidence_reviewed` boolean, contact-sheet completion, or reviewer claim never proves semantic review.
1. Call `create_window_drafts(store, run_id, drafter)`. The drafter receives five-minute windows, fifteen seconds of neighbor context, stable ASR ordinals, local exact `ko-orig` cues, VAD intervals, admitted turns and stored PTS frames; at most four run concurrently. Emit task/attempt and usage events per window.
   Group multiple ASR items only when admitted verified turns fully cover every item and name one compatible speaker. With missing, partial, ambiguous, or conflicting turn ownership, keep the items separate for human review; the validator rejects a grouped cue without verified same-speaker ownership.
2. Each policy-bound `WindowDraft` returns categorical typed observations (`speech_supporting | non_speech | unknown` and `supported | conflicted | insufficient`), one `KEEP | REPLACE_SPAN | INSERT_CUE | HOLD_FOR_CONFIRMATION` receipt per cue, and exactly one cue-evidence coverage record per cue. Retain overlap and complete unresolved candidates without cue IDs. Never use `...` or `…`.
3. Call `assemble_whole_transcript(store, run_id, assembler)`. For policy-bound runs the supplied assembler callback is not invoked: deterministic receipt reconciliation collapses only equal source identities, preserves same-time/text cues with different turn ownership, applies one valid correction, and abstains on conflicting valid corrections. It cannot author new speech.
4. Persist the observations, correction receipts and one provenance record per assembled cue through the typed visual-sidecar publisher using the window results already returned. Do not run a second visual model pass to reconstruct sidecars. Every cue must retain source digest, ASR ordinal and/or exact `ko-orig` cue, observation, correction or explicit KEEP receipt, policy digest, timing and speaker ownership.
5. Permanent policy-bound cue UUIDs derive deterministically from run, policy, source identities, interval and normalized text; replay preserves semantic JSON bytes. Legacy runs keep their existing IDs and legacy assembler.
If a drafter returns no result, emit failure and retry with a materially different real drafter or stop. Never use a parent-authored ASR fallback or a boolean claim as evidence coverage.

## Consented confirmation
If the whole transcript has unresolved items, emit confirmation waiting and ask whether to enter confirmation. Do not expose an item or apply an answer before consent.
- Accept: `uv run transcription-cli confirmation-consent "$RUN_ID" --accept --runs-dir "$RUNS_DIR"`
- Decline: `uv run transcription-cli confirmation-consent "$RUN_ID" --decline --runs-dir "$RUNS_DIR"`
Run `transcription-cli start-confirmation` before consent when no attempt is open; a decline preserves transcript content.
Inside an accepted attempt:
1. Run `transcription-cli next-confirmation "$RUN_ID" --runs-dir "$RUNS_DIR"`.
2. The CLI returns the next unanswered item in ascending video-time order plus `current_number`, `total_count`, and `remaining_count`. Render one item with this order: `질문 {current_number}/{total_count} · 현재 {remaining_count}개 남음`, 구간, 확인 항목, 현재 기록, 추론 화자, 화자 추론 근거, 불확실한 이유, 완전한 후보, 질문. For speaker items, render every person and production staff member named in the run context first, in that context order; append any additional returned candidate not already listed, then append `알 수 없음`. For all other fields, render every returned candidate. Render each choice with a 1-based number and map a numeric user response back to the full choice value before calling `confirm`; never store the number itself as the answer. Speaker items include 대상 발화 and use `speaker-inference.json`; ask for the complete heard speech, actual speaker, or complete screen text as applicable.
3. Agent proposal: `transcription-cli suggest "$RUN_ID" "$CONFIRMATION_ID" --suggestion "$TEXT"`; it never resolves the item.
4. User answer: `transcription-cli confirm "$RUN_ID" "$CONFIRMATION_ID" --answer "$ANSWER"`.
5. Repeat until `ready_for_validation`.

## Final review after confirmation
After confirmation is complete or no unresolved item exists, use the typed interfaces with the same store and run ID:
1. Call `prepare_final_review(store, run_id)` and keep its digest-bound assignment.
2. The purpose is to find speech, speaker, and especially visible Korean screen-text omissions that earlier stages missed, not to acknowledge assignment IDs. Tell every reviewer that prior claimed full coverage missed screen text because ID return and contact sheets replaced semantic inspection.
3. Before spawning reviewers, save the current permission modes of the manifest's source video and extracted audio, make both files unreadable, and restore the exact modes in a `finally` path after every reviewer has stopped. Split five-minute segments across at most four independent reviewers. Reviewers use only stored PTS frames, ASR, admitted exact `ko-orig`, and the already persisted typed observations/receipts; never source media, playback, new extraction, derived subtitle tracks, or speaker output. Contact sheets may locate candidates but never prove text absence or wording; inspect each stored frame at readable resolution.
4. Each reviewer returns exact ordered frame/cue IDs, complete corrections, and one ordered `frame_observations` item per assigned frame: `frame_id`, every complete `visible_screen_text` string (empty only after observing none), and the segment-owned `compared_cue_ids`. Compare observed text with current cue `screen_text`; correct or explicitly escalate every mismatch. Speech changes require an admitted correction receipt or an actual human confirmation. Missing, duplicated, reordered, foreign, or ID-only observations fail closed.
5. Collect the complete `FinalReviewResult` and call `apply_final_review(store, run_id, result)`.
6. If application queues a conflict with a confirmed field, run the accepted confirmation flow again. After every applied conflict answer, discard the old result and start a fresh full assignment and review from step 1. A structural conflict fails closed.

## Validation
Run `uv run transcription-cli validate "$RUN_ID" --runs-dir "$RUNS_DIR"` only after final review applies without conflict confirmation or a fresh full final review completes.
Before validation, read real beginning, middle, and end cues against stored evidence. An obvious wrong word, fragment, missing utterance, or wrong speaker blocks completion and returns that window to drafting.
Validation proves structure, not semantic quality. A successful final validation automatically creates the run-owned `context.input.json`, a `content-context.v1` input/ready asset, and `source.json`, a `source-transcript.v2` draft. Subtitles for Woody can open `source.json` directly as a read-only source. `transcript.json` remains the transcription authority, and the source draft still needs later human source finalization before production translation.

Emit validation success/failure, then root success only at the explicit final gate. `source.json` is the current canonical `source-transcript.v2` draft; never route ASS into this JSON authority path.

Report workflow/validation state and the complete applicable authority inventory: `state.json`, `protected-run.json`, `media.json`, `asr.json`, optional `diarization.json`, `visual-evidence-policy.json`, `subtitle-inventory.json`, `vad.json`, optional speaker-turn authority/decision/derived files, `window-drafts.json`, `visual-observations.json`, `correction-receipts.json`, `cue-provenance.json`, `whole-transcript.json`, `transcript.json`, `transcript.csv`, `confirmations.jsonl`, `confirmation-session.json`, `final-review-assignment.json`, `final-review-result.json`, proposal/decision/application receipts, `final-review.json`, `validation.json`, `context.input.json`, and `source.json`. The confirmation anchor is nested in the current final-review assignment/receipt. Use `evidence-status` for hashes, reuse, optional absence and invalidation; operation-event unavailability remains the non-blocking typed coverage record above, never a fabricated run artifact.

Label historical derived-assisted results `contaminated` (or `incomplete mixed-source` where applicable). Label a new same-video policy-bound comparison only `policy-clean paired diagnostic`; never call either a blind benchmark or general accuracy result.
