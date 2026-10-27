# Copyright 2026 Transcription Module contributors

"""Translator-reviewer routing and user-answer contract tests."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

import anyio
import pytest
from pydantic import ValidationError

from tests.translation.publish_seam import PublicationError, publish_translation
from tests.translation.type_builders import milliseconds
from translation_module import review_routing as review_routing_module
from translation_module import review_state as review_state_module
from translation_module import workflow as workflow_module
from translation_module.answers import (
    AcceptedTranslationAnswer,
    AnswerError,
    TranslationAnswer,
    accept_answer,
    answer_constraint,
)
from translation_module.common import ContentKind, SourceCueId, TargetCueId
from translation_module.contracts import (
    DocumentStatus,
    EpisodeContext,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
)
from translation_module.preparation import bind_source_digest
from translation_module.preparation_errors import PreparationError
from translation_module.review_preparation import (
    ReviewScope,
    build_review_request,
    review_assignment_from_request,
)
from translation_module.review_routing import (
    route_review,
)
from translation_module.review_state import (
    ReviewAttempts,
)
from translation_module.source import (
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)
from translation_module.validation import (
    bind_translation_digest,
    validate_translation,
)
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    FindingSeverity,
    ReviewAssignment,
    ReviewDomain,
    ReviewFinding,
    ReviewResult,
    RouteAction,
    StaleReviewError,
    WorkflowError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

CANDIDATE_DIGEST = "c" * 64


class _AcceptedAnswerCompletion(Protocol):
    question_id: str
    previous_candidate_digest: str
    candidate_digest: str
    accepted_tokens: tuple[str, ...]
    candidate: TranslationDocument


class _AcceptedAnswerSignal(Protocol):
    async def wait(self) -> _AcceptedAnswerCompletion:
        """Wait for the exact accepted-answer completion."""
        ...


class _AnswerResolution(Protocol):
    completion: _AcceptedAnswerCompletion


class _AnswerArbiter(Protocol):
    def completion_signal(self, question_id: str) -> _AcceptedAnswerSignal:
        """Subscribe before submitting competing answers."""
        ...

    async def apply_answer(
        self,
        question: UnresolvedQuestion,
        answer: AcceptedTranslationAnswer,
        candidate: TranslationDocument,
    ) -> _AnswerResolution:
        """Apply one answer candidate or return a typed duplicate result."""
        ...


class _AnswerLifecycleSeam(NamedTuple):
    prepare: Callable[[TranslationDocument, ReviewResult], _AnswerArbiter]
    error: type[Exception]
    accepted_result: type[object]
    duplicate_result: type[object]


def _answer_lifecycle() -> _AnswerLifecycleSeam:
    try:
        module = import_module("translation_module.answer_lifecycle")
    except ModuleNotFoundError as error:
        if error.name != "translation_module.answer_lifecycle":
            raise
        missing_module = "translation_module.answer_lifecycle"
        pytest.fail(
            f"missing public typed answer lifecycle seam: {missing_module}",
            pytrace=False,
        )
    try:
        return _AnswerLifecycleSeam(
            prepare=cast(
                "Callable[[TranslationDocument, ReviewResult], _AnswerArbiter]",
                module.prepare_answer_arbitration,
            ),
            error=cast("type[Exception]", module.AnswerLifecycleError),
            accepted_result=cast("type[object]", module.AcceptedAnswerResult),
            duplicate_result=cast("type[object]", module.DuplicateAnswerResult),
        )
    except AttributeError as error:
        pytest.fail(
            f"incomplete public typed answer lifecycle seam: {error.name}",
            pytrace=False,
        )


def _finding(
    domain: ReviewDomain,
    *,
    required_change: str | None = "Rewrite naturally.",
) -> ReviewFinding:
    return ReviewFinding(
        finding_id=f"finding-{domain.value}",
        candidate_digest=CANDIDATE_DIGEST,
        affected=(
            AffectedReference(
                kind=AffectedKind.SOURCE_CUE,
                item_id="source-1",
            ),
        ),
        severity=FindingSeverity.MAJOR,
        domain=domain,
        defect_class=f"{domain.value}-defect",
        blocking=True,
        problem="The current translation is not acceptable.",
        required_change=required_change,
    )


def _review(*findings: ReviewFinding) -> ReviewResult:
    defect_classes = tuple(dict.fromkeys(finding.defect_class for finding in findings))
    return ReviewResult(
        candidate_digest=CANDIDATE_DIGEST,
        assignment_digest="f" * 64,
        reviewed_source_cue_ids=(SourceCueId("source-1"),),
        reviewed_target_cue_ids=(),
        reviewed_domains=tuple(ReviewDomain),
        completed_defect_audits=tuple(
            workflow_module.CompletedDefectAudit(
                defect_class=defect_class,
                reviewed_source_cue_ids=(SourceCueId("source-1"),),
            )
            for defect_class in defect_classes
        ),
        findings=findings,
    )


def _attempts(*, general: int = 0, high_risk: int = 0) -> ReviewAttempts:
    risk_classes = (review_state_module.RevisionRiskClass.GENERAL,) * general + (
        review_state_module.RevisionRiskClass.HIGH_RISK,
    ) * high_risk
    if not risk_classes:
        return ReviewAttempts(
            root_candidate_digest=CANDIDATE_DIGEST,
            current_candidate_digest=CANDIDATE_DIGEST,
        )
    intermediate_digests = ("b" * 64, "d" * 64)
    before_digest = "a" * 64
    revisions: list[review_state_module.RevisionEntry] = []
    for index, risk_class in enumerate(risk_classes):
        after_digest = (
            CANDIDATE_DIGEST
            if index == len(risk_classes) - 1
            else intermediate_digests[index]
        )
        revisions.append(
            review_state_module.RevisionEntry(
                before_candidate_digest=before_digest,
                after_candidate_digest=after_digest,
                risk_class=risk_class,
                reviewed_domains=(
                    (ReviewDomain.NATURALNESS,)
                    if risk_class is review_state_module.RevisionRiskClass.GENERAL
                    else (ReviewDomain.CULTURE,)
                ),
                defect_classes=("review-defect",),
                audited_source_cue_ids=(SourceCueId("source-1"),),
            )
        )
        before_digest = after_digest
    return ReviewAttempts(
        root_candidate_digest="a" * 64,
        current_candidate_digest=CANDIDATE_DIGEST,
        revisions=tuple(revisions),
    )


def _document() -> TranslationDocument:
    alternatives = (
        TranslationAlternative(
            option_id="option-a",
            text="That was wild.",
            meaning="강한 놀람",
            strength="구어체로 자연스럽다.",
            risk="원문보다 강할 수 있다.",
        ),
        TranslationAlternative(
            option_id="option-b",
            text="That was amazing.",
            meaning="일반적인 감탄",
            strength="안전하고 자연스럽다.",
            risk="강한 반응이 약해질 수 있다.",
        ),
    )
    unresolved = UnresolvedItem(
        unresolved_id="question-1",
        source_cue_ids=(SourceCueId("source-1"),),
        target_cue_ids=(TargetCueId("target-1"),),
        current_translation="That was amazing.",
        why_difficult="두 강도가 모두 장면과 맞는다.",
        alternatives=alternatives,
        recommendation_id="option-a",
        recommendation_reason="표정은 강한 놀람에 가깝다.",
    )
    return TranslationDocument(
        document_id="translation-1",
        content_digest=CANDIDATE_DIGEST,
        source_document_id="source-1",
        source_digest="a" * 64,
        source_locale="ko-KR",
        target_locale="en-US",
        context=EpisodeContext(summary="토크쇼"),
        retained_contexts=(),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="That was amazing.",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=(unresolved,),
        unresolved_questions=(
            UnresolvedQuestion(
                question_id="question-1",
                question_text="Which intensity should this subtitle use?",
                source_cue_ids=(SourceCueId("source-1"),),
                target_cue_ids=(TargetCueId("target-1"),),
                review_domain=ReviewDomain.NATURALNESS,
                source_digest="a" * 64,
                candidate_digest=CANDIDATE_DIGEST,
                assignment_digest="b" * 64,
                revision_session_id="revision-1",
            ),
        ),
        status=DocumentStatus.NEEDS_CONFIRMATION,
    )


def _answer_fixture() -> tuple[
    SourceSnapshot,
    TranslationDocument,
    ReviewAssignment,
    ReviewResult,
]:
    source = bind_source_digest(
        SourceSnapshot(
            document_id="answer-source",
            content_digest="0" * 64,
            maturity=SourceMaturity.FINALIZED,
            source_locale="ko-KR",
            duration_ms=milliseconds(2_000),
            context=EpisodeContext(summary="A reaction."),
            evidence=EvidenceManifest(base_uri="memory://answer-source/"),
            cues=(
                SourceCue(
                    sequence=1,
                    cue_id=SourceCueId("source-1"),
                    start_ms=milliseconds(0),
                    end_ms=milliseconds(2_000),
                    text="정말 대단했어.",
                    speakers=("speaker",),
                    content_kind=ContentKind.DIALOGUE,
                ),
            ),
        )
    )
    unresolved = UnresolvedItem(
        unresolved_id="question-1",
        source_cue_ids=(SourceCueId("source-1"),),
        target_cue_ids=(
            TargetCueId("target-1"),
            TargetCueId("target-2"),
        ),
        current_translation="That was amazing.",
        why_difficult="The reaction intensity remains unresolved.",
        alternatives=(
            TranslationAlternative(
                option_id="option-a",
                text="That was wild.",
                meaning="strong surprise",
                strength="direct",
                risk="stronger intensity",
            ),
            TranslationAlternative(
                option_id="option-b",
                text="That was amazing.",
                meaning="general praise",
                strength="neutral",
                risk="weaker intensity",
            ),
        ),
        recommendation_id="option-a",
        recommendation_reason="The reaction is strong.",
    )
    draft = TranslationDocument.model_construct(
        document_id="answer-document",
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
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="That was",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
            ),
            TargetCue(
                sequence=2,
                cue_id=TargetCueId("target-2"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="amazing.",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=(unresolved,),
        status=DocumentStatus.NEEDS_CONFIRMATION,
    )
    document = bind_translation_digest(draft)
    assignment = review_routing_module.bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=document.content_digest,
            current_candidate_digest=document.content_digest,
            candidate_author_execution_id="answer-author",
            reviewer_execution_id="answer-reviewer",
            source_digest=source.content_digest,
            ordered_source_cue_ids=(SourceCueId("source-1"),),
            ordered_target_cue_ids=(
                TargetCueId("target-1"),
                TargetCueId("target-2"),
            ),
            reviewed_domains=tuple(ReviewDomain),
        )
    )
    question = UnresolvedQuestion(
        question_id=unresolved.unresolved_id,
        question_text="Which intensity should this subtitle use?",
        source_cue_ids=unresolved.source_cue_ids,
        target_cue_ids=unresolved.target_cue_ids,
        review_domain=ReviewDomain.NATURALNESS,
        source_digest=source.content_digest,
        candidate_digest=document.content_digest,
        assignment_digest=assignment.assignment_digest,
        revision_session_id="answer-revision",
    )
    document = TranslationDocument.model_validate(
        document.model_dump() | {"unresolved_questions": (question,)}
    )
    finding = ReviewFinding(
        finding_id=unresolved.unresolved_id,
        candidate_digest=document.content_digest,
        affected=tuple(
            AffectedReference(kind=AffectedKind.TARGET_CUE, item_id=target_id)
            for target_id in unresolved.target_cue_ids
        ),
        severity=FindingSeverity.MAJOR,
        domain=question.review_domain,
        defect_class="reaction-intensity",
        blocking=True,
        problem="The available evidence cannot settle the intensity.",
        required_change=None,
    )
    review = ReviewResult(
        candidate_digest=document.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-1"),),
        reviewed_target_cue_ids=tuple(cue.cue_id for cue in document.cues),
        reviewed_domains=tuple(ReviewDomain),
        completed_defect_audits=(
            workflow_module.CompletedDefectAudit(
                defect_class=finding.defect_class,
                reviewed_source_cue_ids=(SourceCueId("source-1"),),
            ),
        ),
        findings=(finding,),
    )
    return source, document, assignment, review


def _answer_candidate(
    document: TranslationDocument,
    *,
    first_text: str,
    second_text: str,
) -> TranslationDocument:
    cues = (
        document.cues[0].model_copy(
            update={
                "end_ms": milliseconds(900),
                "text": first_text,
            }
        ),
        document.cues[1].model_copy(
            update={
                "start_ms": milliseconds(900),
                "text": second_text,
            }
        ),
    )
    candidate = bind_translation_digest(
        document.model_copy(
            update={
                "cues": cues,
                "unresolved": (),
                "unresolved_questions": (),
                "status": DocumentStatus.FINALIZED,
            }
        )
    )
    return TranslationDocument.model_validate(candidate.model_dump())


def _answer_attempts(document: TranslationDocument) -> ReviewAttempts:
    return ReviewAttempts(
        root_candidate_digest=document.content_digest,
        current_candidate_digest=document.content_digest,
    )


def test_review_request_binds_exact_scope_sessions_and_reviewer_input() -> None:
    """The parent builds complete reviewer input without translator rationale."""
    source = bind_source_digest(
        SourceSnapshot(
            document_id="source",
            content_digest="0" * 64,
            maturity=SourceMaturity.FINALIZED,
            source_locale="ko-KR",
            duration_ms=milliseconds(2_000),
            context=EpisodeContext(summary="A continuous exchange."),
            evidence=EvidenceManifest(base_uri="memory://source/"),
            cues=tuple(
                SourceCue(
                    sequence=index,
                    cue_id=SourceCueId(f"source-{index}"),
                    start_ms=milliseconds((index - 1) * 1_000),
                    end_ms=milliseconds(index * 1_000),
                    text=f"utterance {index}",
                    speakers=("speaker",),
                    content_kind=ContentKind.DIALOGUE,
                    context_note=f"review context {index}",
                    screen_text="ON SCREEN" if index == 1 else None,
                )
                for index in (1, 2)
            ),
        )
    )
    candidate = bind_translation_digest(
        TranslationDocument(
            document_id="candidate",
            content_digest="0" * 64,
            source_document_id=source.document_id,
            source_digest=source.content_digest,
            source_locale="ko-KR",
            target_locale="en-US",
            context=source.context,
            retained_contexts=(),
            cues=(
                TargetCue(
                    sequence=1,
                    cue_id=TargetCueId("target-1"),
                    source_cue_ids=(SourceCueId("source-1"),),
                    start_ms=milliseconds(0),
                    end_ms=milliseconds(1_000),
                    text="Hi.",
                    speakers=("speaker",),
                    content_kind=ContentKind.DIALOGUE,
                ),
            ),
            status=DocumentStatus.FINALIZED,
        )
    )

    request = build_review_request(
        source,
        candidate,
        ReviewScope(
            source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
            target_cue_ids=(TargetCueId("target-1"),),
        ),
        ("translator", "reviser", "assembler", "reviewer"),
    )

    assert request.source_cue_ids == ("source-1", "source-2")
    assert request.reviewed_domains == tuple(ReviewDomain)
    assert request.source_cues[0].screen_text == "ON SCREEN"
    assert tuple(cue.context_note for cue in request.source_cues) == (
        "review context 1",
        "review context 2",
    )
    assert request.read_only_neighbors == ()
    assert "review context" not in request.context.summary
    assert "rationale" not in request.model_dump()
    assignment = review_assignment_from_request(request)
    unknown_target = ReviewResult(
        candidate_digest=candidate.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=request.source_cue_ids,
        reviewed_target_cue_ids=request.target_cue_ids,
        reviewed_domains=request.reviewed_domains,
        findings=(
            ReviewFinding(
                finding_id="unknown-target",
                candidate_digest=candidate.content_digest,
                affected=(
                    AffectedReference(
                        kind=AffectedKind.TARGET_CUE,
                        item_id="unknown-target",
                    ),
                ),
                severity=FindingSeverity.MAJOR,
                domain=ReviewDomain.FIDELITY,
                defect_class="scope",
                blocking=False,
                problem="Unknown target.",
            ),
        ),
    )
    with pytest.raises(WorkflowError, match="affected"):
        _ = route_review(
            unknown_target,
            expected_candidate_digest=candidate.content_digest,
            attempts=ReviewAttempts(
                root_candidate_digest=candidate.content_digest,
                current_candidate_digest=candidate.content_digest,
            ),
            assignment=assignment,
        )
    target_scope_mismatch = unknown_target.model_copy(
        update={"reviewed_target_cue_ids": ()}
    )
    with pytest.raises(WorkflowError, match="target scope"):
        _ = route_review(
            target_scope_mismatch,
            expected_candidate_digest=candidate.content_digest,
            attempts=ReviewAttempts(
                root_candidate_digest=candidate.content_digest,
                current_candidate_digest=candidate.content_digest,
            ),
            assignment=assignment,
        )
    scope_less = ReviewAssignment(
        assignment_digest="f" * 64,
        root_candidate_digest=candidate.content_digest,
        current_candidate_digest=candidate.content_digest,
        candidate_author_execution_id="author",
        reviewer_execution_id="reviewer",
    )
    with pytest.raises(WorkflowError, match="scope"):
        _ = route_review(
            unknown_target.model_copy(
                update={"assignment_digest": scope_less.assignment_digest}
            ),
            expected_candidate_digest=candidate.content_digest,
            attempts=ReviewAttempts(
                root_candidate_digest=candidate.content_digest,
                current_candidate_digest=candidate.content_digest,
            ),
            assignment=scope_less,
        )
    with pytest.raises(PreparationError, match="six"):
        _ = review_assignment_from_request(
            request.model_copy(
                update={"reviewed_domains": request.reviewed_domains[:-1]}
            )
        )
    with pytest.raises(PreparationError, match="unknown source cue"):
        _ = build_review_request(
            source,
            candidate,
            ReviewScope(source_cue_ids=(SourceCueId("missing"),), target_cue_ids=()),
            ("translator", "reviser", "assembler", "reviewer"),
        )
    with pytest.raises(PreparationError, match="digest mismatch"):
        _ = build_review_request(
            source.model_copy(update={"context": EpisodeContext(summary="stale")}),
            candidate,
            ReviewScope(source_cue_ids=(SourceCueId("source-1"),), target_cue_ids=()),
            ("translator", "reviser", "assembler", "reviewer"),
        )
    with pytest.raises(PreparationError, match="different"):
        _ = build_review_request(
            source,
            candidate,
            ReviewScope(source_cue_ids=(SourceCueId("source-1"),), target_cue_ids=()),
            ("translator", "reviser", "assembler", "assembler"),
        )


def test_independent_review_rejects_empty_declared_scope() -> None:
    """An independent review cannot claim approval without reviewing a scope."""
    with pytest.raises(ValidationError, match="scope"):
        _ = ReviewResult(
            candidate_digest=CANDIDATE_DIGEST,
            assignment_digest="f" * 64,
            reviewed_source_cue_ids=(),
            reviewed_target_cue_ids=(),
            reviewed_domains=(),
        )


def test_review_result_cannot_declare_authoritative_execution_identity() -> None:
    """Reviewer-authored JSON cannot itself attest reviewer separation."""
    with pytest.raises(ValidationError):
        _ = ReviewResult.model_validate(
            {
                "candidate_digest": CANDIDATE_DIGEST,
                "candidate_author_execution_id": "candidate-execution",
                "reviewer_execution_id": "reviewer-execution",
                "reviewed_source_cue_ids": (SourceCueId("source-1"),),
                "reviewed_domains": (ReviewDomain.FIDELITY,),
            }
        )


def test_review_assignment_requires_different_execution_ids() -> None:
    """Parent-issued assignment declarations must name separate executions."""
    with pytest.raises(ValidationError, match="different execution IDs"):
        _ = workflow_module.ReviewAssignment(
            assignment_digest="f" * 64,
            root_candidate_digest=CANDIDATE_DIGEST,
            current_candidate_digest=CANDIDATE_DIGEST,
            candidate_author_execution_id="same-execution",
            reviewer_execution_id="same-execution",
        )


def test_reset_attempt_chain_rejects_revised_candidate() -> None:
    """A revised candidate cannot claim zero attempts with an empty chain."""
    with pytest.raises(ValidationError, match="chain"):
        _ = review_state_module.ReviewAttempts(
            root_candidate_digest="a" * 64,
            current_candidate_digest=CANDIDATE_DIGEST,
            revisions=(),
        )


def test_attempt_chain_rejects_discontinuous_revisions() -> None:
    """Every revision must start from the prior revision's output digest."""
    with pytest.raises(ValidationError, match="continuous"):
        _ = review_state_module.ReviewAttempts(
            root_candidate_digest="a" * 64,
            current_candidate_digest=CANDIDATE_DIGEST,
            revisions=(
                review_state_module.RevisionEntry(
                    before_candidate_digest="a" * 64,
                    after_candidate_digest="b" * 64,
                    risk_class=review_state_module.RevisionRiskClass.GENERAL,
                    reviewed_domains=(ReviewDomain.NATURALNESS,),
                    defect_classes=("register",),
                    audited_source_cue_ids=(SourceCueId("source-1"),),
                ),
                review_state_module.RevisionEntry(
                    before_candidate_digest="d" * 64,
                    after_candidate_digest=CANDIDATE_DIGEST,
                    risk_class=review_state_module.RevisionRiskClass.GENERAL,
                    reviewed_domains=(ReviewDomain.NATURALNESS,),
                    defect_classes=("register",),
                    audited_source_cue_ids=(SourceCueId("source-1"),),
                ),
            ),
        )


