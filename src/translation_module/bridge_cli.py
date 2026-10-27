# Copyright 2026 Transcription Module contributors
"""Typed bridge commands for the translation runtime."""
from pathlib import Path
from typing import Annotated

import anyio
import typer
from pydantic import ValidationError

from translation_module import cli_options
from translation_module.answer_lifecycle import (
    AnswerLifecycleError,
    prepare_answer_arbitration,
)
from translation_module.answers import AcceptedTranslationAnswer
from translation_module.authority_cli import record_legacy_candidate
from translation_module.candidate import (
    CandidateAssemblyError,
    ChunkCandidate,
    assemble_candidate,
)
from translation_module.canonical.legacy_authority import (
    AttemptRecordIdentity,
    LegacyAuthorityError,
    record_failed_legacy_candidate,
    record_failed_legacy_intent,
)
from translation_module.cli_io import read_json, write_model_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail
from translation_module.document import (
    TranslationDocument,
    UnresolvedQuestion,
    translation_digest,
)
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_open_cli import register_evidence_open_commands
from translation_module.preparation import (
    TranslationBatchPlan,
    prepare_request,
    validate_translation_batch_plan,
)
from translation_module.preparation_errors import PreparationError
from translation_module.review_routing import route_review, validate_review_state
from translation_module.review_state import (
    ReviewAttempts,
)
from translation_module.source import SourceSnapshot
from translation_module.workflow import ReviewAssignment, ReviewResult, WorkflowError


def _validate_batch_plan_command(source_path: Path, plan_path: Path) -> None:
    try:
        source = SourceSnapshot.model_validate_json(read_json(source_path))
        plan = TranslationBatchPlan.model_validate_json(read_json(plan_path))
        chunks = validate_translation_batch_plan(source, plan)
    except (OSError, PreparationError, ValidationError) as error:
        _fail("batch_plan_validation_failed", error)
    _echo(
        valid=True,
        plan_id=plan.plan_id,
        source_digest=source.content_digest,
        chunk_count=len(chunks),
        owned_count=sum(len(chunk.owned_source_cue_ids) for chunk in chunks),
    )


def _prepare_request_command(
    source_path: Path,
    plan_path: Path,
    chunk_id: str,
    evidence_root: cli_options.EvidenceRoot,
    output: cli_options.Output,
) -> None:
    try:
        source = SourceSnapshot.model_validate_json(read_json(source_path))
        batch = TranslationBatchPlan.model_validate_json(read_json(plan_path))
        plans = validate_translation_batch_plan(source, batch)
        plan = next((item for item in plans if item.chunk_id == chunk_id), None)
        if plan is None:
            _fail(
                "request_preparation_failed",
                PreparationError(f"unknown chunk ID: {chunk_id}"),
            )
        request = prepare_request(source, plan, FileEvidenceResolver(evidence_root))
        write_model_atomic(output, request)
    except (OSError, PreparationError, ValidationError) as error:
        _fail("request_preparation_failed", error)
    _echo(
        chunk_id=request.chunk_id,
        owned_count=len(request.owned_cues),
        output=str(output),
    )


def _assemble_candidate_command(
    paths: Annotated[tuple[Path, Path], typer.Argument(metavar="SOURCE PLAN")],
    candidate_paths: list[Path],
    candidate_output: cli_options.CandidateOutput,
    attempt_authority: cli_options.AttemptAuthority,
    rationale_path: cli_options.Rationales,
) -> None:
    source_path, plan_path = paths
    document_id, output = candidate_output
    authority_root, attempt_id, attempt_kind, parent = attempt_authority
    identity = AttemptRecordIdentity(
        attempt_id, attempt_kind, None if parent == "-" else parent
    )
    source: SourceSnapshot | None = None
    document: TranslationDocument | None = None
    batch: TranslationBatchPlan | None = None
    recorded = False
    try:
        source = SourceSnapshot.model_validate_json(read_json(source_path))
        batch = TranslationBatchPlan.model_validate_json(read_json(plan_path))
        plans = validate_translation_batch_plan(source, batch)
        candidates = tuple(
            ChunkCandidate.model_validate_json(read_json(path))
            for path in candidate_paths
        )
        document = assemble_candidate(
            source, plans, candidates, document_id=document_id
        )
        record_legacy_candidate(
            source,
            document,
            authority_root,
            identity,
            rationale_path,
        )
        recorded = True
        write_model_atomic(output, document)
    except (
        CandidateAssemblyError,
        LegacyAuthorityError,
        OSError,
        ValidationError,
        ValueError,
    ) as error:
        if not recorded and source is not None:
            if document is not None:
                record_failed_legacy_candidate(
                    source, document, authority_root, identity
                )
            elif batch is not None:
                record_failed_legacy_intent(
                    source, batch, document_id, authority_root, identity
                )
        _fail("candidate_assembly_failed", error)
    _echo(
        document_id=document.document_id,
        content_digest=document.content_digest,
        output=str(output),
    )


