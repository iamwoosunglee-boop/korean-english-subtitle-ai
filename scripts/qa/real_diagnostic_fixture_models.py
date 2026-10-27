# Copyright 2026 Transcription Module contributors

"""Strict fixture authority and evidence-bearing Task-4 input models."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from scripts.qa.paired_diagnostic_models import (
    AlignmentClaim,
    BaselineRun,
    CandidateRun,
    DiagnosticModel,
    DiagnosticOutcomes,
    NonBlank,
    NonNegative,
    SafetyCounts,
    Sha256Digest,
)

type ArtifactKind = Literal[
    "candidate_transcript",
    "policy",
    "inventory",
    "asr",
    "vad",
    "observations",
    "corrections",
    "provenance",
    "speaker_authority_v2",
    "verified_speaker_turns_v3",
    "final_review_assignment",
    "final_review_result",
    "final_review_receipt",
    "final_review_proposal",
    "final_review_decision",
    "final_review_application",
    "confirmation_anchor",
]
type EvidenceDigestField = Literal[
    "candidate_transcript_sha256",
    "policy_file_sha256",
    "inventory_file_sha256",
    "speaker_authority_sha256",
    "verified_speaker_turns_sha256",
    "final_review_assignment_digest",
    "final_review_result_digest",
    "final_review_receipt_digest",
    "final_review_proposal_digest",
    "final_review_decision_digest",
    "final_review_application_digest",
    "confirmation_anchor_digest",
]

ARTIFACT_KINDS: Final[tuple[ArtifactKind, ...]] = (
    "candidate_transcript",
    "policy",
    "inventory",
    "asr",
    "vad",
    "observations",
    "corrections",
    "provenance",
    "speaker_authority_v2",
    "verified_speaker_turns_v3",
    "final_review_assignment",
    "final_review_result",
    "final_review_receipt",
    "final_review_proposal",
    "final_review_decision",
    "final_review_application",
    "confirmation_anchor",
)
ARTIFACT_PATHS: Final[tuple[tuple[ArtifactKind, str], ...]] = (
    ("candidate_transcript", "artifacts/candidate-transcript.json"),
    ("policy", "artifacts/visual-evidence-policy.json"),
    ("inventory", "artifacts/subtitle-inventory.json"),
    ("asr", "artifacts/asr.json"),
    ("vad", "artifacts/vad.json"),
    ("observations", "artifacts/visual-observations.json"),
    ("corrections", "artifacts/correction-receipts.json"),
    ("provenance", "artifacts/cue-provenance.json"),
    ("speaker_authority_v2", "artifacts/speaker-turn-authority.v2.json"),
    ("verified_speaker_turns_v3", "artifacts/verified-speaker-turns.v3.json"),
    ("final_review_assignment", "artifacts/final-review-assignment.json"),
    ("final_review_result", "artifacts/final-review-result.json"),
    ("final_review_receipt", "artifacts/final-review-receipt.json"),
    ("final_review_proposal", "artifacts/final-review-proposal.json"),
    ("final_review_decision", "artifacts/final-review-decision.json"),
    ("final_review_application", "artifacts/final-review-application.json"),
    ("confirmation_anchor", "artifacts/confirmation-anchor.json"),
)
ARTIFACT_EVIDENCE_FIELDS: Final[
    tuple[tuple[ArtifactKind, EvidenceDigestField], ...]
] = (
    ("candidate_transcript", "candidate_transcript_sha256"),
    ("policy", "policy_file_sha256"),
    ("inventory", "inventory_file_sha256"),
    ("speaker_authority_v2", "speaker_authority_sha256"),
    ("verified_speaker_turns_v3", "verified_speaker_turns_sha256"),
    ("final_review_assignment", "final_review_assignment_digest"),
    ("final_review_result", "final_review_result_digest"),
    ("final_review_receipt", "final_review_receipt_digest"),
    ("final_review_proposal", "final_review_proposal_digest"),
    ("final_review_decision", "final_review_decision_digest"),
    ("final_review_application", "final_review_application_digest"),
    ("confirmation_anchor", "confirmation_anchor_digest"),
)
Character = Annotated[str, StringConstraints(min_length=1, max_length=1)]
EXPECTED_INTERVALS: Final = 19


def _canonical_sha(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class InputMode(StrEnum):
    """Whether diagnostic authority is synthetic or current-run evidence."""

    FIXTURE = "fixture"
    REAL_RUN = "real_run"


class ArtifactRecord(DiagnosticModel):
    """One ordered, exact fixture artifact identity."""

    relative_path: NonBlank
    kind: ArtifactKind
    mode: NonNegative
    size: NonNegative
    sha256: Sha256Digest


class PairedDiagnosticFixtureRun(DiagnosticModel):
    """Complete synthetic artifact snapshot accepted only in fixture mode."""

    schema_version: Literal["paired-diagnostic-fixture-run.v1"] = (
        "paired-diagnostic-fixture-run.v1"
    )
    input_mode: Literal[InputMode.FIXTURE] = InputMode.FIXTURE
    run_id: NonBlank
    baseline_run_id: NonBlank
    policy_digest: Sha256Digest
    artifacts: tuple[ArtifactRecord, ...] = Field(min_length=1)
    artifact_snapshot_digest: Sha256Digest
    disallowed_track_contribution: NonNegative
    non_speech_unknown_elevation: NonNegative
    user_confirmed_overwrite: NonNegative

    @model_validator(mode="after")
    def artifact_order_is_canonical(self) -> Self:
        """Require the complete ordered manifest without repeated paths."""
        kinds = tuple(record.kind for record in self.artifacts)
        paths = tuple(record.relative_path for record in self.artifacts)
        if kinds != ARTIFACT_KINDS:
            message = "fixture artifact kinds must use canonical order"
            raise ValueError(message)
        if len(paths) != len(set(paths)):
            message = "fixture artifact paths must be unique"
            raise ValueError(message)
        return self


class MeaningAssessment(StrEnum):
    """Closed semantic result for one frozen case."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    MISSING = "missing"
    UNASSESSABLE = "unassessable"