def test_blocker_routes_to_audit_until_defect_class_scope_is_complete() -> None:
    """A blocker cannot route onward before its class-wide candidate audit."""
    finding = workflow_module.ReviewFinding(
        finding_id="finding-culture",
        candidate_digest=CANDIDATE_DIGEST,
        affected=(AffectedReference(kind=AffectedKind.SOURCE_CUE, item_id="source-1"),),
        severity=FindingSeverity.MAJOR,
        domain=ReviewDomain.CULTURE,
        defect_class="register",
        blocking=True,
        problem="The register is wrong.",
        required_change="Use a neutral greeting.",
    )
    review = workflow_module.ReviewResult(
        candidate_digest=CANDIDATE_DIGEST,
        assignment_digest="f" * 64,
        reviewed_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
        reviewed_target_cue_ids=(),
        reviewed_domains=tuple(ReviewDomain),
        findings=(finding,),
        completed_defect_audits=(
            workflow_module.CompletedDefectAudit(
                defect_class="register",
                reviewed_source_cue_ids=(SourceCueId("source-1"),),
            ),
        ),
    )

    route = route_review(
        review,
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=review_state_module.ReviewAttempts(
            root_candidate_digest=CANDIDATE_DIGEST,
            current_candidate_digest=CANDIDATE_DIGEST,
        ),
    )

    assert route.action is RouteAction.AUDIT
    assert route.audit_defect_classes == ("register",)


