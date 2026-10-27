# Copyright 2026 Transcription Module contributors

"""Atomic translation publication gate tests."""

from __future__ import annotations

from typing import Protocol, cast

import pytest

from tests.translation.publish_seam import PublicationError, publish_translation
from tests.translation.type_builders import milliseconds
from translation_module import review_routing as review_routing_module
from translation_module import review_state as review_state_module
from translation_module import workflow as workflow_module
from translation_module.common import SourceCueId, TargetCueId
from translation_module.contracts import (
    ContentKind,
    DocumentStatus,
    EpisodeContext,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
)
from translation_module.preparation import bind_source_digest
from translation_module.review_state import (
    ReviewAttempts,
)
from translation_module.validation import (
    TranslationValidationError,
    bind_translation_digest,
)
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    FindingSeverity,
    ReviewDomain,
    ReviewFinding,
    ReviewKind,
    ReviewResult,
)


class _PublishWithoutAssignment(Protocol):
    def __call__(
        self,
        source: SourceSnapshot,
        candidate: TranslationDocument,
        review: ReviewResult,
        *,
        attempts: ReviewAttempts,
    ) -> TranslationDocument:
        """Call publication with reviewer JSON but no parent assignment."""
        ...


class _PublishWithoutAttempts(Protocol):
    def __call__(
        self,
        source: SourceSnapshot,
        candidate: TranslationDocument,
        review: ReviewResult,
        *,
        assignment: workflow_module.ReviewAssignment,
    ) -> TranslationDocument:
        """Call the legacy publication shape without attempt state."""
        ...


def _source() -> SourceSnapshot:
    return bind_source_digest(
        SourceSnapshot(
            document_id="source-1",
            content_digest="0" * 64,
            maturity=SourceMaturity.FINALIZED,
            source_locale="ko-KR",
            duration_ms=milliseconds(1_000),
            context=EpisodeContext(summary="인사"),
            evidence=EvidenceManifest(base_uri="memory://source/"),
            cues=(
                SourceCue(
                    sequence=1,
                    cue_id=SourceCueId("source-cue-1"),
                    start_ms=milliseconds(0),
                    end_ms=milliseconds(1_000),
                    text="안녕.",
                    speakers=("민지",),
                    content_kind=ContentKind.DIALOGUE,
                ),
            ),
        )
    )