def _route_review_command(
    candidate_path: Path,
    review_path: Path,
    assignment_path: Path,
    attempts_path: Path,
    output: cli_options.Output,
) -> None:
    try:
        candidate = TranslationDocument.model_validate_json(read_json(candidate_path))
        if translation_digest(candidate) != candidate.content_digest:
            _fail("review_routing_failed", WorkflowError("candidate digest mismatch"))
        review = ReviewResult.model_validate_json(read_json(review_path))
        assignment = ReviewAssignment.model_validate_json(read_json(assignment_path))
        attempts = ReviewAttempts.model_validate_json(read_json(attempts_path))
        focused_domains = validate_review_state(
            candidate.content_digest, review, assignment, attempts
        )
        route = route_review(
            review,
            expected_candidate_digest=candidate.content_digest,
            attempts=attempts,
            focused_domains_reviewed=focused_domains,
            assignment=assignment,
        )
        write_model_atomic(output, route)
    except (OSError, ValidationError, WorkflowError) as error:
        _fail("review_routing_failed", error)
    _echo(action=route.action.value, output=str(output))


def _resolve_answer_command(
    paths: Annotated[
        tuple[Path, Path, Path, Path, Path],
        typer.Argument(metavar="DOCUMENT QUESTION ANSWER ESCALATION_REVIEW CANDIDATE"),
    ],
    revision_io: cli_options.RevisionIo,
    attempt_authority: cli_options.AttemptAuthority,
    rationale_path: cli_options.Rationales,
) -> None:
    document_path, question_path, answer_path, review_path, candidate_path = paths
    source_path, output = revision_io
    authority_root, attempt_id, attempt_kind, parent = attempt_authority
    identity = AttemptRecordIdentity(
        attempt_id, attempt_kind, None if parent == "-" else parent
    )
    source: SourceSnapshot | None = None
    candidate: TranslationDocument | None = None
    recorded = False
    try:
        source = SourceSnapshot.model_validate_json(read_json(source_path))
        document = TranslationDocument.model_validate_json(read_json(document_path))
        question = UnresolvedQuestion.model_validate_json(read_json(question_path))
        answer = AcceptedTranslationAnswer.model_validate_json(read_json(answer_path))
        review = ReviewResult.model_validate_json(read_json(review_path))
        candidate = TranslationDocument.model_validate_json(read_json(candidate_path))
        arbiter = prepare_answer_arbitration(document, review)
        completion = anyio.run(arbiter.complete_answer, question, answer, candidate)
        revised = completion.candidate
        record_legacy_candidate(
            source,
            revised,
            authority_root,
            identity,
            rationale_path,
        )
        recorded = True
        write_model_atomic(output, revised)
    except (
        AnswerLifecycleError,
        LegacyAuthorityError,
        OSError,
        ValidationError,
        ValueError,
    ) as error:
        if not recorded and source is not None and candidate is not None:
            record_failed_legacy_candidate(source, candidate, authority_root, identity)
        _fail("answer_resolution_failed", error)
    _echo(
        document_id=revised.document_id,
        content_digest=revised.content_digest,
        output=str(output),
        requires_rereview=True,
    )


def register_bridge_commands(app: typer.Typer) -> None:
    """Register the source-ordered runtime bridge commands."""
    _ = app.command("validate-batch-plan")(_validate_batch_plan_command)
    _ = app.command("prepare-request")(_prepare_request_command)
    _ = app.command("assemble-candidate")(_assemble_candidate_command)
    register_evidence_open_commands(app)
    _ = app.command("route-review")(_route_review_command)
    _ = app.command("resolve-answer")(_resolve_answer_command)