def test_revision_audit_scope_must_cover_candidate_review_scope() -> None:
    """A revision entry cannot claim a partial audit as candidate-wide."""
    review = workflow_module.ReviewResult(
        candidate_digest=CANDIDATE_DIGEST,
        assignment_digest="f" * 64,
        reviewed_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
        reviewed_target_cue_ids=(),
        reviewed_domains=tuple(ReviewDomain),
    )
    attempts = review_state_module.ReviewAttempts(
        root_candidate_digest="a" * 64,
        current_candidate_digest=CANDIDATE_DIGEST,
        revisions=(
            review_state_module.RevisionEntry(
                before_candidate_digest="a" * 64,
                after_candidate_digest=CANDIDATE_DIGEST,
                risk_class=review_state_module.RevisionRiskClass.GENERAL,
                reviewed_domains=(ReviewDomain.NATURALNESS,),
                defect_classes=("register",),
                audited_source_cue_ids=(SourceCueId("source-1"),),
            ),
        ),
    )

    with pytest.raises(workflow_module.WorkflowError, match="candidate review scope"):
        _ = route_review(
            review,
            expected_candidate_digest=CANDIDATE_DIGEST,
            attempts=attempts,
        )


def test_findings_of_any_severity_prevent_finalization_until_audit() -> None:
    """A retained finding cannot be silently accepted as final."""
    finding = _finding(ReviewDomain.NATURALNESS).model_copy(update={"blocking": False})

    route = route_review(
        _review(finding).model_copy(update={"completed_defect_audits": ()}),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(),
    )

    assert route.action is RouteAction.AUDIT


