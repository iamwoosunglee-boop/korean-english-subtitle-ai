# Copyright 2026 Transcription Module contributors

"""Reusable strict values for canonical preflight tests."""

from pathlib import Path

from tests.translation.canonical_attempt_support import (
    attempt_binding,
    attempt_document,
)
from translation_module.canonical.attempts import (
    TranslationAttempt,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationLineRationale,
    translation_line_text_digest,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.preflight import (
    AdjacentCoverageResult,
    CandidatePreflightContext,
    GlossaryCoverageResult,
    PreflightCheckerEvidence,
    SemanticQualificationKind,
    SemanticQualificationResult,
)
from translation_module.canonical.source import SourceTranscriptV2

EXAMPLES = Path(__file__).parents[2] / "docs/contracts/examples"


def preflight_context(*, repeated_term: bool = False) -> CandidatePreflightContext:
    """Build a digest-valid two-target candidate and dependency graph."""
    source = SourceTranscriptV2.model_validate_json(
        (EXAMPLES / "source-transcript.v2.example.json").read_bytes()
    )
    glossary = TranslationGlossaryAsset.model_validate_json(
        (EXAMPLES / "translation-glossary.v1.example.json").read_bytes()
    )
    candidate = attempt_document()
    first = candidate.cues[0].model_copy(
        update={"text": "One.", "speakers": (candidate.cues[0].speakers[0],)}
    )
    second = first.model_copy(
        update={
            "sequence": 2,
            "cue_id": "target:000002",
            "start_ms": 1000,
            "end_ms": 2000,
            "text": "Two.",
        }
    )
    candidate = bind_canonical_digests(
        candidate.model_copy(update={"cues": (first, second)})
    )
    if repeated_term:
        first_source = source.cues[0].model_copy(update={"text": "세라"})
        second_source = first_source.model_copy(
            update={
                "sequence": 2,
                "cue_id": "source:000002",
                "start_ms": 1000,
                "end_ms": 2000,
            }
        )
        source = bind_canonical_digests(
            source.model_copy(
                update={
                    "duration_ms": 2000,
                    "cues": (first_source, second_source),
                }
            )
        )
        glossary = bind_canonical_digests(
            glossary.model_copy(
                update={
                    "prepared_from_source_ref": (
                        glossary.prepared_from_source_ref.model_copy(
                            update={"content_digest": source.content_digest}
                        )
                    )
                }
            )
        )
        candidate = bind_canonical_digests(
            candidate.model_copy(
                update={
                    "source_ref": candidate.source_ref.model_copy(
                        update={"content_digest": source.content_digest}
                    ),
                    "glossary_ref": candidate.glossary_ref.model_copy(
                        update={"content_digest": glossary.content_digest}
                    ),
                }
            )
        )
    return CandidatePreflightContext(candidate, source, glossary)


def checker_evidence(
    context: CandidatePreflightContext,
) -> PreflightCheckerEvidence:
    """Build exact empty semantic findings for every required scope."""
    adjacent = tuple(
        AdjacentCoverageResult(
            left_target_cue_id=left.cue_id,
            right_target_cue_id=right.cue_id,
            finding_ids=(),
        )
        for left, right in zip(
            context.candidate.cues,
            context.candidate.cues[1:],
            strict=False,
        )
    )
    glossary = ()
    if all("세라" in cue.text for cue in context.source.cues):
        glossary = (
            GlossaryCoverageResult(
                glossary_entry_id=context.glossary.entries[0].entry_id,
                source_cue_ids=tuple(cue.cue_id for cue in context.source.cues),
                finding_ids=(),
            ),
        )
    return PreflightCheckerEvidence(
        adjacent_results=adjacent,
        glossary_results=glossary,
        semantic_results=(
            SemanticQualificationResult(
                kind=SemanticQualificationKind.SOURCE_CLAIMS,
                source_cue_ids=tuple(cue.cue_id for cue in context.source.cues),
                target_cue_ids=tuple(cue.cue_id for cue in context.candidate.cues),
                reviewer_execution_ids=("source-claims-execution",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
            SemanticQualificationResult(
                kind=SemanticQualificationKind.TARGET_ONLY,
                source_cue_ids=(),
                target_cue_ids=tuple(cue.cue_id for cue in context.candidate.cues),
                reviewer_execution_ids=("target-only-execution",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
        ),
        findings=(),
    )


def preflight_attempt(context: CandidatePreflightContext) -> TranslationAttempt:
    """Build exact Todo 4 rationale coverage for one preflight candidate."""
    rationales = tuple(
        TranslationLineRationale(
            target_cue_id=cue.cue_id,
            line_number=line_number,
            speaker_id=cue.speakers[0],
            line_text_digest=translation_line_text_digest(line),
            rationale_ko="문맥과 화자의 말투를 반영했습니다.",
        )
        for cue in context.candidate.cues
        for line_number, line in enumerate(cue.text.split("\n"), start=1)
    )
    return TranslationAttempt(
        attempt_id="attempt-preflight",
        ordinal=1,
        kind=TranslationAttemptKind.INITIAL,
        outcome=TranslationAttemptOutcome.SUCCEEDED,
        binding=attempt_binding(context.candidate),
        candidate_digest=context.candidate.content_digest,
        rationales=rationales,
    )