def _candidate(
    status: DocumentStatus,
    source: SourceSnapshot | None = None,
) -> TranslationDocument:
    source = source or _source()
    unresolved = ()
    if status is DocumentStatus.NEEDS_CONFIRMATION:
        unresolved = (
            UnresolvedItem(
                unresolved_id="question-1",
                source_cue_ids=(SourceCueId("source-cue-1"),),
                target_cue_ids=(TargetCueId("target-cue-1"),),
                current_translation="Hi.",
                why_difficult="The register needs a user decision.",
                alternatives=(
                    TranslationAlternative(
                        option_id="casual",
                        text="Hi.",
                        meaning="A casual greeting.",
                        strength="Natural and brief.",
                        risk="May be too casual.",
                    ),
                    TranslationAlternative(
                        option_id="neutral",
                        text="Hello.",
                        meaning="A neutral greeting.",
                        strength="Works in most contexts.",
                        risk="May sound formal.",
                    ),
                ),
                recommendation_id="casual",
                recommendation_reason="The scene is informal.",
            ),
        )
    draft = TranslationDocument.model_construct(
        document_id="translation-1",
        content_digest="0" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale=source.source_locale,
        target_locale="en-US",
        context=source.context,
        retained_contexts=(),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-cue-1"),
                source_cue_ids=(SourceCueId("source-cue-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Hi.",
                speakers=("민지",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=unresolved,
        status=status,
    )
    candidate = bind_translation_digest(draft)
    if not unresolved:
        return candidate
    return TranslationDocument.model_validate(
        candidate.model_dump()
        | {
            "unresolved_questions": (
                UnresolvedQuestion(
                    question_id="question-1",
                    question_text="Which register should this subtitle use?",
                    source_cue_ids=(SourceCueId("source-cue-1"),),
                    target_cue_ids=(TargetCueId("target-cue-1"),),
                    review_domain=ReviewDomain.NATURALNESS,
                    source_digest=source.content_digest,
                    candidate_digest=candidate.content_digest,
                    assignment_digest="b" * 64,
                    revision_session_id="revision-1",
                ),
            ),
        }
    )


def _review(
    candidate: TranslationDocument,
    *,
    assignment: workflow_module.ReviewAssignment | None = None,
    blocker: bool = False,
    actionable: bool = True,
    coverage: tuple[
        tuple[SourceCueId, ...],
        tuple[ReviewDomain, ...],
    ]
    | None = None,
) -> ReviewResult:
    findings = ()
    if blocker:
        findings = (
            ReviewFinding(
                finding_id="finding-1",
                candidate_digest=candidate.content_digest,
                affected=(
                    AffectedReference(
                        kind=AffectedKind.TARGET_CUE,
                        item_id="target-cue-1",
                    ),
                ),
                severity=FindingSeverity.MAJOR,
                domain=ReviewDomain.CULTURE,
                defect_class="register",
                blocking=True,
                problem="The register may be wrong.",
                required_change="Use a neutral greeting." if actionable else None,
            ),
        )
    reviewed_source_cue_ids, reviewed_domains = coverage or (
        (SourceCueId("source-cue-1"),),
        tuple(ReviewDomain),
    )
    assignment = assignment or _assignment(candidate)
    return ReviewResult(
        candidate_digest=candidate.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=reviewed_source_cue_ids,
        reviewed_target_cue_ids=tuple(cue.cue_id for cue in candidate.cues),
        reviewed_domains=reviewed_domains,
        completed_defect_audits=(
            (
                workflow_module.CompletedDefectAudit(
                    defect_class="register",
                    reviewed_source_cue_ids=reviewed_source_cue_ids,
                ),
            )
            if blocker
            else ()
        ),
        findings=findings,
    )


def _attempts(
    candidate: TranslationDocument,
    *,
    focused_domains_reviewed: tuple[ReviewDomain, ...] = (),
) -> ReviewAttempts:
    return ReviewAttempts(
        root_candidate_digest=candidate.content_digest,
        current_candidate_digest=candidate.content_digest,
        focused_domains_reviewed=focused_domains_reviewed,
    )


def _assignment(
    candidate: TranslationDocument,
    *,
    root_candidate_digest: str | None = None,
    current_candidate_digest: str | None = None,
    reviewer_execution_id: str = "reviewer-execution",
) -> workflow_module.ReviewAssignment:
    assignment = workflow_module.ReviewAssignment(
        assignment_digest="0" * 64,
        root_candidate_digest=root_candidate_digest or candidate.content_digest,
        current_candidate_digest=current_candidate_digest or candidate.content_digest,
        candidate_author_execution_id="candidate-execution",
        reviewer_execution_id=reviewer_execution_id,
        source_digest=candidate.source_digest,
        ordered_source_cue_ids=(SourceCueId("source-cue-1"),),
        ordered_target_cue_ids=(TargetCueId("target-cue-1"),),
        reviewed_domains=tuple(ReviewDomain),
    )
    return review_routing_module.bind_review_assignment_digest(assignment)


def _assigned_review(
    candidate: TranslationDocument,
    assignment: workflow_module.ReviewAssignment,
) -> workflow_module.ReviewResult:
    return workflow_module.ReviewResult(
        candidate_digest=candidate.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-cue-1"),),
        reviewed_target_cue_ids=(TargetCueId("target-cue-1"),),
        reviewed_domains=tuple(ReviewDomain),
    )


def test_review_assignment_round_trips_and_clean_review_publishes() -> None:
    """Characterize the existing assignment and clean-publication contract."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    assignment = _assignment(candidate)
    restored = workflow_module.ReviewAssignment.model_validate_json(
        assignment.model_dump_json()
    )

    published = publish_translation(
        _source(),
        candidate,
        _assigned_review(candidate, restored),
        assignment=restored,
        attempts=_attempts(candidate),
    )

    assert restored.candidate_author_execution_id == "candidate-execution"
    assert restored.reviewer_execution_id == "reviewer-execution"
    assert published is candidate


def test_review_json_alone_cannot_attest_independent_publication() -> None:
    """Publication requires parent-issued assignment evidence beside review JSON."""
    publish_without_assignment = cast(
        "_PublishWithoutAssignment",
        cast("object", publish_translation),
    )
    candidate = _candidate(DocumentStatus.FINALIZED)

    with pytest.raises(TypeError, match="assignment"):
        _ = publish_without_assignment(
            _source(),
            candidate,
            _review(candidate),
            attempts=_attempts(candidate),
        )


def test_publication_rejects_assignment_for_a_different_candidate() -> None:
    """A canonical assignment for stale candidate bytes cannot authorize publish."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    assignment = _assignment(
        candidate,
        root_candidate_digest="e" * 64,
        current_candidate_digest="e" * 64,
    )

    with pytest.raises(PublicationError, match=r"assignment.*candidate"):
        _ = publish_translation(
            _source(),
            candidate,
            _assigned_review(candidate, assignment),
            assignment=assignment,
            attempts=_attempts(candidate),
        )


def test_publication_rejects_noncanonical_assignment_digest() -> None:
    """Changing assignment fields invalidates its canonical digest binding."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    assignment = _assignment(candidate)
    tampered_assignment = assignment.model_copy(
        update={"reviewer_execution_id": "other-reviewer"}
    )

    with pytest.raises(PublicationError, match="assignment digest"):
        _ = publish_translation(
            _source(),
            candidate,
            _assigned_review(candidate, assignment),
            assignment=tampered_assignment,
            attempts=_attempts(candidate),
        )


def test_publication_rejects_review_bound_to_another_assignment() -> None:
    """Reviewer scope must reference the exact parent-issued assignment digest."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    assignment = _assignment(candidate)
    other_assignment = _assignment(candidate, reviewer_execution_id="other-reviewer")

    with pytest.raises(PublicationError, match=r"review.*assignment"):
        _ = publish_translation(
            _source(),
            candidate,
            _assigned_review(candidate, other_assignment),
            assignment=assignment,
            attempts=_attempts(candidate),
        )


def test_clean_independent_review_publishes_finalized_candidate() -> None:
    """A clean review publishes its exact finalized candidate."""
    candidate = _candidate(DocumentStatus.FINALIZED)

    published = publish_translation(
        _source(),
        candidate,
        _review(candidate),
        assignment=_assignment(candidate),
        attempts=_attempts(candidate),
    )

    assert published is candidate
    assert published.status is DocumentStatus.FINALIZED


def test_publication_rejects_review_scope_omitting_candidate_target() -> None:
    """Every published target cue requires exact independent-review coverage."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    first_target = candidate.cues[0].model_copy(update={"end_ms": milliseconds(500)})
    second_target = TargetCue(
        sequence=2,
        cue_id=TargetCueId("target-cue-2"),
        source_cue_ids=(SourceCueId("source-cue-1"),),
        start_ms=milliseconds(500),
        end_ms=milliseconds(1_000),
        text="Hello.",
        speakers=("민지",),
        content_kind=ContentKind.DIALOGUE,
    )
    candidate = bind_translation_digest(
        candidate.model_copy(update={"cues": (first_target, second_target)})
    )
    assignment = _assignment(candidate)
    review = _assigned_review(candidate, assignment)
    candidate_target_ids = tuple(cue.cue_id for cue in candidate.cues)

    assert candidate_target_ids == (
        TargetCueId("target-cue-1"),
        TargetCueId("target-cue-2"),
    )
    assert (
        assignment.ordered_target_cue_ids
        == review.reviewed_target_cue_ids
        == candidate_target_ids[:1]
    )

    with pytest.raises(PublicationError):
        _ = publish_translation(
            _source(),
            candidate,
            review,
            assignment=assignment,
            attempts=_attempts(candidate),
        )


def test_clean_independent_review_cannot_publish_draft_source() -> None:
    """A digest-bound draft remains ineligible after a clean review chain."""
    source = bind_source_digest(
        _source().model_copy(update={"maturity": SourceMaturity.DRAFT})
    )
    candidate = _candidate(DocumentStatus.FINALIZED, source)

    with pytest.raises(PublicationError, match="draft"):
        _ = publish_translation(
            source,
            candidate,
            _review(candidate),
            assignment=_assignment(candidate),
            attempts=_attempts(candidate),
        )


@pytest.mark.parametrize(
    ("reviewed_source_cue_ids", "reviewed_domains"),
    [
        ((SourceCueId("not-a-source-cue"),), tuple(ReviewDomain)),
        (
            (SourceCueId("source-cue-1"),),
            tuple(
                domain for domain in ReviewDomain if domain is not ReviewDomain.LYRICS
            ),
        ),
    ],
)
def test_publication_rejects_incomplete_independent_review_coverage(
    reviewed_source_cue_ids: tuple[SourceCueId, ...],
    reviewed_domains: tuple[ReviewDomain, ...],
) -> None:
    """Publication requires exact source-cue and six-domain review coverage."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    review = _review(
        candidate,
        coverage=(reviewed_source_cue_ids, reviewed_domains),
    )

    with pytest.raises(PublicationError, match="coverage"):
        _ = publish_translation(
            _source(),
            candidate,
            review,
            assignment=_assignment(candidate),
            attempts=_attempts(candidate),
        )


def test_publish_translation_requires_explicit_attempt_state() -> None:
    """Publication cannot silently reset omitted revision attempts to zero."""
    publish_without_attempts = cast(
        "_PublishWithoutAttempts", cast("object", publish_translation)
    )
    candidate = _candidate(DocumentStatus.FINALIZED)

    with pytest.raises(TypeError, match="attempts"):
        _ = publish_without_attempts(
            _source(),
            candidate,
            _review(candidate),
            assignment=_assignment(candidate),
        )


def test_attempt_state_cannot_route_a_different_candidate() -> None:
    """Revision-attempt state is bound to the exact candidate digest."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    stale_attempts = ReviewAttempts(
        root_candidate_digest="e" * 64,
        current_candidate_digest="e" * 64,
    )

    with pytest.raises(PublicationError, match="attempt chain"):
        _ = publish_translation(
            _source(),
            candidate,
            _review(candidate),
            assignment=_assignment(candidate),
            attempts=stale_attempts,
        )


def test_escalation_cannot_publish_before_an_answered_rereview_cycle() -> None:
    """Unresolved escalation stays blocked until an answered candidate is reviewed."""
    candidate = _candidate(DocumentStatus.NEEDS_CONFIRMATION)

    with pytest.raises(PublicationError):
        _ = publish_translation(
            _source(),
            candidate,
            _review(candidate, blocker=True, actionable=False),
            assignment=_assignment(candidate),
            attempts=_attempts(candidate),
        )


def test_revision_requires_current_focused_rereview_before_publication() -> None:
    """A clean full audit alone cannot skip focused rereview after revision."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    root_digest = "a" * 64
    attempts = ReviewAttempts(
        root_candidate_digest=root_digest,
        current_candidate_digest=candidate.content_digest,
        revisions=(
            review_state_module.RevisionEntry(
                before_candidate_digest=root_digest,
                after_candidate_digest=candidate.content_digest,
                risk_class=review_state_module.RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(ReviewDomain.CULTURE,),
                defect_classes=("register",),
                audited_source_cue_ids=(SourceCueId("source-cue-1"),),
            ),
        ),
    )
    assignment = _assignment(candidate, root_candidate_digest=root_digest)

    with pytest.raises(PublicationError, match="focused"):
        _ = publish_translation(
            _source(),
            candidate,
            _assigned_review(candidate, assignment),
            assignment=assignment,
            attempts=attempts,
        )


def test_forged_focused_attestation_cannot_authorize_revised_publication() -> None:
    """A kind-and-digest-only focused review is not parent-issued evidence."""
    candidate = _candidate(DocumentStatus.FINALIZED)
    root_digest = "a" * 64
    assignment = _assignment(candidate, root_candidate_digest=root_digest)
    focused_review = ReviewResult(
        candidate_digest=candidate.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-cue-1"),),
        reviewed_target_cue_ids=(TargetCueId("target-cue-1"),),
        reviewed_domains=(ReviewDomain.CULTURE,),
        kind=ReviewKind.FOCUSED,
    )
    attempts = ReviewAttempts.model_construct(
        root_candidate_digest=root_digest,
        current_candidate_digest=candidate.content_digest,
        revisions=(
            review_state_module.RevisionEntry(
                before_candidate_digest=root_digest,
                after_candidate_digest=candidate.content_digest,
                risk_class=review_state_module.RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(ReviewDomain.CULTURE,),
                defect_classes=("register",),
                audited_source_cue_ids=(SourceCueId("source-cue-1"),),
            ),
        ),
        focused_domains_reviewed=(),
        focused_review=focused_review,
        focused_assignment=assignment,
    )

    with pytest.raises(PublicationError, match="focused"):
        _ = publish_translation(
            _source(),
            candidate,
            _assigned_review(candidate, assignment),
            assignment=assignment,
            attempts=attempts,
        )


def test_deterministic_validation_runs_before_review_routing() -> None:
    """Invalid candidate bytes fail before stale review routing."""
    candidate = _candidate(DocumentStatus.FINALIZED).model_copy(
        update={"source_digest": "f" * 64}
    )
    stale_review = ReviewResult(
        candidate_digest="e" * 64,
        assignment_digest="f" * 64,
        reviewed_source_cue_ids=(SourceCueId("source-cue-1"),),
        reviewed_target_cue_ids=(TargetCueId("target-cue-1"),),
        reviewed_domains=tuple(ReviewDomain),
    )

    with pytest.raises(TranslationValidationError):
        _ = publish_translation(
            _source(),
            candidate,
            stale_review,
            assignment=_assignment(candidate),
            attempts=_attempts(candidate),
        )


def test_stale_review_and_new_user_answer_candidate_cannot_publish() -> None:
    """An answered candidate requires validation and a new exact review."""
    old_candidate = _candidate(DocumentStatus.NEEDS_CONFIRMATION)
    old_review = _review(old_candidate, blocker=True, actionable=False)
    answered_candidate = bind_translation_digest(
        old_candidate.model_copy(
            update={"status": DocumentStatus.FINALIZED, "unresolved": ()}
        )
    )
    before = answered_candidate.model_dump_json()

    with pytest.raises(PublicationError, match=r"review.*assignment"):
        _ = publish_translation(
            _source(),
            answered_candidate,
            old_review,
            assignment=_assignment(answered_candidate),
            attempts=_attempts(answered_candidate),
        )

    assert answered_candidate.model_dump_json() == before


@pytest.mark.parametrize(
    ("status", "review_kind"),
    [
        (DocumentStatus.NEEDS_CONFIRMATION, "clean"),
        (DocumentStatus.FINALIZED, "escalating"),
        (DocumentStatus.FINALIZED, "actionable"),
    ],
)
def test_route_or_status_mismatch_fails_without_mutation(
    status: DocumentStatus,
    review_kind: str,
) -> None:
    """Non-publishable routes and status mismatches leave input untouched."""
    candidate = _candidate(status)
    before = candidate.model_dump_json()

    with pytest.raises(PublicationError):
        _ = publish_translation(
            _source(),
            candidate,
            _review(
                candidate,
                blocker=review_kind != "clean",
                actionable=review_kind == "actionable",
            ),
            assignment=_assignment(candidate),
            attempts=_attempts(candidate),
        )

    assert candidate.model_dump_json() == before


def test_focused_review_cannot_publish_directly() -> None:
    """Focused review evidence cannot directly publish a candidate."""
    candidate = _candidate(DocumentStatus.FINALIZED)

    assignment = _assignment(candidate)
    focused_review = ReviewResult(
        candidate_digest=candidate.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-cue-1"),),
        reviewed_target_cue_ids=(TargetCueId("target-cue-1"),),
        reviewed_domains=(ReviewDomain.CULTURE,),
        kind=ReviewKind.FOCUSED,
    )

    with pytest.raises(PublicationError, match="focused"):
        _ = publish_translation(
            _source(),
            candidate,
            focused_review,
            assignment=assignment,
            attempts=_attempts(candidate),
        )


def test_clean_independent_rereview_after_focused_review_can_publish() -> None:
    """A later clean independent review owns the publication decision."""
    candidate = _candidate(DocumentStatus.FINALIZED)

    published = publish_translation(
        _source(),
        candidate,
        _review(candidate),
        assignment=_assignment(candidate),
        attempts=_attempts(
            candidate,
            focused_domains_reviewed=(ReviewDomain.CULTURE,),
        ),
    )

    assert published is candidate