def test_focused_review_must_stay_within_parent_affected_scope() -> None:
    """A focused review is a narrow parent-issued follow-up, not a new audit."""
    assignment = review_routing_module.bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=CANDIDATE_DIGEST,
            current_candidate_digest=CANDIDATE_DIGEST,
            candidate_author_execution_id="author",
            reviewer_execution_id="reviewer",
            source_digest="a" * 64,
            ordered_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
            ordered_target_cue_ids=(TargetCueId("target-1"), TargetCueId("target-2")),
            reviewed_domains=tuple(ReviewDomain),
            focused_source_cue_ids=(SourceCueId("source-1"),),
            focused_target_cue_ids=(TargetCueId("target-1"),),
            focused_domains=(ReviewDomain.CULTURE,),
        )
    )
    review = ReviewResult(
        candidate_digest=CANDIDATE_DIGEST,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-2"),),
        reviewed_target_cue_ids=(TargetCueId("target-2"),),
        reviewed_domains=(ReviewDomain.CULTURE,),
        kind=workflow_module.ReviewKind.FOCUSED,
    )

    with pytest.raises(WorkflowError, match="focused"):
        _ = route_review(
            review,
            expected_candidate_digest=CANDIDATE_DIGEST,
            attempts=_attempts(),
            assignment=assignment,
        )