class PresenceAssessment(StrEnum):
    """Closed mandatory-meaning presence result."""

    PRESENT = "present"
    MISSING = "missing"
    UNASSESSABLE = "unassessable"
    NOT_IN_POPULATION = "not_in_population"


class AutomaticActionAssessment(StrEnum):
    """Closed automatic action reviewed for one case."""

    KEEP = "keep"
    REPLACE_SPAN = "replace_span"
    INSERT_CUE = "insert_cue"
    HOLD_FOR_CONFIRMATION = "hold_for_confirmation"


class OwnerAssessment(StrEnum):
    """Closed critical-owner result."""

    CORRECT = "correct"
    WRONG = "wrong"
    NOT_APPLICABLE = "not_applicable"


class DamageAssessment(StrEnum):
    """Closed automatic-damage result."""

    PRESERVED = "preserved"
    DAMAGED = "damaged"
    NOT_APPLICABLE = "not_applicable"


class AssessmentIntervalMapping(DiagnosticModel):
    """One frozen interval bound to exact synthetic cue and turn identities."""

    interval_id: NonBlank
    candidate_cue_id: NonBlank
    authority_turn_ids: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def authority_turns_are_unique(self) -> Self:
        """Reject repeated authority turns within one interval bridge."""
        if len(self.authority_turn_ids) != len(set(self.authority_turn_ids)):
            message = "assessment authority turn IDs must be unique"
            raise ValueError(message)
        return self


class AssessmentCharacterTrace(DiagnosticModel):
    """One exact changed-character row supplied by fixture assessment."""

    character_id: NonBlank
    value: Character
    source_evidence_id: NonBlank
    source_value: NonBlank

    @model_validator(mode="after")
    def source_contains_character(self) -> Self:
        """Require the cited source value to contain the changed character."""
        if self.value not in self.source_value:
            message = "assessment character is absent from source value"
            raise ValueError(message)
        return self


class CaseAssessment(DiagnosticModel):
    """One exhaustive closed-enum assessment for a frozen case."""

    case_id: NonBlank
    interval_mappings: tuple[AssessmentIntervalMapping, ...] = Field(min_length=1)
    final_meaning: MeaningAssessment
    clean_asr_mandatory_meaning: PresenceAssessment
    final_mandatory_meaning: PresenceAssessment
    automatic_action: AutomaticActionAssessment
    critical_owner: OwnerAssessment
    automatic_damage: DamageAssessment
    unsupported_additions_before: NonNegative
    unsupported_additions_after: NonNegative
    character_traces: tuple[AssessmentCharacterTrace, ...]

    @model_validator(mode="after")
    def case_references_are_unique(self) -> Self:
        """Reject repeated intervals or trace identities within one case."""
        intervals = tuple(item.interval_id for item in self.interval_mappings)
        characters = tuple(item.character_id for item in self.character_traces)
        if len(intervals) != len(set(intervals)):
            message = "assessment interval IDs must be unique per case"
            raise ValueError(message)
        if len(characters) != len(set(characters)):
            message = "assessment character IDs must be unique"
            raise ValueError(message)
        return self


