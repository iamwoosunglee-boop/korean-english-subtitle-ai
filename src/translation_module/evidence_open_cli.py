# Copyright 2026 Transcription Module contributors

"""Canonical CLI route for explicit frame opening and review preparation."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from translation_module import cli_options
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.cli_io import read_json, write_models_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenContext,
    EvidenceOpenError,
    EvidenceOpenRecordError,
)
from translation_module.evidence_open_preparation import (
    EvidenceOpenSelection,
    OpenedReviewInput,
    build_opened_review_request,
    review_assignment_from_opened_request,
)
from translation_module.evidence_open_store import EvidenceOpenStore

OpenFramePaths = Annotated[
    tuple[Path, Path, str],
    typer.Argument(metavar="SOURCE CANDIDATE EVIDENCE_ID"),
]
ActorRoleOption = Annotated[
    EvidenceActorRole,
    typer.Option("--actor-role", help="Workflow role explicitly opening the frame."),
]
ConsumerIdOption = Annotated[
    str,
    typer.Option("--consumer-id", help="Attempt or review consuming the frame."),
]


def _load_bound_artifacts(
    source_path: Path,
    candidate_path: Path,
) -> tuple[SourceTranscriptV2, TranslationDocumentV2]:
    source = SourceTranscriptV2.model_validate_json(read_json(source_path))
    candidate = TranslationDocumentV2.model_validate_json(read_json(candidate_path))
    verify_canonical_digests(source)
    verify_canonical_digests(candidate)
    reference = candidate.source_ref
    if (
        reference.document_id != source.document_id
        or reference.revision_id != source.revision_id
        or reference.content_digest != source.content_digest
    ):
        raise EvidenceOpenRecordError(
            evidence_id="<candidate>",
            reason="candidate source binding is stale",
        )
    return source, candidate


def _open_frame_command(
    paths: OpenFramePaths,
    actor_role: ActorRoleOption,
    consumer_id: ConsumerIdOption,
    evidence_root: cli_options.EvidenceRoot,
    evidence_open_root: cli_options.EvidenceOpenRoot,
) -> None:
    """Explicitly read one canonical frame and persist consumer-bound proof."""
    source_path, candidate_path, evidence_id = paths
    try:
        source, candidate = _load_bound_artifacts(source_path, candidate_path)
        record = EvidenceOpenStore(
            evidence_open_root,
            EvidenceOpenContext(
                source=source,
                consumer_content_digest=candidate.content_digest,
            ),
            FileEvidenceResolver(evidence_root),
        ).open_frame(evidence_id, actor_role, consumer_id)
    except (
        EvidenceOpenError,
        EvidenceOpenRecordError,
        OSError,
        ValidationError,
    ) as error:
        _fail("evidence_open_failed", error)
    _echo(
        evidence_id=record.evidence_id,
        actor_role=record.actor_role.value,
        consumer_id=record.consumer_id,
        consumer_content_digest=record.consumer_content_digest,
        record_digest=record.record_digest,
    )


def _prepare_review_command(
    paths: cli_options.SourceCandidate,
    evidence_open_root: cli_options.EvidenceOpenRoot,
    output: cli_options.Output,
    assignment_output: Annotated[Path, typer.Option("--assignment-output")],
    runtime_session_ids: cli_options.RuntimeSessions,
) -> None:
    """Assemble reviewer input solely from matching persisted open records."""
    source_path, candidate_path = paths
    try:
        source, candidate = _load_bound_artifacts(source_path, candidate_path)
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for cue in candidate.cues
                for evidence_id in cue.evidence_ids
            )
        )
        request = build_opened_review_request(
            OpenedReviewInput(
                selection=EvidenceOpenSelection(
                    source=source,
                    evidence_ids=evidence_ids,
                    actor_role=EvidenceActorRole.REVIEWER,
                    consumer_id=runtime_session_ids[3],
                    consumer_content_digest=candidate.content_digest,
                ),
                candidate=candidate,
            ),
            EvidenceOpenStore(
                evidence_open_root,
                EvidenceOpenContext(
                    source=source,
                    consumer_content_digest=candidate.content_digest,
                ),
                None,
            ),
        )
        assignment = review_assignment_from_opened_request(
            request,
            runtime_session_ids,
        )
        write_models_atomic(((output, request), (assignment_output, assignment)))
    except (EvidenceOpenRecordError, OSError, ValidationError) as error:
        _fail("review_preparation_failed", error)
    _echo(
        candidate_digest=request.candidate_content_digest,
        assignment_digest=assignment.assignment_digest,
        output=str(output),
        assignment_output=str(assignment_output),
    )


def register_evidence_open_commands(app: typer.Typer) -> None:
    """Register canonical explicit-open and persisted-review commands."""
    _ = app.command("open-frame")(_open_frame_command)
    _ = app.command("prepare-review")(_prepare_review_command)