def test_review_without_blockers_finalizes() -> None:
    """A clean independent review routes to finalization."""
    route = route_review(
        _review(),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(),
    )

    assert route.action is RouteAction.FINALIZE


def test_high_risk_blocker_routes_to_focused_review_first() -> None:
    """An unreviewed culture blocker requests focused review."""
    route = route_review(
        _review(_finding(ReviewDomain.CULTURE)),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(),
    )

    assert route.action is RouteAction.FOCUSED_REVIEW
    assert route.focused_domains == (ReviewDomain.CULTURE,)


def test_focused_high_risk_blocker_routes_to_revision() -> None:
    """A focused blocker routes to a new candidate revision."""
    route = route_review(
        _review(_finding(ReviewDomain.CULTURE)),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(high_risk=1),
        focused_domains_reviewed=(ReviewDomain.CULTURE,),
    )

    assert route.action is RouteAction.REVISE


@pytest.mark.parametrize(
    ("domain", "general_attempts", "high_risk_attempts"),
    [
        (ReviewDomain.NATURALNESS, 2, 0),
        (ReviewDomain.CULTURE, 0, 3),
    ],
)
def test_revision_limit_routes_to_user(
    domain: ReviewDomain,
    general_attempts: int,
    high_risk_attempts: int,
) -> None:
    """Approved revision caps route unresolved blockers to the user."""
    route = route_review(
        _review(_finding(domain)),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(
            general=general_attempts,
            high_risk=high_risk_attempts,
        ),
        focused_domains_reviewed=(domain,),
    )

    assert route.action is RouteAction.ESCALATE


