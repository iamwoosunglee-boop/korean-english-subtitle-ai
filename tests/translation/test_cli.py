# Copyright 2026 Transcription Module contributors

"""End-to-end translation CLI boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from tests.translation.type_builders import evidence_id, milliseconds
from translation_module import cli as cli_module
from translation_module import cli_io as cli_io_module
from translation_module._cli_io_namespace import bind_output
from translation_module._cli_io_wal import build_wal, entry, internal_name
from translation_module.answers import (
    AcceptedTranslationAnswer,
    accept_answer,
)
from translation_module.canonical.attempts import (
    TranslationAttemptStore,
    TranslationLineRationale,
    translation_line_text_digest,
)
from translation_module.canonical.legacy_authority import (
    AttemptRationaleInput,
    project_legacy_candidate,
)
from translation_module.canonical.preflight import (
    AdjacentCoverageResult,
    PreflightCheckerEvidence,
    SemanticQualificationKind,
    SemanticQualificationResult,
)
from translation_module.cli import MAX_JSON_BYTES
from translation_module.common import SourceCueId, TargetCueId
from translation_module.contracts import (
    ContentKind,
    DocumentStatus,
    EpisodeContext,
    EvidenceEntry,
    EvidenceKind,
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
from translation_module.preparation import (
    ChunkPlan,
    TranslationBatchPlan,
    TranslationRequest,
    bind_source_digest,
)
from translation_module.review_routing import (
    bind_review_assignment_digest,
)
from translation_module.review_state import (
    ReviewAttempts,
)
from translation_module.validation import bind_translation_digest
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    FindingSeverity,
    ReviewAssignment,
    ReviewDomain,
    ReviewFinding,
    ReviewResult,
)

if TYPE_CHECKING:
    from translation_module.publication import PublicationReviewInputs

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2
PRIVATE_FILE_MODE = 0o600
FRAME_BYTES = b"frame evidence"


class _AnswerCliFixture(NamedTuple):
    source: SourceSnapshot
    document: TranslationDocument
    question: UnresolvedQuestion
    answer: AcceptedTranslationAnswer
    escalation_assignment: ReviewAssignment
    escalation_review: ReviewResult
    candidate: TranslationDocument


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "translation-cli", *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _rationale_path(
    root: Path,
    source: SourceSnapshot,
    document: TranslationDocument,
    name: str,
) -> Path:
    projected = project_legacy_candidate(source, document, root)
    rationales = tuple(
        TranslationLineRationale(
            target_cue_id=cue.cue_id,
            line_number=line_number,
            speaker_id=(
                cue.speakers[0]
                if len(cue.speakers) == 1
                else cue.speakers[line_number - 1]
            ),
            line_text_digest=translation_line_text_digest(line),
            rationale_ko="원문의 맥락과 화자 말투를 반영했습니다.",
        )
        for cue in projected.candidate.cues
        for line_number, line in enumerate(cue.text.split("\n"), start=1)
    )
    path = root.parent / f"{name}-rationales.json"
    _ = path.write_text(
        AttemptRationaleInput(rationales=rationales).model_dump_json(),
        encoding="utf-8",
    )
    return path


def _select_candidate(
    root: Path,
    paths: tuple[Path, Path],
    source: SourceSnapshot,
    document: TranslationDocument,
    attempt_id: str,
) -> subprocess.CompletedProcess[str]:
    source_path, document_path = paths
    candidate = project_legacy_candidate(source, document, root).candidate
    target_ids = tuple(cue.cue_id for cue in candidate.cues)
    source_ids = tuple(
        dict.fromkeys(
            source_id for cue in candidate.cues for source_id in cue.source_cue_ids
        )
    )
    checker = PreflightCheckerEvidence(
        adjacent_results=tuple(
            AdjacentCoverageResult(
                left_target_cue_id=left.cue_id,
                right_target_cue_id=right.cue_id,
                finding_ids=(),
            )
            for left, right in zip(candidate.cues, candidate.cues[1:], strict=False)
        ),
        glossary_results=(),
        semantic_results=(
            SemanticQualificationResult(
                kind=SemanticQualificationKind.SOURCE_CLAIMS,
                source_cue_ids=source_ids,
                target_cue_ids=target_ids,
                reviewer_execution_ids=("cli-source-claims",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
            SemanticQualificationResult(
                kind=SemanticQualificationKind.TARGET_ONLY,
                source_cue_ids=(),
                target_cue_ids=target_ids,
                reviewer_execution_ids=("cli-target-only",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
        ),
        findings=(),
    )
    checker_path = root.parent / "checker.json"
    _ = checker_path.write_text(checker.model_dump_json(), encoding="utf-8")
    return _run_cli(
        "preflight-select",
        str(source_path),
        str(document_path),
        str(checker_path),
        "--authority-root",
        str(root),
        "--attempt-id",
        attempt_id,
        "--selection",
        "selection-cli",
        "2026-08-18T14:00:00Z",
        "human-editor",
    )


def _source(
    *,
    bound: bool,
    evidence_root: Path | None = None,
) -> SourceSnapshot:
    source = SourceSnapshot(
        document_id="source-cli",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(3_000),
        context=EpisodeContext(summary="게스트 환영 인사"),
        evidence=EvidenceManifest(
            base_uri=(
                evidence_root.as_uri()
                if evidence_root is not None
                else "file:///unused/"
            ),
            entries=(
                (
                    EvidenceEntry(
                        evidence_id=evidence_id("frame-1"),
                        kind=EvidenceKind.FRAME,
                        media_type="image/jpeg",
                        resource_digest=hashlib.sha256(FRAME_BYTES).hexdigest(),
                        locator="frames/frame-1.jpg",
                        pts_ms=milliseconds(1_500),
                    ),
                )
                if evidence_root is not None
                else ()
            ),
        ),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source-1"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="어서 오세요.",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
            SourceCue(
                sequence=2,
                cue_id=SourceCueId("source-2"),
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="환영합니다.",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                evidence_ids=(evidence_id("frame-1"),)
                if evidence_root is not None
                else (),
            ),
        ),
    )
    return bind_source_digest(source) if bound else source


def _batch(source: SourceSnapshot, selected: ChunkPlan) -> TranslationBatchPlan:
    return TranslationBatchPlan(
        plan_id="cli-plan",
        source_digest=source.content_digest,
        chunks=(
            ChunkPlan(
                chunk_id="chunk-0",
                owned_source_cue_ids=(SourceCueId("source-1"),),
            ),
            selected,
        ),
    )


def _assignment(document: TranslationDocument) -> ReviewAssignment:
    return bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=document.content_digest,
            current_candidate_digest=document.content_digest,
            candidate_author_execution_id="candidate-execution",
            reviewer_execution_id="reviewer-execution",
            source_digest=document.source_digest,
            ordered_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
            ordered_target_cue_ids=tuple(cue.cue_id for cue in document.cues),
            reviewed_domains=tuple(ReviewDomain),
        )
    )


def _review(document: TranslationDocument) -> ReviewResult:
    return ReviewResult(
        candidate_digest=document.content_digest,
        assignment_digest=_assignment(document).assignment_digest,
        reviewed_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
        reviewed_target_cue_ids=tuple(cue.cue_id for cue in document.cues),
        reviewed_domains=tuple(ReviewDomain),
    )


def _attempts(
    document: TranslationDocument,
    *,
    focused_domains_reviewed: tuple[ReviewDomain, ...] = (),
) -> ReviewAttempts:
    return ReviewAttempts(
        root_candidate_digest=document.content_digest,
        current_candidate_digest=document.content_digest,
        focused_domains_reviewed=focused_domains_reviewed,
    )


def _document(source: SourceSnapshot) -> TranslationDocument:
    document = TranslationDocument(
        document_id="translation-cli",
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
                source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
                start_ms=milliseconds(0),
                end_ms=milliseconds(2_000),
                text="Welcome!",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        status=DocumentStatus.FINALIZED,
    )
    return bind_translation_digest(document)


run_cli_fixture = _run_cli
batch_fixture = _batch
rationale_path_fixture = _rationale_path
select_candidate_fixture = _select_candidate
source_fixture = _source
document_fixture = _document


def _answer_cli_fixture() -> _AnswerCliFixture:
    source = _source(bound=True)
    source_scope = (SourceCueId("source-1"), SourceCueId("source-2"))
    target_scope = (TargetCueId("target-1"), TargetCueId("target-2"))
    unresolved = UnresolvedItem(
        unresolved_id="question-1",
        source_cue_ids=source_scope,
        target_cue_ids=target_scope,
        current_translation="That was amazing.",
        why_difficult="The reaction intensity is ambiguous.",
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
                strength="safe",
                risk="weaker intensity",
            ),
        ),
        recommendation_id="option-a",
        recommendation_reason="The reaction is strong.",
    )
    draft = TranslationDocument.model_construct(
        document_id="answer-cli",
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
                cue_id=target_scope[0],
                source_cue_ids=source_scope,
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="That was",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
            TargetCue(
                sequence=2,
                cue_id=target_scope[1],
                source_cue_ids=source_scope,
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="amazing.",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=(unresolved,),
        status=DocumentStatus.NEEDS_CONFIRMATION,
    )
    document = bind_translation_digest(draft)
    escalation_assignment = bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=document.content_digest,
            current_candidate_digest=document.content_digest,
            candidate_author_execution_id="answer-author",
            reviewer_execution_id="answer-reviewer",
            source_digest=source.content_digest,
            ordered_source_cue_ids=source_scope,
            ordered_target_cue_ids=target_scope,
            reviewed_domains=tuple(ReviewDomain),
        )
    )
    question = UnresolvedQuestion(
        question_id=unresolved.unresolved_id,
        question_text="Which intensity should this subtitle use?",
        source_cue_ids=source_scope,
        target_cue_ids=target_scope,
        review_domain=ReviewDomain.NATURALNESS,
        source_digest=source.content_digest,
        candidate_digest=document.content_digest,
        assignment_digest=escalation_assignment.assignment_digest,
        revision_session_id="answer-revision",
    )
    document = TranslationDocument.model_validate(
        document.model_dump() | {"unresolved_questions": (question,)}
    )
    finding = ReviewFinding(
        finding_id=question.question_id,
        candidate_digest=document.content_digest,
        affected=tuple(
            AffectedReference(
                kind=AffectedKind.TARGET_CUE,
                item_id=target_id,
            )
            for target_id in target_scope
        ),
        severity=FindingSeverity.MAJOR,
        domain=question.review_domain,
        defect_class="reaction-intensity",
        blocking=True,
        problem="The available evidence cannot settle the intensity.",
        required_change=None,
    )
    escalation_review = ReviewResult(
        candidate_digest=document.content_digest,
        assignment_digest=escalation_assignment.assignment_digest,
        reviewed_source_cue_ids=source_scope,
        reviewed_target_cue_ids=target_scope,
        reviewed_domains=tuple(ReviewDomain),
        findings=(finding,),
    )
    answer = accept_answer(question, alternative_id="option-a")
    candidate = bind_translation_digest(
        document.model_copy(
            update={
                "cues": (
                    document.cues[0].model_copy(
                        update={
                            "end_ms": milliseconds(900),
                            "text": "That was",
                        }
                    ),
                    document.cues[1].model_copy(
                        update={
                            "start_ms": milliseconds(900),
                            "text": "wild.",
                        }
                    ),
                ),
                "unresolved": (),
                "unresolved_questions": (),
                "status": DocumentStatus.FINALIZED,
            }
        )
    )
    candidate = TranslationDocument.model_validate(candidate.model_dump())
    return _AnswerCliFixture(
        source=source,
        document=document,
        question=question,
        answer=answer,
        escalation_assignment=escalation_assignment,
        escalation_review=escalation_review,
        candidate=candidate,
    )


def test_bind_source_writes_digest_bound_snapshot(tmp_path: Path) -> None:
    """The CLI writes a source snapshot bound to its exact content."""
    source_path = tmp_path / "source-draft.json"
    output_path = tmp_path / "source.json"
    _ = source_path.write_text(_source(bound=False).model_dump_json(), encoding="utf-8")

    result = _run_cli(
        "bind-source",
        str(source_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    published = SourceSnapshot.model_validate_json(
        output_path.read_text(encoding="utf-8")
    )
    assert published.content_digest != "0" * 64
    assert json.loads(result.stdout)["content_digest"] == (published.content_digest)


def test_bind_source_preserves_explicit_draft_maturity(tmp_path: Path) -> None:
    """Raw source binding keeps the caller's draft publication boundary."""
    payload = _source(bound=False).model_dump(mode="json")
    payload["maturity"] = SourceMaturity.DRAFT.value
    source_path = tmp_path / "draft.json"
    output_path = tmp_path / "bound-draft.json"
    _ = source_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_cli(
        "bind-source",
        str(source_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["maturity"] == "draft"


def test_bind_source_rejects_omitted_maturity(tmp_path: Path) -> None:
    """Raw source binding cannot infer finalized maturity from omission."""
    source_path = tmp_path / "source-without-maturity.json"
    output_path = tmp_path / "bound-source.json"
    payload = _source(bound=False).model_dump(mode="json")
    del payload["maturity"]
    _ = source_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_cli(
        "bind-source",
        str(source_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert "maturity" in result.stderr
    assert not output_path.exists()


def test_validate_batch_plan_reports_typed_success(tmp_path: Path) -> None:
    """The CLI reports a deterministic summary without product writes."""
    source = _source(bound=True)
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        _batch(
            source,
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-2"),),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = _run_cli("validate-batch-plan", str(source_path), str(plan_path))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": True,
        "plan_id": "cli-plan",
        "source_digest": source.content_digest,
        "chunk_count": 2,
        "owned_count": 2,
    }


@pytest.mark.parametrize("mutation", ["stale", "foreign", "missing", "duplicate"])
def test_validate_batch_plan_rejects_invalid_full_run(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Malformed, stale, foreign, and incomplete plans are rejected."""
    source = _source(bound=True)
    payload = _batch(
        source,
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-2"),),
        ),
    ).model_dump(mode="json")
    if mutation == "stale":
        payload["source_digest"] = "f" * 64
    elif mutation == "foreign":
        payload["chunks"][1]["owned_source_cue_ids"] = ["foreign"]
    elif mutation == "missing":
        payload["chunks"] = payload["chunks"][:1]
    else:
        payload["chunks"][1]["owned_source_cue_ids"] = ["source-1"]
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_cli("validate-batch-plan", str(source_path), str(plan_path))

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"batch_plan_validation_failed"' in result.stderr


def test_prepare_request_unknown_chunk_leaves_no_output(tmp_path: Path) -> None:
    """Chunk selection rejects foreign identity before creating output."""
    source = _source(bound=True)
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    output_path = tmp_path / "request.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        _batch(
            source,
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-2"),),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = _run_cli(
        "prepare-request",
        str(source_path),
        str(plan_path),
        "unknown",
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert "unknown chunk ID" in result.stderr
    assert not output_path.exists()


def test_prepare_request_writes_owned_and_context_payload(
    tmp_path: Path,
) -> None:
    """The CLI preserves owned cues and read-only boundary context."""
    source = _source(bound=True)
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    output_path = tmp_path / "request.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        _batch(
            source,
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-2"),),
                context_before_source_cue_ids=(SourceCueId("source-1"),),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = _run_cli(
        "prepare-request",
        str(source_path),
        str(plan_path),
        "chunk-1",
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    request = TranslationRequest.model_validate_json(
        output_path.read_text(encoding="utf-8")
    )
    assert request.source_maturity is SourceMaturity.FINALIZED
    assert request.owned_cues[0].cue_id == "source-2"
    assert request.context_before[0].cue_id == "source-1"


def test_prepare_review_rejects_runtime_session_reuse_atomically(
    tmp_path: Path,
) -> None:
    """An author cannot be reused as reviewer and no files are emitted."""
    source = _source(bound=True)
    document = _document(source)
    source_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidate.json"
    output = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = candidate_path.write_text(document.model_dump_json(), encoding="utf-8")
    result = _run_cli(
        "prepare-review",
        str(source_path),
        str(candidate_path),
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output),
        "--assignment-output",
        str(assignment),
        "--runtime-session-ids",
        "same",
        "reviser",
        "assembler",
        "same",
    )
    assert result.returncode == CLI_ERROR_EXIT
    assert not output.exists()
    assert not assignment.exists()


def test_resolve_answer_uses_resegmented_lifecycle_candidate(
    tmp_path: Path,
) -> None:
    """The public resolver completes, validates, rereviews, and publishes."""
    fixture = _answer_cli_fixture()
    paths = {
        name: tmp_path / f"{name}.json"
        for name in (
            "source",
            "document",
            "question",
            "answer",
            "escalation-review",
            "candidate",
            "resolved",
            "escalation-assignment",
            "stale-publication",
            "fresh-review",
            "fresh-assignment",
            "attempts",
            "route",
            "published",
        )
    }
    inputs = {
        "source": fixture.source,
        "document": fixture.document,
        "question": fixture.question,
        "answer": fixture.answer,
        "escalation-review": fixture.escalation_review,
        "candidate": fixture.candidate,
        "escalation-assignment": fixture.escalation_assignment,
    }
    for name, model in inputs.items():
        _ = paths[name].write_text(model.model_dump_json(), encoding="utf-8")
    authority_root = tmp_path / "canonical-authority"
    rationale_path = _rationale_path(
        authority_root, fixture.source, fixture.candidate, "revision"
    )

    result = _run_cli(
        "resolve-answer",
        str(paths["document"]),
        str(paths["question"]),
        str(paths["answer"]),
        str(paths["escalation-review"]),
        str(paths["candidate"]),
        "--revision-io",
        str(paths["source"]),
        str(paths["resolved"]),
        "--attempt-authority",
        str(authority_root),
        "revision-attempt",
        "alternative",
        "-",
        "--rationales",
        str(rationale_path),
    )

    assert result.returncode == 0, result.stderr
    resolved = TranslationDocument.model_validate_json(
        paths["resolved"].read_text(encoding="utf-8")
    )
    assert resolved == fixture.candidate
    assert tuple(cue.text for cue in resolved.cues) == ("That was", "wild.")
    assert resolved.status is DocumentStatus.FINALIZED
    assert resolved.unresolved == ()
    assert resolved.unresolved_questions == ()
    assert tuple(
        item.attempt_id
        for item in TranslationAttemptStore(authority_root / "attempts").load()
    ) == ("revision-attempt",)
    assert json.loads(result.stdout) == {
        "document_id": resolved.document_id,
        "content_digest": resolved.content_digest,
        "output": str(paths["resolved"]),
        "requires_rereview": True,
    }

    validation = _run_cli(
        "validate",
        str(paths["source"]),
        str(paths["resolved"]),
    )
    assert validation.returncode == 0, validation.stderr
    assert json.loads(validation.stdout)["valid"] is True

    _ = paths["attempts"].write_text(
        _attempts(resolved).model_dump_json(),
        encoding="utf-8",
    )
    stale_publication = _run_cli(
        "publish",
        str(paths["source"]),
        str(paths["resolved"]),
        str(paths["escalation-review"]),
        "--publication-authority",
        str(paths["attempts"]),
        str(paths["escalation-assignment"]),
        str(authority_root),
        "--output",
        str(paths["stale-publication"]),
    )
    assert stale_publication.returncode == CLI_ERROR_EXIT
    assert stale_publication.stdout == ""
    assert not paths["stale-publication"].exists()

    fresh_assignment = _assignment(resolved)
    fresh_review = _review(resolved)
    _ = paths["fresh-assignment"].write_text(
        fresh_assignment.model_dump_json(),
        encoding="utf-8",
    )
    _ = paths["fresh-review"].write_text(
        fresh_review.model_dump_json(),
        encoding="utf-8",
    )
    routed = _run_cli(
        "route-review",
        str(paths["resolved"]),
        str(paths["fresh-review"]),
        str(paths["fresh-assignment"]),
        str(paths["attempts"]),
        "--output",
        str(paths["route"]),
    )
    assert routed.returncode == 0, routed.stderr
    assert json.loads(paths["route"].read_text(encoding="utf-8"))["action"] == (
        "finalize"
    )
    assert fresh_assignment.assignment_digest != (
        fixture.escalation_assignment.assignment_digest
    )
    assert fresh_review.candidate_digest == resolved.content_digest
    selected = _select_candidate(
        authority_root,
        (paths["source"], paths["resolved"]),
        fixture.source,
        resolved,
        "revision-attempt",
    )
    assert selected.returncode == 0, selected.stderr

    publication = _run_cli(
        "publish",
        str(paths["source"]),
        str(paths["resolved"]),
        str(paths["fresh-review"]),
        "--publication-authority",
        str(paths["attempts"]),
        str(paths["fresh-assignment"]),
        str(authority_root),
        "--output",
        str(paths["published"]),
    )
    assert publication.returncode == 0, publication.stderr
    assert (
        TranslationDocument.model_validate_json(
            paths["published"].read_text(encoding="utf-8")
        )
        == resolved
    )


@pytest.mark.parametrize(
    "mutation",
    ["stale-review", "stale-candidate", "token-loss", "bad-identity"],
)
def test_resolve_answer_rejects_invalid_lifecycle_input_atomically(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Rejected lifecycle evidence exits two without producing a candidate."""
    fixture = _answer_cli_fixture()
    question = fixture.question
    answer = fixture.answer
    review = fixture.escalation_review
    candidate = fixture.candidate

    if mutation == "stale-review":
        stale_digest = "f" * 64
        review = review.model_copy(
            update={
                "candidate_digest": stale_digest,
                "findings": tuple(
                    finding.model_copy(update={"candidate_digest": stale_digest})
                    for finding in review.findings
                ),
            }
        )
    elif mutation == "stale-candidate":
        candidate = candidate.model_copy(
            update={"content_digest": fixture.document.content_digest}
        )
    elif mutation == "token-loss":
        candidate = bind_translation_digest(
            candidate.model_copy(
                update={
                    "cues": (
                        candidate.cues[0],
                        candidate.cues[1].model_copy(update={"text": "amazing."}),
                    )
                }
            )
        )
    else:
        question = question.model_copy(
            update={"revision_session_id": "forged-revision"}
        )
        answer = accept_answer(question, alternative_id="option-a")

    paths = {
        name: tmp_path / f"{name}.json"
        for name in (
            "document",
            "question",
            "answer",
            "review",
            "candidate",
            "output",
            "source",
        )
    }
    inputs = {
        "source": fixture.source,
        "document": fixture.document,
        "question": question,
        "answer": answer,
        "review": review,
        "candidate": candidate,
    }
    for name, model in inputs.items():
        _ = paths[name].write_text(model.model_dump_json(), encoding="utf-8")
    authority_root = tmp_path / "invalid-authority"
    rationale_path = _rationale_path(
        authority_root, fixture.source, fixture.candidate, "invalid"
    )

    result = _run_cli(
        "resolve-answer",
        str(paths["document"]),
        str(paths["question"]),
        str(paths["answer"]),
        str(paths["review"]),
        str(paths["candidate"]),
        "--revision-io",
        str(paths["source"]),
        str(paths["output"]),
        "--attempt-authority",
        str(authority_root),
        "invalid-attempt",
        "alternative",
        "-",
        "--rationales",
        str(rationale_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == ("answer_resolution_failed")
    assert not paths["output"].exists()
    failed_attempts = TranslationAttemptStore(authority_root / "attempts").load()
    assert len(failed_attempts) == 1
    failed_payload = failed_attempts[0].model_dump(mode="json", exclude_none=True)
    assert failed_payload["outcome"] == "failed"
    assert "candidate_digest" not in failed_payload
    assert "rationales" not in failed_payload


def test_cli_rejects_symlink_json_input(tmp_path: Path) -> None:
    """CLI JSON inputs cannot redirect reads through symlinks."""
    source = tmp_path / "source.json"
    _ = source.write_text(_source(bound=False).model_dump_json(), encoding="utf-8")
    link = tmp_path / "source-link.json"
    link.symlink_to(source)
    output = tmp_path / "output.json"

    result = _run_cli("bind-source", str(link), "--output", str(output))

    assert result.returncode == CLI_ERROR_EXIT
    assert not output.exists()


def test_cli_rejects_oversized_json_without_output(tmp_path: Path) -> None:
    """CLI JSON reads stop at the configured byte boundary."""
    source = tmp_path / "large.json"
    _ = source.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    output = tmp_path / "output.json"

    result = _run_cli("bind-source", str(source), "--output", str(output))

    assert result.returncode == CLI_ERROR_EXIT
    assert "too large" in result.stderr
    assert not output.exists()


def test_cli_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """A FIFO is rejected without waiting for a writer."""
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    output = tmp_path / "output.json"

    result = _run_cli("bind-source", str(fifo), "--output", str(output))

    assert result.returncode == CLI_ERROR_EXIT
    assert not output.exists()


@pytest.mark.parametrize("special_kind", ["device", "socket"])
def test_cli_rejects_non_regular_json_input(
    tmp_path: Path,
    special_kind: str,
) -> None:
    """Device and socket paths cannot enter JSON parsing."""
    output = tmp_path / "output.json"
    bound_socket: socket.socket | None = None
    if special_kind == "device":
        source = Path("/dev/null")
    else:
        source = PROJECT_ROOT / f".translation-cli-{os.getpid()}.socket"
        source.unlink(missing_ok=True)
        bound_socket = socket.socket(socket.AF_UNIX)
        bound_socket.bind(str(source))
    try:
        result = _run_cli("bind-source", str(source), "--output", str(output))
    finally:
        if bound_socket is not None:
            bound_socket.close()
            source.unlink(missing_ok=True)

    assert result.returncode == CLI_ERROR_EXIT
    assert not output.exists()


@pytest.mark.parametrize("precreated_kind", ["file", "symlink"])
def test_atomic_write_never_opens_precreated_temporary(
    tmp_path: Path,
    precreated_kind: str,
) -> None:
    """Exclusive no-follow creation protects a predictable temp name."""
    output = tmp_path / "source.json"
    source = _source(bound=False)
    payload = f"{source.model_dump_json(indent=2)}\n".encode()
    with bind_output(output) as root:
        wal = build_wal(root, (entry(output.name, None, payload),))
        temporary = tmp_path / internal_name(wal, wal.entries[0])
    victim = tmp_path / "victim.json"
    _ = victim.write_text("do not overwrite", encoding="utf-8")
    if precreated_kind == "file":
        _ = temporary.write_text("attacker file", encoding="utf-8")
    else:
        temporary.symlink_to(victim)
    with pytest.raises(OSError, match="current CLI staging path"):
        _ = cli_io_module.write_model_atomic(output, source)

    assert not output.exists()
    assert victim.read_text(encoding="utf-8") == "do not overwrite"
    assert temporary.exists() or temporary.is_symlink()


def test_atomic_write_cleans_owned_temporary_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replace removes only the temporary created by this write."""
    output = tmp_path / "source.json"
    source = _source(bound=False)
    payload = f"{source.model_dump_json(indent=2)}\n".encode()
    with bind_output(output) as root:
        wal = build_wal(root, (entry(output.name, None, payload),))
        temporary = tmp_path / internal_name(wal, wal.entries[0])

    def fail_replace(
        _temporary: str,
        _output: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        del src_dir_fd, dst_dir_fd
        message = "replace failed"
        raise OSError(message)

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        _ = cli_io_module.write_model_atomic(output, source)

    assert not temporary.exists()
    assert not output.exists()


def test_atomic_write_uses_private_file_mode(tmp_path: Path) -> None:
    """Published JSON is created with owner-only permissions."""
    output = tmp_path / "source.json"

    cli_io_module.write_model_atomic(output, _source(bound=False))

    assert stat.S_IMODE(output.stat().st_mode) == PRIVATE_FILE_MODE


def test_validate_reports_machine_readable_failure(tmp_path: Path) -> None:
    """The CLI rejects stale translation input with a JSON error."""
    source = _source(bound=True)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "translation.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    stale = _document(source).model_copy(update={"source_digest": "f" * 64})
    _ = document_path.write_text(stale.model_dump_json(), encoding="utf-8")

    result = _run_cli(
        "validate",
        str(source_path),
        str(document_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"validation_failed"' in result.stderr
    assert "source digest" in result.stderr


def test_publish_requires_explicit_attempt_options(tmp_path: Path) -> None:
    """The CLI cannot publish while silently resetting revision attempts."""
    source = _source(bound=True)
    document = _document(source)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    output_path = tmp_path / "translation.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = review_path.write_text(
        json.dumps({"candidate_digest": document.content_digest, "findings": []}),
        encoding="utf-8",
    )

    result = _run_cli(
        "publish",
        str(source_path),
        str(document_path),
        str(review_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert not output_path.exists()


def test_publish_requires_parent_assignment_option(tmp_path: Path) -> None:
    """Review JSON and attempt state cannot replace parent-issued assignment."""
    source = _source(bound=True)
    document = _document(source)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    attempts_path = tmp_path / "attempts.json"
    output_path = tmp_path / "translation.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = review_path.write_text(_review(document).model_dump_json(), encoding="utf-8")
    _ = attempts_path.write_text(
        _attempts(document).model_dump_json(),
        encoding="utf-8",
    )

    result = _run_cli(
        "publish",
        str(source_path),
        str(document_path),
        str(review_path),
        "--output",
        str(output_path),
        "--attempts",
        str(attempts_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert not output_path.exists()


def test_publish_forwards_attempt_state_and_focused_domains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI options reach publication as typed routing state."""
    source = _source(bound=True)
    document = _document(source)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    attempts_path = tmp_path / "attempts.json"
    assignment_path = tmp_path / "assignment.json"
    output_path = tmp_path / "translation.json"
    attempts = _attempts(
        document,
        focused_domains_reviewed=(ReviewDomain.CULTURE, ReviewDomain.LYRICS),
    )
    assignment = _assignment(document)
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = review_path.write_text(_review(document).model_dump_json(), encoding="utf-8")
    _ = attempts_path.write_text(attempts.model_dump_json(), encoding="utf-8")
    _ = assignment_path.write_text(assignment.model_dump_json(), encoding="utf-8")
    forwarded_assignment: ReviewAssignment | None = None
    forwarded_attempts: ReviewAttempts | None = None

    def capture_publish(
        authority_root_input: Path,
        source_input: SourceSnapshot,
        candidate: TranslationDocument,
        review: ReviewResult,
        review_inputs: PublicationReviewInputs,
    ) -> TranslationDocument:
        assert authority_root_input == authority_root
        nonlocal forwarded_assignment, forwarded_attempts
        forwarded_assignment = review_inputs.assignment
        forwarded_attempts = review_inputs.attempts
        assert source_input == source
        assert review.candidate_digest == candidate.content_digest
        return candidate

    monkeypatch.setattr(cli_module, "publish_translation", capture_publish)
    authority_root = tmp_path / "publish-forward-authority"
    rationale = _rationale_path(authority_root, source, document, "forward")
    recorded = _run_cli(
        "record-candidate-attempt",
        str(source_path),
        str(document_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "forward-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale),
    )
    assert recorded.returncode == 0, recorded.stderr
    selected = _select_candidate(
        authority_root,
        (source_path, document_path),
        source,
        document,
        "forward-attempt",
    )
    assert selected.returncode == 0, selected.stderr

    cli_module.publish_command(
        source_path,
        (document_path, review_path),
        output_path,
        (attempts_path, assignment_path, authority_root),
    )

    assert forwarded_assignment == assignment
    assert forwarded_attempts == attempts
    assert attempts.focused_domains_reviewed == (
        ReviewDomain.CULTURE,
        ReviewDomain.LYRICS,
    )


def test_publish_writes_only_independently_approved_document(
    tmp_path: Path,
) -> None:
    """The public finalization surface binds validation and clean review."""
    source = _source(bound=True)
    document = _document(source)
    review = _review(document)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    attempts_path = tmp_path / "attempts.json"
    assignment_path = tmp_path / "assignment.json"
    output_path = tmp_path / "translation.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = review_path.write_text(review.model_dump_json(), encoding="utf-8")
    _ = attempts_path.write_text(
        _attempts(document).model_dump_json(), encoding="utf-8"
    )
    _ = assignment_path.write_text(
        _assignment(document).model_dump_json(),
        encoding="utf-8",
    )

    authority_root = tmp_path / "publish-authority"
    rationale = _rationale_path(authority_root, source, document, "publish")
    recorded = _run_cli(
        "record-candidate-attempt",
        str(source_path),
        str(document_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "publish-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale),
    )
    assert recorded.returncode == 0, recorded.stderr
    selected = _select_candidate(
        authority_root,
        (source_path, document_path),
        source,
        document,
        "publish-attempt",
    )
    assert selected.returncode == 0, selected.stderr

    result = _run_cli(
        "publish",
        str(source_path),
        str(document_path),
        str(review_path),
        "--output",
        str(output_path),
        "--publication-authority",
        str(attempts_path),
        str(assignment_path),
        str(authority_root),
    )

    assert result.returncode == 0, result.stderr
    published = TranslationDocument.model_validate_json(
        output_path.read_text(encoding="utf-8")
    )
    assert published.document_id == document.document_id
    assert json.loads(result.stdout)["status"] == "finalized"

    linked_root = tmp_path / "publish-authority-link"
    linked_root.symlink_to(authority_root, target_is_directory=True)
    linked_output = tmp_path / "linked-translation.json"
    linked = _run_cli(
        "publish",
        str(source_path),
        str(document_path),
        str(review_path),
        "--output",
        str(linked_output),
        "--publication-authority",
        str(attempts_path),
        str(assignment_path),
        str(linked_root),
    )
    assert linked.returncode == CLI_ERROR_EXIT
    assert not linked_output.exists()


def test_publish_rejects_omitted_candidate_target_without_output(
    tmp_path: Path,
) -> None:
    """The real CLI rejects incomplete target attestation atomically."""
    source = _source(bound=True)
    document = _document(source)
    first_target = document.cues[0].model_copy(
        update={
            "source_cue_ids": (SourceCueId("source-1"),),
            "end_ms": milliseconds(1_000),
        }
    )
    second_target = TargetCue(
        sequence=2,
        cue_id=TargetCueId("target-2"),
        source_cue_ids=(SourceCueId("source-2"),),
        start_ms=milliseconds(1_000),
        end_ms=milliseconds(2_000),
        text="Welcome.",
        speakers=("한지아",),
        content_kind=ContentKind.DIALOGUE,
    )
    document = bind_translation_digest(
        document.model_copy(update={"cues": (first_target, second_target)})
    )
    assignment = bind_review_assignment_digest(
        _assignment(document).model_copy(
            update={
                "assignment_digest": "0" * 64,
                "ordered_target_cue_ids": (TargetCueId("target-1"),),
            }
        )
    )
    review = ReviewResult(
        candidate_digest=document.content_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=(
            SourceCueId("source-1"),
            SourceCueId("source-2"),
        ),
        reviewed_target_cue_ids=(TargetCueId("target-1"),),
        reviewed_domains=tuple(ReviewDomain),
    )
    candidate_target_ids = tuple(cue.cue_id for cue in document.cues)
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    attempts_path = tmp_path / "attempts.json"
    assignment_path = tmp_path / "assignment.json"
    output_path = tmp_path / "translation.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = review_path.write_text(review.model_dump_json(), encoding="utf-8")
    _ = attempts_path.write_text(
        _attempts(document).model_dump_json(), encoding="utf-8"
    )
    _ = assignment_path.write_text(assignment.model_dump_json(), encoding="utf-8")

    assert candidate_target_ids == (
        TargetCueId("target-1"),
        TargetCueId("target-2"),
    )
    assert (
        assignment.ordered_target_cue_ids
        == review.reviewed_target_cue_ids
        == candidate_target_ids[:1]
    )

    authority_root = tmp_path / "incomplete-authority"
    rationale = _rationale_path(authority_root, source, document, "incomplete")
    recorded = _run_cli(
        "record-candidate-attempt",
        str(source_path),
        str(document_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "incomplete-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale),
    )
    assert recorded.returncode == 0, recorded.stderr
    selected = _select_candidate(
        authority_root,
        (source_path, document_path),
        source,
        document,
        "incomplete-attempt",
    )
    assert selected.returncode == 0, selected.stderr

    result = _run_cli(
        "publish",
        str(source_path),
        str(document_path),
        str(review_path),
        "--output",
        str(output_path),
        "--publication-authority",
        str(attempts_path),
        str(assignment_path),
        str(authority_root),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert json.loads(result.stderr)["error"]["code"] == "publication_failed"
    assert result.stdout == ""
    assert not output_path.exists()
