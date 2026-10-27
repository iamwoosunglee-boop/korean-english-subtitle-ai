---
name: video-translation
description: >
  Translates a canonical context-rich subtitle source from ko-KR to natural
  en-US JSON through prepared context and glossary assets, bounded semantic
  chunks, evidence-backed drafting, independent review, focused risk review,
  user escalation, and deterministic validation. Use when the user supplies
  or references a canonical source transcript JSON, or explicitly requests an
  unpublished draft-source trial.
---

# Context-rich Video Translation

## Hard gates

- Require a digest-bound `source-transcript.v2` JSON. A finalized publication
  source must have canonical finalization metadata; a draft may be used only
  for an explicitly unpublished trial.
- Treat `source-transcript.v1` and `translation-document.v1` as read-only legacy
  migration inputs. Convert them before canonical work:
  `translation-cli migrate-project SOURCE_V1 --translation TRANSLATION_V1 --output-dir DIRECTORY --evidence-root DIRECTORY`.
- Prepare `content-context.v1`, `translation-glossary.v1`, and the evidence
  manifest through the canonical asset commands before drafting. Their v1
  suffixes are current asset schema versions, not legacy source/document
  versions.
- Raw ASS is not a translation input. Authoritative ASS must first pass through
  `transcription-cli export-ass-translation-source`, then through canonical
  migration when the export is legacy v1.
- Preflight every referenced evidence resource. Open relevant frames; an
  evidence ID alone is not visual inspection.
- Preserve speaker, retained context, source lineage, timing, content kind, and
  canonical digests.
- Never translate each source cue independently merely because it is timed.
- The candidate author never writes its independent review. Do not finalize
  before receiving actual output from a separate reviewer session.
- Autonomous continuation is part of the E2E contract. Intermediate chunk,
  review, focused-review, revision, and validation completions are resume
  signals, not stopping points. Immediately start the next executable stage;
  do not emit a final response or wait for another user message unless the
  user-confirmation gate below is actually reached.
- Before ending any turn in an active translation run, either the requested
  final artifact must be complete or a live task/monitor must exist whose
  completion resumes the next stage. A pending todo list is not a resumption
  channel. Never stop after an intermediate check with no live continuation.
- For an active E2E run, the turn preamble's stop condition must name final
  pipeline completion or creation of that live resumption channel. Do not bind
  the turn to stop merely because one intermediate validation passes.
- Final output is `translation-document.v2` JSON, not ASS/SRT/VTT.
- `bind-source`, `validate-batch-plan`, `prepare-request`,
  `assemble-candidate`, `prepare-review`, `route-review`, `resolve-answer`,
  `validate`, and `publish` are legacy v1 interfaces. Do not use them as the
  canonical v2 production path.
- Transient executable source retained as evidence must use an inert extension
  such as `.py.txt`, with command/output captured separately.

## Operation events

Instrumentation observes this workflow; it never weakens or replaces a
translation gate.

- Use the project operation-event tool to emit `senpi.operation.v1` events at
  the translation boundaries in
  `src/dashboard_module/schemas/workflow-event-matrix.v1.json`.
- Before work starts, require the workflow-supplied content/job ID, run ID,
  root task ID, and attempt ID. Every event carries that `content_id`,
  `run_id`, the active Senpi `session_id`, `task_id`, `parent_task_id`,
  `attempt_id`, `attempt_number`, `retry_of_attempt_id`, `stage`, and
  `stage_cycle`. Do not derive identity from paths, artifact existence, todo
  text, or task idleness.
- Give each bounded chunk, independent review, focused review, revision, and
  final validation its own child task and attempt identity. Use `translation`
  for source/context/chunk/revision/validation work and `review` for independent
  review, focused review, and confirmation waits.
- Emit `running` when an operation starts and a matching `succeeded`, `failed`,
  or `cancelled` terminal event for every started attempt. Interrupted states
  use a structured `reason_code`. Evidence contains only stable artifact IDs,
  exact SHA-256 digests, and observation times.