def test_blocker_without_actionable_strategy_escalates_immediately() -> None:
    """A blocker without a new strategy escalates before retry limits."""
    route = route_review(
        _review(
            _finding(
                ReviewDomain.FIDELITY,
                required_change=None,
            )
        ),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(),
        focused_domains_reviewed=(ReviewDomain.FIDELITY,),
    )

    assert route.action is RouteAction.ESCALATE


def test_naturalness_revisions_count_against_general_cap_despite_high_risk_label() -> (
    None
):
    """Caller-selected risk labels cannot hide a general-domain revision."""
    attempts = ReviewAttempts(
        root_candidate_digest="a" * 64,
        current_candidate_digest=CANDIDATE_DIGEST,
        revisions=(
            review_state_module.RevisionEntry(
                before_candidate_digest="a" * 64,
                after_candidate_digest="b" * 64,
                risk_class=review_state_module.RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(ReviewDomain.NATURALNESS,),
                defect_classes=("naturalness-defect",),
                audited_source_cue_ids=(SourceCueId("source-1"),),
            ),
            review_state_module.RevisionEntry(
                before_candidate_digest="b" * 64,
                after_candidate_digest="d" * 64,
                risk_class=review_state_module.RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(ReviewDomain.NATURALNESS,),
                defect_classes=("naturalness-defect",),
                audited_source_cue_ids=(SourceCueId("source-1"),),
            ),
            review_state_module.RevisionEntry(
                before_candidate_digest="d" * 64,
                after_candidate_digest=CANDIDATE_DIGEST,
                risk_class=review_state_module.RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(ReviewDomain.NATURALNESS,),
                defect_classes=("naturalness-defect",),
                audited_source_cue_ids=(SourceCueId("source-1"),),
            ),
        ),
    )

    route = route_review(
        _review(_finding(ReviewDomain.NATURALNESS)),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=attempts,
    )

    assert route.action is RouteAction.ESCALATE


def test_stale_review_cannot_route_changed_candidate() -> None:
    """Review bytes from an older candidate cannot control routing."""
    with pytest.raises(StaleReviewError):
        _ = route_review(
            _review(),
            expected_candidate_digest="d" * 64,
            attempts=_attempts(),
        )


def test_user_answer_selects_complete_alternative() -> None:
    """An option answer becomes a binding complete translation."""
    constraint = answer_constraint(
        _document(),
        TranslationAnswer(
            document_id="translation-1",
            document_digest=CANDIDATE_DIGEST,
            unresolved_id="question-1",
            alternative_id="option-a",
        ),
    )

    assert constraint.binding_text == "That was wild."
    assert constraint.source_cue_ids == ("source-1",)


def test_stale_user_answer_preserves_published_document() -> None:
    """A stale answer fails without mutating the published document."""
    document = _document()
    before = document.model_dump_json()

    with pytest.raises(AnswerError, match="stale"):
        _ = answer_constraint(
            document,
            TranslationAnswer(
                document_id="translation-1",
                document_digest="d" * 64,
                unresolved_id="question-1",
                direct_text="That was incredible.",
            ),
        )

    assert document.model_dump_json() == before