class AssessmentAuthor(DiagnosticModel):
    """One fixture author response over all frozen cases."""

    execution_id: NonBlank
    cases: tuple[CaseAssessment, ...] = Field(min_length=1)


class AssessmentReview(DiagnosticModel):
    """Independent approval bound to exact author and snapshot bytes."""

    execution_id: NonBlank
    author_payload_sha256: Sha256Digest
    independently_opened_snapshot_digest: Sha256Digest
    decision: Literal["approve", "reject"]


class PairedDiagnosticAssessment(DiagnosticModel):
    """Strict independently reviewed fixture assessment."""

    schema_version: Literal["paired-diagnostic-assessment.v1"] = (
        "paired-diagnostic-assessment.v1"
    )
    fixture_sha256: Sha256Digest
    candidate_run_id: NonBlank
    artifact_snapshot_digest: Sha256Digest
    assessment_rule_version: Literal["paired-diagnostic-assessment-rules.v1"]
    author: AssessmentAuthor
    review: AssessmentReview

    @model_validator(mode="after")
    def review_is_independent(self) -> Self:
        """Require distinct author and reviewer execution identities."""
        if self.author.execution_id == self.review.execution_id:
            message = "assessment identities must differ"
            raise ValueError(message)
        return self


class IntervalEvidenceBridge(DiagnosticModel):
    """One evaluator interval connected to exact candidate evidence."""

    interval_id: NonBlank
    candidate_cue_id: NonBlank
    authority_turn_ids: tuple[NonBlank, ...] = Field(min_length=1)


class CaseEvidenceBridge(DiagnosticModel):
    """One frozen case and all of its ordered interval evidence."""

    case_id: NonBlank
    intervals: tuple[IntervalEvidenceBridge, ...] = Field(min_length=1)


class DiagnosticEvidenceManifest(DiagnosticModel):
    """Evidence embedded directly in one paired-diagnostic v2 input."""

    fixture_sha256: Sha256Digest
    artifact_manifest: tuple[ArtifactRecord, ...] = Field(min_length=1)
    artifact_manifest_sha256: Sha256Digest
    artifact_snapshot_digest: Sha256Digest
    assessment_sha256: Sha256Digest
    assessment_author_payload_sha256: Sha256Digest
    candidate_transcript_sha256: Sha256Digest
    working_transcript_digest: Sha256Digest
    policy_file_sha256: Sha256Digest
    policy_digest: Sha256Digest
    inventory_file_sha256: Sha256Digest
    inventory_digest: Sha256Digest
    source_authority_digest: Sha256Digest
    source_digest: Sha256Digest
    speaker_authority_sha256: Sha256Digest
    verified_speaker_turns_sha256: Sha256Digest
    final_review_assignment_digest: Sha256Digest
    final_review_result_digest: Sha256Digest
    final_review_receipt_digest: Sha256Digest
    final_review_proposal_digest: Sha256Digest
    final_review_decision_digest: Sha256Digest
    final_review_application_digest: Sha256Digest
    confirmation_anchor_digest: Sha256Digest
    case_evidence: tuple[CaseEvidenceBridge, ...] = Field(
        min_length=17,
        max_length=17,
    )
    validation_expected_absent: Literal[True] = True

    @model_validator(mode="after")
    def frozen_bridge_population_is_complete(self) -> Self:
        """Require exactly 17 cases and 19 unique comparable intervals."""
        kinds = tuple(record.kind for record in self.artifact_manifest)
        manifest_payload = [
            record.model_dump(mode="json") for record in self.artifact_manifest
        ]
        manifest_sha = _canonical_sha(manifest_payload)
        if kinds != ARTIFACT_KINDS:
            message = "v2 artifact manifest order is not canonical"
            raise ValueError(message)
        by_kind = {
            record.kind: record.sha256 for record in self.artifact_manifest
        }
        digest_bindings = (
            (self.candidate_transcript_sha256, by_kind["candidate_transcript"]),
            (self.policy_file_sha256, by_kind["policy"]),
            (self.inventory_file_sha256, by_kind["inventory"]),
            (self.speaker_authority_sha256, by_kind["speaker_authority_v2"]),
            (
                self.verified_speaker_turns_sha256,
                by_kind["verified_speaker_turns_v3"],
            ),
            (
                self.final_review_assignment_digest,
                by_kind["final_review_assignment"],
            ),
            (self.final_review_result_digest, by_kind["final_review_result"]),
            (self.final_review_receipt_digest, by_kind["final_review_receipt"]),
            (self.final_review_proposal_digest, by_kind["final_review_proposal"]),
            (self.final_review_decision_digest, by_kind["final_review_decision"]),
            (
                self.final_review_application_digest,
                by_kind["final_review_application"],
            ),
            (self.confirmation_anchor_digest, by_kind["confirmation_anchor"]),
        )
        if (
            manifest_sha != self.artifact_manifest_sha256
            or manifest_sha != self.artifact_snapshot_digest
            or any(left != right for left, right in digest_bindings)
        ):
            message = "v2 artifact manifest bindings are inconsistent"
            raise ValueError(message)
        cases = tuple(item.case_id for item in self.case_evidence)
        intervals = tuple(
            interval.interval_id
            for case in self.case_evidence
            for interval in case.intervals
        )
        if len(cases) != len(set(cases)):
            message = "v2 evidence case IDs must be unique"
            raise ValueError(message)
        if (
            len(intervals) != EXPECTED_INTERVALS
            or len(intervals) != len(set(intervals))
        ):
            message = "v2 evidence requires 19 unique interval bridges"
            raise ValueError(message)
        return self