- Never put source or translated prose, prompts, model responses, rationale,
  terminal output, tool payloads, unrestricted errors, or chain-of-thought in
  an event. A model identifier and provider-response ID are allowed only in a
  `usage.reported` event.

Emit at these exact boundaries:

1. `source_validation`: use a dedicated child attempt, start before canonical
   source validation, and persist its `succeeded` completion bound to the exact
   supplied source digest before context preparation starts. Fail before
   context or drafting on any identity, lifecycle, evidence, or digest error.
2. `context_preparation`: start only after source validation; bind input and
   completion events to the exact source, ready context, ready glossary, and
   evidence-manifest digests.
3. `chunk_drafting`: emit one child operation per at-most-64-cue owned chunk,
   bound to the source/context/glossary digests. Its success evidence identifies
   the exact chunk candidate digest; neighboring context remains read-only.
4. `independent_review`: emit a separate reviewer child operation bound to the
   exact candidate/document content digest plus the source, context, and
   glossary digests. A review success records both that candidate digest and
   the assembled review digest. A review for any other digest is stale and
   cannot approve the current candidate.
5. `focused_risk_review`: emit only for blocking fidelity, speaker, culture, or
   lyrics risk, bound to the exact current candidate and independent-review
   digests. Its success is advisory and never implies approval or finalization.
6. `revision`: emit one new stage cycle for each candidate-changing revision,
   with input review/candidate digests and the new candidate digest. Keep the
   two-revision naturalness/segmentation cap and three-revision
   fidelity/speaker/culture/lyrics cap; do not reset counts on retry or resume.
7. `escalation`: emit `waiting` with `revision_cap_reached` or a narrower
   structured reason and exact current candidate, review, and unresolved-
   decision artifact digests. User confirmation does not count as approval for
   another digest.
8. `final_validation`: bind deterministic validation to the exact current v2
   document content digest and the final independent-review digest. A digest
   conflict emits `failed`; artifact presence alone never means validation or
   approval passed.
9. `publication`: after final validation and canonical finalization, persist a
   timestamped `work.state` publication child attempt and its `succeeded`
   completion, bound to the exact published v2 document and current review
   digests. This is a publication observation, not a delivery receipt.
10. `success`: emit on the root attempt only after the current digest passes
    deterministic validation, complete independent review, confirmation when
    required, canonical finalization, and the persisted publication completion
    above. Record the exact published document and current review digests. Never
    infer success or delivery from a file, a completed child task, fixture
    metadata, or an idle task batch.
11. `failure` and `cancellation`: terminate the affected attempt immediately
    with a structured reason. Do not later emit success for that attempt.
12. `retry`: create a new attempt for the same task with incremented
    `attempt_number` and exact `retry_of_attempt_id`. `resume`: explicitly move
    the waiting parent attempt back to `running` with a structured reason; it is
    not a new revision and does not erase prior failure evidence.
13. `provider_usage`: emit one `usage.reported` event from provider-reported
    response usage. Attribute every available token/cost value to that exact
    provider response; use null plus an explicit availability when unavailable.
14. `senpi_delivery`: emit `delivery.recorded` only after an actual Senpi
    delivery with an immutable receipt bound to the published output digest.
    Publication without delivery emits no delivery event.

If the operation-event extension or tool is absent, record operation coverage
as `availability: unavailable` with
`reason_code: operation_extension_unavailable` in the run/task receipt and
continue the same translation path. Do not fabricate an event, retry solely for
instrumentation, or block source validation, translation, review,
confirmation, publication, or validation. Other event-write failures receive
the same nonblocking unavailable-coverage treatment.

Before preparing assets, translating, or reviewing, read and apply
`references/quality-checklist.md`. Its source-wide, semantic-chunk, and
document-wide scopes are mandatory; never apply meaning checks cue by cue.

## Prepare

1. If the supplied source is legacy v1, run `migrate-project` and continue only
   with the generated canonical v2 project.
2. Run
   `translation-cli prepare-assets SOURCE_V2 INPUT_CONTEXT --output-dir DIRECTORY`.