@pytest.mark.parametrize(
    "domain",
    [ReviewDomain.NATURALNESS, ReviewDomain.SEGMENTATION],
)
def test_nonblocking_general_findings_obey_revision_cap(
    domain: ReviewDomain,
) -> None:
    """Nonblocking general findings revise below the cap and escalate at it."""
    finding = _finding(domain).model_copy(update={"blocking": False})

    before_cap = route_review(
        _review(finding),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(general=1),
    )
    exhausted = route_review(
        _review(finding),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(general=2),
    )

    assert (before_cap.action, exhausted.action) == (
        RouteAction.REVISE,
        RouteAction.ESCALATE,
    )


@pytest.mark.parametrize(
    "domain",
    [
        ReviewDomain.FIDELITY,
        ReviewDomain.SPEAKER,
        ReviewDomain.CULTURE,
        ReviewDomain.LYRICS,
    ],
)
def test_nonblocking_high_risk_findings_audit_then_obey_revision_cap(
    domain: ReviewDomain,
) -> None:
    """Nonblocking high-risk findings audit and obey caps without focus."""
    finding = _finding(domain).model_copy(update={"blocking": False})

    unaudited = route_review(
        _review(finding).model_copy(update={"completed_defect_audits": ()}),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(high_risk=2),
    )
    before_cap = route_review(
        _review(finding),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(high_risk=2),
    )
    exhausted = route_review(
        _review(finding),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(high_risk=3),
    )

    assert unaudited.action is RouteAction.AUDIT
    assert (before_cap.action, exhausted.action) == (
        RouteAction.REVISE,
        RouteAction.ESCALATE,
    )
    assert before_cap.focused_domains == ()
    assert exhausted.focused_domains == ()


def test_focused_review_domains_include_only_blocking_high_risk_findings() -> None:
    """Focused review includes only blocking high-risk domains in enum order."""
    route = route_review(
        _review(
            _finding(ReviewDomain.CULTURE).model_copy(update={"blocking": False}),
            _finding(ReviewDomain.LYRICS),
            _finding(ReviewDomain.FIDELITY).model_copy(update={"blocking": False}),
            _finding(ReviewDomain.SPEAKER),
        ),
        expected_candidate_digest=CANDIDATE_DIGEST,
        attempts=_attempts(),
    )

    assert route.action is RouteAction.FOCUSED_REVIEW
    assert route.focused_domains == (
        ReviewDomain.SPEAKER,
        ReviewDomain.LYRICS,
    )


@pytest.mark.parametrize("bijection_failure", ["missing_blocker", "extra_blocker"])
def test_answer_binding_rejects_non_bijective_blockers(
    bijection_failure: str,
) -> None:
    """Answer arbitration requires one unresolvable blocker per unresolved item."""
    seam = _answer_lifecycle()
    _, document, _, review = _answer_fixture()
    if bijection_failure == "missing_blocker":
        invalid_review = review.model_copy(update={"findings": ()})
    else:
        extra = review.findings[0].model_copy(update={"finding_id": "question-2"})
        invalid_review = review.model_copy(
            update={"findings": (*review.findings, extra)}
        )

    with pytest.raises(seam.error):
        _ = seam.prepare(document, invalid_review)


def test_answer_binding_rejects_actionable_blocker() -> None:
    """Only blockers without an autonomous resolution can become questions."""
    seam = _answer_lifecycle()
    _, document, _, review = _answer_fixture()
    actionable = review.findings[0].model_copy(
        update={"required_change": "Revise the candidate."}
    )

    with pytest.raises(seam.error):
        _ = seam.prepare(
            document,
            review.model_copy(update={"findings": (actionable,)}),
        )


def test_answer_resegmentation_rejects_token_loss() -> None:
    """Resegmented target scope cannot omit or replace accepted wording tokens."""
    seam = _answer_lifecycle()
    _, document, _, review = _answer_fixture()
    question = document.unresolved_questions[0]
    answer = accept_answer(question, alternative_id="option-a")
    candidate = _answer_candidate(
        document,
        first_text="That was",
        second_text="amazing.",
    )
    arbiter = seam.prepare(document, review)

    async def apply_invalid_projection() -> None:
        _ = await arbiter.apply_answer(question, answer, candidate)

    with pytest.raises(seam.error):
        anyio.run(apply_invalid_projection)


def test_answer_binding_rejects_stale_resegmented_candidate() -> None:
    """A noncanonical proposed digest cannot complete an accepted answer."""
    seam = _answer_lifecycle()
    _, document, _, review = _answer_fixture()
    question = document.unresolved_questions[0]
    answer = accept_answer(question, alternative_id="option-a")
    candidate = _answer_candidate(
        document,
        first_text="That was",
        second_text="wild.",
    ).model_copy(update={"content_digest": "d" * 64})
    arbiter = seam.prepare(document, review)

    async def apply_stale_candidate() -> None:
        _ = await arbiter.apply_answer(question, answer, candidate)

    with pytest.raises(seam.error):
        anyio.run(apply_stale_candidate)


