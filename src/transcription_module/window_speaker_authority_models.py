# Copyright 2026 Transcription Module contributors

"""Independent persisted authority for named speaker-turn decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrozenModel, RunId

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module._drafting_models import DiarizationRunArtifact
    from transcription_module.subtitle_inventory import SubtitleInventory

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]


@dataclass(frozen=True, slots=True)
class SpeakerTurnEvidenceSnapshot:
    """Exact authority, observation, and decision bytes captured before replay."""

    authority_payload: bytes
    observation_payload: bytes
    decision_payload: bytes


@dataclass(frozen=True, slots=True)
class SpeakerTurnReplayContext:
    """Current source and inventory checkpoint used to replay named turns."""

    run_dir: Path
    run_id: RunId
    policy_digest: str
    source_digest: str
    duration_ms: int
    frame_points: tuple[tuple[str, int], ...]
    vad_intervals: tuple[tuple[str, int, int], ...]
    inventory: SubtitleInventory
    diarization: DiarizationRunArtifact | None


class SpeakerTurnAuthorityRecord(FrozenModel):
    """One canonical turn mapped to persisted evidence and decision records."""

    turn_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    cluster: NonBlank
    speaker: NonBlank
    verification_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    verification_decision_ids: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def record_is_bounded_and_unique(self) -> Self:
        """Reject invalid timing and repeated proof references."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "speaker-turn authority end must follow start"
            raise ValueError(message)
        for values in (
            self.verification_evidence_ids,
            self.verification_decision_ids,
        ):
            if len(values) != len(set(values)):
                message = "speaker-turn authority references must be unique"
                raise ValueError(message)
        return self


class SpeakerTurnAuthoritySet(FrozenModel):
    """Policy-bound authority admitted independently of the derived turn file."""

    schema_version: Literal["speaker-turn-authority.v1"] = "speaker-turn-authority.v1"
    run_id: RunId
    policy_digest: Sha256Digest
    source_digest: Sha256Digest
    observation_path: Literal["visual-observations.json"] = "visual-observations.json"
    observation_sha256: Sha256Digest
    decision_path: Literal["speaker-turn-decisions.jsonl"] = (
        "speaker-turn-decisions.jsonl"
    )
    decision_sha256: Sha256Digest
    records: tuple[SpeakerTurnAuthorityRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def authority_ownership_is_unique(self) -> Self:
        """One admitted evidence or decision record may prove only one turn."""
        turn_ids = tuple(item.turn_id for item in self.records)
        evidence_ids = tuple(
            value for item in self.records for value in item.verification_evidence_ids
        )
        decision_ids = tuple(
            value for item in self.records for value in item.verification_decision_ids
        )
        if len(turn_ids) != len(set(turn_ids)):
            message = "speaker-turn authority turn IDs must be unique"
            raise ValueError(message)
        if len(evidence_ids) != len(set(evidence_ids)) or len(decision_ids) != len(
            set(decision_ids)
        ):
            message = "speaker-turn authority cannot reuse proof records"
            raise ValueError(message)
        return self


def canonical_speaker_turn_authority_bytes(
    authority: SpeakerTurnAuthoritySet,
) -> bytes:
    """Serialize independent authority in its sole admitted byte form."""
    payload = json.dumps(
        authority.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{payload}\n".encode()


def speaker_turn_authority_digest(
    run_id: RunId,
    source_digest: str,
    record: SpeakerTurnAuthorityRecord,
) -> str:
    """Digest every canonical turn and proof-reference field."""
    payload = json.dumps(
        {
            "run_id": str(run_id),
            "source_digest": source_digest,
            **record.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "SpeakerTurnAuthorityRecord",
    "SpeakerTurnAuthoritySet",
    "SpeakerTurnEvidenceSnapshot",
    "SpeakerTurnReplayContext",
    "canonical_speaker_turn_authority_bytes",
    "speaker_turn_authority_digest",
]