3. Treat both outputs as incomplete drafts. Inspect the entire finalized source,
   input context, and evidence; complete the prepared context and glossary with
   evidence-backed facts and terms.
   Facts whose IDs start with `visual-context:` are stored AI scene observations,
   not human answers. Inspect their supporting frames and dialogue before
   resolving them. Their empty source scope does not assert that every cue
   shares the scene: assign relevant current source cue IDs during preparation
   only when the relationship is supported. Never infer speech ownership or
   timing from frame PTS, or mark these observations `human_confirmed` without
   an actual user confirmation.
4. Give a separate reviewer the finalized source and completed glossary. The
   reviewer must find missing terminology across the source, not merely
   conflicts among entries already present. Resolve every finding before
   continuing.
5. Never clone, copy, or rebind a ready context or glossary across source
   revisions, even for the same episode. A source revision change always starts
   again at `prepare-assets`. An empty glossary is acceptable only after the
   source-wide audit and independent missing-term review find no candidates.
6. Run
   `translation-cli ready-assets SOURCE_V2 PREPARED_CONTEXT GLOSSARY --output-dir DIRECTORY`.
7. Read the finalized source, ready context, ready glossary, all source cues,
   and the prepared evidence manifest.
8. Before any translation, audit speaker/text structure for every source cue:
   - one speaker may own one unprefixed utterance;
   - multiple sequential speakers require the same number of utterance lines,
     separated by `\N` or a real line break, in the same order as `speakers`;
   - a multi-speaker cue with unseparated text, mismatched speaker/line counts,
     an unexplained extra speaker, or ambiguous turn ownership is blocking
     source ambiguity. Never invent, duplicate, remove, or reassign an
     utterance merely to make the counts match.
9. For blocking speaker ambiguity, stop before chunk drafting and ask the user
   one focused source-correction question. Show the cue ID, timestamp, full
   source text, current speaker list, neighboring dialogue, and inspected
   evidence; ask for the corrected speaker order and line ownership. Resume
   only after the answer is bound to a corrected canonical source snapshot or
   an explicit source-correction record.
10. Infer coherent semantic ranges from sentence completion, dialogue flow,
   silence, scene changes, speaker turns, lyrics, and crosstalk.
11. Persist those ranges once as one source-bound `TranslationBatchPlan`.
    Each chunk owns at most 64 source cues and receives read-only context before
    and after; the cap is an execution-size limit, never a semantic-boundary
    rule. Translator tasks may use only ranges from this plan.

## Translate

- Give the translator its owned source range, ready context, ready glossary,
  inspected frame observations, and read-only neighboring context.
- Reconstruct the complete intended meaning before writing target cues.
- Write one natural continuous target passage for the complete semantic chunk
  before segmenting it into display cues. Never translate owned source cues
  independently and concatenate the results.
- Bind every target cue to the exact ordered source cue IDs and speakers that
  supply its meaning; the aggregate target timing must stay inside that source
  lineage envelope.
- A target cue may merge adjacent source cues into one natural display unit
  within their aggregate timing envelope. Do not call later clauses
  "premature" merely because the merged cue begins with the first linked
  source cue.
- One source cue with up to two ordered sequential speakers may remain one
  two-line target cue over the source cue's aggregate timing when each line
  uses `- utterance` and matches `speakers`. Do not invent an internal turn
  timestamp that canonical source evidence does not provide. Confirmed joint
  speech remains one line with all joint speakers.
- Never place meaning outside its declared source lineage or aggregate timing
  envelope merely to improve phrasing or CPS.
- Produce natural conversational en-US without Korean word order,
  translationese, literal idioms, omissions, inventions, or meaning reversal.
- Preserve existing English lyrics. Translate Korean lyrics for meaning and
  emotion, not singable meter or rhyme.