def test_answer_concurrent_conflict_emits_one_exact_completion() -> None:
    """Competing answers yield one completion and one typed duplicate result."""
    seam = _answer_lifecycle()
    _, document, _, review = _answer_fixture()
    question = document.unresolved_questions[0]
    first_answer = accept_answer(question, alternative_id="option-a")
    second_answer = accept_answer(question, direct_text="That was vivid.")
    first_candidate = _answer_candidate(
        document,
        first_text="That was",
        second_text="wild.",
    )
    second_candidate = _answer_candidate(
        document,
        first_text="That was",
        second_text="vivid.",
    )
    before = document.model_dump_json()
    arbiter = seam.prepare(document, review)
    signal = arbiter.completion_signal(question.question_id)
    results: list[_AnswerResolution] = []

    async def submit(
        answer: AcceptedTranslationAnswer,
        candidate: TranslationDocument,
    ) -> None:
        results.append(await arbiter.apply_answer(question, answer, candidate))

    async def scenario() -> _AcceptedAnswerCompletion | None:
        emitted: _AcceptedAnswerCompletion | None = None
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(submit, first_answer, first_candidate)
            _ = tasks.start_soon(submit, second_answer, second_candidate)
            with anyio.fail_after(1):
                emitted = await signal.wait()
        return emitted

    emitted = anyio.run(scenario)

    assert emitted is not None
    accepted = [item for item in results if type(item) is seam.accepted_result]
    duplicates = [item for item in results if type(item) is seam.duplicate_result]
    assert len(accepted) == len(duplicates) == 1
    assert accepted[0].completion == duplicates[0].completion == emitted
    assert emitted.candidate_digest in {
        first_candidate.content_digest,
        second_candidate.content_digest,
    }
    assert emitted.candidate.content_digest == emitted.candidate_digest
    assert document.model_dump_json() == before


def test_answer_resegmentation_binding_completes_fresh_review_lifecycle() -> None:
    """An applied answer completes only after validation and exact fresh rereview."""
    seam = _answer_lifecycle()
    source, document, old_assignment, escalation_review = _answer_fixture()
    question = document.unresolved_questions[0]
    answer = accept_answer(question, alternative_id="option-a")
    proposed = _answer_candidate(
        document,
        first_text="That   was",
        second_text="wild.",
    )
    escalation_route = route_review(
        escalation_review,
        expected_candidate_digest=document.content_digest,
        attempts=_answer_attempts(document),
        assignment=old_assignment,
    )
    assert escalation_route.action is RouteAction.ESCALATE

    arbiter = seam.prepare(document, escalation_review)
    signal = arbiter.completion_signal(question.question_id)

    async def apply_and_wait() -> tuple[
        _AnswerResolution | None,
        _AcceptedAnswerCompletion | None,
    ]:
        resolution: _AnswerResolution | None = None
        completion: _AcceptedAnswerCompletion | None = None
        resolution = await arbiter.apply_answer(question, answer, proposed)
        with anyio.fail_after(1):
            completion = await signal.wait()
        return resolution, completion

    resolution, completion = anyio.run(apply_and_wait)

    assert resolution is not None
    assert completion is not None
    assert type(resolution) is seam.accepted_result
    assert resolution.completion == completion
    assert completion.question_id == question.question_id
    assert completion.previous_candidate_digest == document.content_digest
    assert completion.candidate_digest != document.content_digest
    assert completion.candidate_digest == proposed.content_digest
    assert completion.accepted_tokens == ("That", "was", "wild.")
    assert completion.candidate.status is DocumentStatus.FINALIZED
    assert completion.candidate.unresolved == ()
    validate_translation(source, completion.candidate)

    with pytest.raises(PublicationError):
        _ = publish_translation(
            source,
            completion.candidate,
            escalation_review,
            assignment=old_assignment,
            attempts=_answer_attempts(completion.candidate),
        )

    request = build_review_request(
        source,
        completion.candidate,
        ReviewScope(
            source_cue_ids=tuple(cue.cue_id for cue in source.cues),
            target_cue_ids=tuple(cue.cue_id for cue in completion.candidate.cues),
        ),
        (
            "fresh-translator",
            "fresh-reviser",
            "fresh-assembler",
            "fresh-independent-reviewer",
        ),
    )
    fresh_assignment = review_assignment_from_request(request)
    fresh_review = ReviewResult(
        candidate_digest=completion.candidate_digest,
        assignment_digest=fresh_assignment.assignment_digest,
        reviewed_source_cue_ids=request.source_cue_ids,
        reviewed_target_cue_ids=request.target_cue_ids,
        reviewed_domains=request.reviewed_domains,
    )
    attempts = _answer_attempts(completion.candidate)
    route = route_review(
        fresh_review,
        expected_candidate_digest=completion.candidate_digest,
        attempts=attempts,
        assignment=fresh_assignment,
    )
    published = publish_translation(
        source,
        completion.candidate,
        fresh_review,
        assignment=fresh_assignment,
        attempts=attempts,
    )

    assert fresh_assignment.current_candidate_digest == completion.candidate_digest
    assert fresh_assignment.assignment_digest != question.assignment_digest
    assert fresh_assignment.reviewer_execution_id == "fresh-independent-reviewer"
    assert fresh_review.candidate_digest == completion.candidate_digest
    assert route.action is RouteAction.FINALIZE
    assert published == completion.candidate
