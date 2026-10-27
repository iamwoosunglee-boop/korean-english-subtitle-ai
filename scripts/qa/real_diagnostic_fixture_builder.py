# Copyright 2026 Transcription Module contributors

"""Confined replay of authoritative paired-diagnostic fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, Never, override
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError
from tests.visual_correction_fixture_support import VisualCorrectionFixture

from scripts.qa.paired_diagnostic_models import (
    AlignmentClaim,
    BaselineRun,
    CandidateRun,
    CharacterTrace,
    Contamination,
    DiagnosticOutcomes,
    SafetyCounts,
)
from scripts.qa.real_diagnostic_fixture_models import (
    ARTIFACT_PATHS,
    ArtifactKind,
    AutomaticActionAssessment,
    CaseEvidenceBridge,
    DamageAssessment,
    DiagnosticEvidenceManifest,
    EvidencePairedDiagnosticInput,
    EvidencePairedDiagnosticPayload,
    InputMode,
    IntervalEvidenceBridge,
    MeaningAssessment,
    OwnerAssessment,
    PairedDiagnosticAssessment,
    PairedDiagnosticFixtureRun,
    PresenceAssessment,
)

if TYPE_CHECKING:
    from pathlib import Path

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class DiagnosticBuildError(Exception):
    """One stable builder failure exposed by the CLI."""

    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message


def diagnostic_error(code: str, message: str) -> DiagnosticBuildError:
    """Construct one typed builder failure without losing its stable code."""
    return DiagnosticBuildError(code=code, message=message)


def refuse(code: str, message: str) -> Never:
    """Raise one typed builder failure with a stable code."""
    raise DiagnosticBuildError(code=code, message=message)


@dataclass(frozen=True, slots=True)
class _OpenedArtifact:
    payload: bytes
    mode: int
    size: int
    sha256: str


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: JsonValue) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def canonical_model_bytes(model: EvidencePairedDiagnosticInput) -> bytes:
    """Serialize one validated v2 input deterministically."""
    return _canonical(model.model_dump(mode="json"))


def _read_confined(root: Path, relative: str) -> _OpenedArtifact:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in relative
    ):
        refuse(
            "unsafe_artifact_path",
            "unsafe artifact path",
        )
    root_fd = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    directory_fd = root_fd
    opened_directories: list[int] = []
    try:
        for part in path.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            opened_directories.append(next_fd)
            directory_fd = next_fd
        file_fd = os.open(
            path.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                refuse(
                    "unsafe_artifact_path",
                    "artifact must be a single-link regular file",
                )
            with os.fdopen(file_fd, "rb", closefd=False) as stream:
                payload = stream.read()
            return _OpenedArtifact(
                payload=payload,
                mode=stat.S_IMODE(metadata.st_mode),
                size=metadata.st_size,
                sha256=_sha(payload),
            )
        finally:
            os.close(file_fd)
    except OSError:
        refuse(
            "unsafe_artifact_path",
            "artifact path is not safely confined",
        )
    finally:
        for opened_fd in reversed(opened_directories):
            os.close(opened_fd)
        os.close(root_fd)


def _load_fixture_run(root: Path) -> PairedDiagnosticFixtureRun:
    opened = _read_confined(root, "fixture-run.json")
    try:
        return PairedDiagnosticFixtureRun.model_validate_json(opened.payload)
    except ValidationError:
        refuse(
            "stale_artifact",
            "fixture-run manifest is invalid",
        )


def _load_assessment(root: Path) -> tuple[PairedDiagnosticAssessment, bytes]:
    payload = _read_confined(root, "paired-diagnostic-assessment.json").payload
    try:
        probe = _JSON_OBJECT.validate_json(payload)
    except ValidationError:
        refuse(
            "non_authoritative_outcome",
            "assessment is not an authoritative assessment document",
        )
    if probe.get("schema_version") != "paired-diagnostic-assessment.v1":
        refuse(
            "non_authoritative_outcome",
            "assessment is not an authoritative assessment document",
        )
    try:
        return PairedDiagnosticAssessment.model_validate_json(payload), payload
    except ValidationError:
        refuse(
            "invalid_assessment",
            "assessment document is invalid",
        )


def _open_artifacts(
    root: Path,
    fixture_run: PairedDiagnosticFixtureRun,
) -> dict[ArtifactKind, _OpenedArtifact]:
    expected_paths = dict(ARTIFACT_PATHS)
    if any(
        record.relative_path != expected_paths[record.kind]
        for record in fixture_run.artifacts
    ):
        refuse(
            "forbidden_evidence",
            "artifact kind is not bound to its canonical fixture path",
        )
    opened: dict[ArtifactKind, _OpenedArtifact] = {}
    for record in fixture_run.artifacts:
        artifact = _read_confined(root, record.relative_path)
        if (
            artifact.mode != record.mode
            or artifact.size != record.size
            or artifact.sha256 != record.sha256
        ):
            refuse(
                "stale_artifact",
                "artifact bytes differ from the fixture manifest",
            )
        document = _JSON_OBJECT.validate_json(artifact.payload)
        if document.get("kind") != record.kind:
            refuse(
                "stale_artifact",
                "artifact kind differs from the fixture manifest",
            )
        if document.get("run_id") != fixture_run.run_id:
            refuse(
                "foreign_run",
                "artifact bytes belong to another run",
            )
        opened[record.kind] = artifact
    manifest_value = [
        record.model_dump(mode="json") for record in fixture_run.artifacts
    ]
    snapshot = _sha(
        json.dumps(
            manifest_value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    if snapshot != fixture_run.artifact_snapshot_digest:
        refuse(
            "stale_artifact",
            "artifact snapshot digest is stale",
        )
    return opened


def _validate_assessment_population(
    fixture: VisualCorrectionFixture,
    assessment: PairedDiagnosticAssessment,
) -> None:
    expected = tuple(case.case_id for case in fixture.cases)
    actual = tuple(case.case_id for case in assessment.author.cases)
    if len(actual) != len(set(actual)):
        refuse(
            "duplicate_assessment",
            "assessment contains a duplicated case",
        )
    unknown = set(actual) - set(expected)
    if unknown:
        refuse(
            "unknown_frozen_case",
            "assessment contains an unknown frozen case",
        )
    if actual != expected:
        refuse(
            "incomplete_frozen_population",
            "assessment does not preserve complete frozen case order",
        )
    for frozen, assessed in zip(
        fixture.cases,
        assessment.author.cases,
        strict=True,
    ):
        intervals = tuple(item.interval_id for item in assessed.interval_mappings)
        if intervals != frozen.comparable_interval_ids:
            refuse(
                "incomplete_frozen_population",
                "assessment does not preserve complete interval order",
            )


def _artifact_ids(
    payload: bytes,
    field: Literal["cue_ids", "turn_ids"],
) -> set[str]:
    document = _JSON_OBJECT.validate_json(payload)
    values = TypeAdapter(tuple[str, ...]).validate_python(document[field])
    return set(values)


def build_fixture_input(
    fixture_path: Path,
    run_fixture: Path,
) -> EvidencePairedDiagnosticInput:
    """Build one v2 input solely from a confined fixture replay."""
    fixture_bytes = fixture_path.read_bytes()
    fixture = VisualCorrectionFixture.model_validate_json(fixture_bytes)
    fixture_run = _load_fixture_run(run_fixture)
    assessment, assessment_bytes = _load_assessment(run_fixture)
    if assessment.candidate_run_id != fixture_run.run_id:
        refuse(
            "foreign_run",
            "assessment belongs to another run",
        )
    artifacts = _open_artifacts(run_fixture, fixture_run)
    _validate_assessment_population(fixture, assessment)
    author_bytes = _canonical(assessment.author.model_dump(mode="json"))
    author_sha = _sha(author_bytes)
    if (
        assessment.fixture_sha256 != _sha(fixture_bytes)
        or assessment.artifact_snapshot_digest
        != fixture_run.artifact_snapshot_digest
        or assessment.review.author_payload_sha256 != author_sha
        or assessment.review.independently_opened_snapshot_digest
        != fixture_run.artifact_snapshot_digest
        or assessment.review.decision != "approve"
    ):
        refuse(
            "invalid_assessment",
            "assessment bindings or independent approval are invalid",
        )
    cue_ids = _artifact_ids(
        artifacts["candidate_transcript"].payload,
        "cue_ids",
    )
    turn_ids = _artifact_ids(
        artifacts["verified_speaker_turns_v3"].payload,
        "turn_ids",
    )
    interval_by_id = {
        interval.interval_id: interval
        for interval in fixture.reference_only.comparable_intervals
    }
    claims: list[AlignmentClaim] = []
    bridges: list[CaseEvidenceBridge] = []
    traces: list[CharacterTrace] = []
    for assessed in assessment.author.cases:
        mapped: list[IntervalEvidenceBridge] = []
        for mapping in assessed.interval_mappings:
            if (
                mapping.candidate_cue_id not in cue_ids
                or not set(mapping.authority_turn_ids) <= turn_ids
            ):
                refuse(
                    "invalid_assessment",
                    "assessment mapping does not resolve to candidate evidence",
                )
            interval = interval_by_id[mapping.interval_id]
            claims.append(
                AlignmentClaim(
                    case_id=assessed.case_id,
                    interval_id=interval.interval_id,
                    source_cue_ids=interval.source_identity.source_cue_ids,
                    speakers=tuple(interval.source_identity.speaker.split("|")),
                    turn_id=interval.source_identity.turn_id,
                    start_ms=interval.start_ms,
                    end_ms=interval.end_ms,
                )
            )
            mapped.append(
                IntervalEvidenceBridge(
                    interval_id=mapping.interval_id,
                    candidate_cue_id=mapping.candidate_cue_id,
                    authority_turn_ids=mapping.authority_turn_ids,
                )
            )
        bridges.append(
            CaseEvidenceBridge(
                case_id=assessed.case_id,
                intervals=tuple(mapped),
            )
        )
        traces.extend(
            CharacterTrace(
                case_id=assessed.case_id,
                character_id=trace.character_id,
                value=trace.value,
                source_evidence_id=trace.source_evidence_id,
                source_value=trace.source_value,
            )
            for trace in assessed.character_traces
        )
    cases = assessment.author.cases
    outcomes = DiagnosticOutcomes(
        correct_meaning_case_ids=tuple(
            case.case_id
            for case in cases
            if case.final_meaning is MeaningAssessment.CORRECT
        ),
        mandatory_missing_before_case_ids=tuple(
            case.case_id
            for case in cases
            if case.clean_asr_mandatory_meaning is PresenceAssessment.MISSING
        ),
        mandatory_missing_after_case_ids=tuple(
            case.case_id
            for case in cases
            if case.final_mandatory_meaning is PresenceAssessment.MISSING
        ),
        auto_corrected_case_ids=tuple(
            case.case_id
            for case in cases
            if case.automatic_action
            is AutomaticActionAssessment.REPLACE_SPAN
        ),
        auto_inserted_case_ids=tuple(
            case.case_id
            for case in cases
            if case.automatic_action is AutomaticActionAssessment.INSERT_CUE
        ),
        changed_character_count=len(traces),
        correction_trace=tuple(traces),
    )
    safety = SafetyCounts(
        disallowed_track_contribution=(
            fixture_run.disallowed_track_contribution
        ),
        non_speech_unknown_elevation=fixture_run.non_speech_unknown_elevation,
        user_confirmed_overwrite=fixture_run.user_confirmed_overwrite,
        critical_wrong_owner=sum(
            case.critical_owner is OwnerAssessment.WRONG for case in cases
        ),
        correct_to_wrong_auto_damage=sum(
            case.automatic_damage is DamageAssessment.DAMAGED
            for case in cases
        ),
        unsupported_additions_before=sum(
            case.unsupported_additions_before for case in cases
        ),
        unsupported_additions_after=sum(
            case.unsupported_additions_after for case in cases
        ),
    )
    artifact_sha = {
        kind: artifact.sha256 for kind, artifact in artifacts.items()
    }
    evidence = DiagnosticEvidenceManifest(
        fixture_sha256=_sha(fixture_bytes),
        artifact_manifest=fixture_run.artifacts,
        artifact_manifest_sha256=fixture_run.artifact_snapshot_digest,
        artifact_snapshot_digest=fixture_run.artifact_snapshot_digest,
        assessment_sha256=_sha(assessment_bytes),
        assessment_author_payload_sha256=author_sha,
        candidate_transcript_sha256=artifact_sha["candidate_transcript"],
        working_transcript_digest=artifact_sha["candidate_transcript"],
        policy_file_sha256=artifact_sha["policy"],
        policy_digest=fixture_run.policy_digest,
        inventory_file_sha256=artifact_sha["inventory"],
        inventory_digest=artifact_sha["inventory"],
        source_authority_digest=artifact_sha["speaker_authority_v2"],
        source_digest=artifact_sha["candidate_transcript"],
        speaker_authority_sha256=artifact_sha["speaker_authority_v2"],
        verified_speaker_turns_sha256=(
            artifact_sha["verified_speaker_turns_v3"]
        ),
        final_review_assignment_digest=artifact_sha[
            "final_review_assignment"
        ],
        final_review_result_digest=artifact_sha["final_review_result"],
        final_review_receipt_digest=artifact_sha["final_review_receipt"],
        final_review_proposal_digest=artifact_sha["final_review_proposal"],
        final_review_decision_digest=artifact_sha["final_review_decision"],
        final_review_application_digest=artifact_sha[
            "final_review_application"
        ],
        confirmation_anchor_digest=artifact_sha["confirmation_anchor"],
        case_evidence=tuple(bridges),
        validation_expected_absent=True,
    )
    payload = EvidencePairedDiagnosticPayload(
        input_mode=InputMode.FIXTURE,
        evaluation_kind="same_video_paired_diagnostic",
        comparison_method="comparable_intervals",
        alignment_basis="source_identity_time_speaker",
        baseline_run=BaselineRun(
            run_id=UUID(fixture_run.baseline_run_id),
            contamination=Contamination.CONTAMINATED,
            label="derived-assisted contaminated pre-change baseline",
        ),
        candidate_run=CandidateRun(
            run_id=UUID(fixture_run.run_id),
            contamination=Contamination.POLICY_CLEAN,
            label="policy-clean paired diagnostic",
        ),
        policy_digest=fixture_run.policy_digest,
        alignment_claims=tuple(claims),
        outcomes=outcomes,
        safety_counts=safety,
        evidence=evidence,
    )
    return EvidencePairedDiagnosticInput.bind(payload)


__all__ = [
    "DiagnosticBuildError",
    "build_fixture_input",
    "canonical_model_bytes",
    "diagnostic_error",
    "refuse",
]