- Treat screen text as evidence, not an independently translated cue.
- English display cues may contain at most two sequential speakers. Render
  each turn on its own `- utterance` line separated by an actual LF, with the
  line order matching `speakers`. JSON serialization represents that LF as
  `\n`; literal ASS `\N` is not valid canonical target text.
  If a source cue names more than two speakers,
  resegment without changing timing ownership when evidence permits; otherwise
  route it through the blocking speaker-ambiguity confirmation gate.
- Resegment display-ready target cues inside the referenced source envelope.
- Before returning a revised chunk, check every target cue against its timing
  for configured line-count, characters-per-line, and CPS limits; resegment
  within the owned source envelope when restored meaning does not fit.
- Keep read-only neighboring context out of owned output.
- Save each typed chunk result separately. Assemble them into one
  `translation-document.v2` draft with contiguous target ordering, unique IDs,
  complete source lineage, and the exact source digest.

## Record and select the candidate

Use one `AUTHORITY` directory for candidate attempts, preflight, selection,
review assembly, finalization, and delivery. `PROJECT` is the existing common
directory containing the candidate and its referenced dependencies. Candidate
asset paths are relative to the candidate file and stay inside `PROJECT`.
`SOURCE` is the finalized source; `CONTEXT` and `GLOSSARY` are its ready assets.

1. Save `RATIONALES` as a JSON array of `TranslationLineRationale` records:
   `target_cue_id`, one-based `line_number`, `speaker_id`, `line_text_digest`,
   and a concrete `rationale_ko`. Hash each actual displayed line with
   `translation_line_text_digest`. Preserve speaker-owned line order.
2. Record the candidate before requesting selection or assembled review:

   ```bash
   translation-cli record-candidate-attempt SOURCE CANDIDATE \
     --authority-root AUTHORITY --attempt ATTEMPT_ID initial - \
     --rationales RATIONALES
   ```

   Each revision uses a new candidate revision ID and attempt ID,
   `--attempt NEW_ATTEMPT_ID alternative PARENT_ATTEMPT_ID`, and new rationale
   records. Use `retry` for a retry of a failed attempt. Do not overwrite a
   persisted candidate or previous attempt.
3. Save `CHECKER` as `BoundPreflightCheckerEvidence`
   (`translation-preflight-checker.v1`). Bind the exact translation document
   ID, revision ID and content digest, source document ID, revision ID and
   content digest, and glossary asset ID, revision ID and content digest.
   Its `evidence` is `PreflightCheckerEvidence`: complete adjacent-boundary,
   glossary, source-claims and target-only review results, plus findings and
   target requirements. Independent executions supply the source-claims and
   target-only judgments; do not fabricate empty results.
4. Select the recorded attempt through the canonical v2 CLI:

   ```bash
   translation-cli preflight-select SOURCE CANDIDATE CHECKER \
     --authority-root AUTHORITY --dependency-root PROJECT \
     --attempt-id ATTEMPT_ID \
     --selection SELECTION_ID SELECTED_AT SELECTED_BY
   ```

   `SELECTED_AT` is the actual UTC timestamp and `SELECTED_BY` identifies the
   selecting actor. The selection receipt returned by this command binds
   subsequent review and finalization. CPS warnings do not permit removing
   meaning or rewriting candidate bytes.

## Validate and review

1. Validate the complete v2 candidate for schema, lifecycle, identity, lineage,
   coverage, order, timing, speaker, evidence, readability, and digests.
   Reject any target speaker absent from its linked source cues and any
   multi-line speaker order that does not match the linked source turns before
   candidate selection or review.
2. Run
   `translation-cli prepare-shared-review-harness SOURCE_V2 TRANSLATION PLAN --output HARNESS`.
   Give one reviewer each exact chunk in `HARNESS.chunks`. Never create,
   coalesce, split, or renumber reviewer ranges separately from the translation
   plan.
3. Give each separate reviewer the candidate plus the canonical source, ready
   context, ready glossary, evidence manifest, and inspected evidence.
4. The reviewer first reassembles target cues into semantic chunks and checks
   each complete passage, then performs a document-wide terminology, referent,
   tone, and continuity audit. Cue-by-cue review alone is invalid.