class EvidencePairedDiagnosticPayload(DiagnosticModel):
    """Unbound evidence-bearing fields used only by the trusted builder."""

    schema_version: Literal["paired-diagnostic-input.v2"] = (
        "paired-diagnostic-input.v2"
    )
    input_mode: InputMode
    evaluation_kind: Literal["same_video_paired_diagnostic"]
    comparison_method: Literal["comparable_intervals"]
    alignment_basis: Literal["source_identity_time_speaker"]
    baseline_run: BaselineRun
    candidate_run: CandidateRun
    policy_digest: Sha256Digest
    alignment_claims: tuple[AlignmentClaim, ...]
    outcomes: DiagnosticOutcomes
    safety_counts: SafetyCounts
    evidence: DiagnosticEvidenceManifest

    @model_validator(mode="after")
    def policy_and_candidate_bindings_match(self) -> Self:
        """Require evaluator and embedded evidence policy digests to match."""
        if self.policy_digest != self.evidence.policy_digest:
            message = "v2 evidence policy digest differs from evaluator input"
            raise ValueError(message)
        return self


class EvidencePairedDiagnosticInput(EvidencePairedDiagnosticPayload):
    """Hash-bound evaluator input for fixture and future real-run modes."""

    input_payload_sha256: Sha256Digest

    @classmethod
    def bind(
        cls,
        payload: EvidencePairedDiagnosticPayload,
    ) -> Self:
        """Bind every v2 evaluator and evidence field into one digest."""
        data = payload.model_dump(mode="json")
        data["input_payload_sha256"] = _canonical_sha(data)
        return cls.model_validate(data)

    @model_validator(mode="after")
    def input_payload_is_bound(self) -> Self:
        """Reject any post-build change to outcomes, safety, or evidence."""
        payload = self.model_dump(
            mode="json",
            exclude={"input_payload_sha256"},
        )
        if self.input_payload_sha256 != _canonical_sha(payload):
            message = "v2 input payload digest is stale"
            raise ValueError(message)
        return self


__all__ = [
    "ARTIFACT_EVIDENCE_FIELDS",
    "ARTIFACT_KINDS",
    "ARTIFACT_PATHS",
    "ArtifactKind",
    "ArtifactRecord",
    "AssessmentAuthor",
    "AssessmentCharacterTrace",
    "AssessmentIntervalMapping",
    "AssessmentReview",
    "AutomaticActionAssessment",
    "CaseAssessment",
    "CaseEvidenceBridge",
    "DamageAssessment",
    "DiagnosticEvidenceManifest",
    "EvidencePairedDiagnosticInput",
    "EvidencePairedDiagnosticPayload",
    "InputMode",
    "IntervalEvidenceBridge",
    "MeaningAssessment",
    "OwnerAssessment",
    "PairedDiagnosticAssessment",
    "PairedDiagnosticFixtureRun",
    "PresenceAssessment",
]