5. Reviewer findings must identify affected source/target/context IDs,
   severity, domain, blocking status, problem, actionable change, and candidate
   digest. The reviewer, not the candidate author, supplies those findings.
6. The parent runtime records the reviewer output in a
   `ReviewAssemblyManifest`, then runs:
   `translation-cli assemble-review CANDIDATE SOURCE CONTEXT GLOSSARY MANIFEST --authority-root AUTHORITY --output REVIEW`.
   Use the same review profile ID/version in the manifest and finalization.
   The command reloads the selected candidate; a standalone review file cannot
   replace attempt recording or preflight selection.
7. Any finding triggers a candidate-wide audit for the same defect class before
   revision. Apply every result together rather than patching only named cues.
8. Route only blocking fidelity, speaker, culture, or lyrics risks to a focused
   reviewer. Focused review is advisory and cannot finalize the document.
9. Revalidate and independently rereview every changed candidate.
10. The final independent review must cover every source cue and all six
   domains: fidelity, speaker, culture, lyrics, naturalness, and segmentation.

Stop after two candidate-changing revisions for naturalness or segmentation,
or three for fidelity, speaker, culture, or lyrics, and escalate to user
confirmation. Escalate earlier when no new evidence or actionable strategy
exists. Rephrasing the same approach does not count as a revision.

## User confirmation

Ask only after autonomous attempts cannot responsibly decide. Show:

- the full source utterance and current best translation;
- why it remains difficult;
- checked dialogue, frames, screen text, and terminology;
- two to four complete alternatives;
- each alternative's meaning, strength, and mistranslation/cultural risk;
- one recommendation and its reason.

Accept one option ID or non-empty direct wording. Reject stale, duplicate,
unknown, or empty answers without changing the candidate. Bind the accepted
answer to the matching v2 unresolved decision, create a revised v2 candidate,
then run deterministic validation and a new independent review.

## Finalize

- Finalize only `translation-document.v2`.
- A finalized document must have canonical finalization metadata and contain
  no unresolved decisions, unassigned lineage, or unacknowledged blocking
  review findings.
- Preserve ready context, speakers, evidence references, target cue IDs,
  source cue lineage, source digest, and review acknowledgements.
- Never finalize or export a draft-source trial.
- Do not invoke the legacy v1 `translation-cli publish` command for a v2
  document.
- Export downstream subtitle formats only after the v2 document and assembled
  review both pass canonical validation.
- Save the actual actor's decision as `TranslationFinalization` in
  `FINALIZATION`, including `finalized_at`, `finalized_by`,
  `review_acknowledgement`, and `finding_dispositions`. With a current passing
  review, use `review_acknowledgement: passed` and an empty disposition array.
  Any human acceptance of an exact finding must come from the user.
- Finalize the selected revision, then deliver that exact finalized record:

  ```bash
  translation-cli finalize-translation CANDIDATE SOURCE CONTEXT GLOSSARY \
    REVIEW FINALIZATION --authority-root AUTHORITY \
    --review-profile PROFILE_ID PROFILE_VERSION --output FINAL
  translation-cli assemble-delivery FINAL SOURCE CONTEXT GLOSSARY REVIEW \
    TIMECODE_SPEC --authority-root AUTHORITY \
    --review-profile PROFILE_ID PROFILE_VERSION \
    --output-dir DELIVERY
  ```

  Delivery reads the selected document and revision IDs from `FINAL`.
  `TIMECODE_SPEC` is JSON with the video's `frame_rate_numerator`,
  `frame_rate_denominator`, and confirmed `drop_frame` boolean.
  Delivery requires a current complete review and verifies the supplied final
  record against the existing selection. An absent or stale review
  acknowledgement does not authorize delivery. The selected draft and attempt
  history remain unchanged.
- A translation run is not complete merely because an intermediate artifact
  was written or a task batch became idle. Completion requires the requested
  final artifact, deterministic validation evidence, and the final independent
  review result.

The governing product contract is
`docs/prd/context-rich-subtitle-translation.md`.
