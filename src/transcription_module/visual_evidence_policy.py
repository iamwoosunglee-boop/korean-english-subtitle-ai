# Copyright 2026 Transcription Module contributors

"""Canonical policy identity for policy-bound visual transcription evidence."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrozenModel

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
BASE_SUBTITLE_ADAPTER_CONTRACT = "yt-dlp-subtitles.v1"


class EvidenceEra(StrEnum):
    """Compatibility boundary for persisted transcription evidence."""

    LEGACY = "legacy"
    POLICY_BOUND = "policy_bound"


class VisualRole(StrEnum):
    """Transcript-facing role assigned to complete visible text."""

    SPEECH_SUPPORTING = "speech_supporting"
    NON_SPEECH = "non_speech"
    UNKNOWN = "unknown"


class EvidenceState(StrEnum):
    """Whether the available evidence supports one role decision."""

    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class SemanticRelation(StrEnum):
    """Bounded relation between visible text and candidate speech."""

    EXACT = "exact"
    SELECTIVE = "selective"
    CONDENSED = "condensed"
    PARAPHRASE = "paraphrase"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class CorrectionAction(StrEnum):
    """The only visual-correction actions allowed by policy."""

    KEEP = "KEEP"
    REPLACE_SPAN = "REPLACE_SPAN"
    INSERT_CUE = "INSERT_CUE"
    HOLD_FOR_CONFIRMATION = "HOLD_FOR_CONFIRMATION"


class NormalizationRule(StrEnum):
    """Text-only transformations allowed for visual candidate bytes."""

    NFC = "NFC"
    LINE_JOIN = "line_join"
    WHITESPACE_COLLAPSE = "whitespace_collapse"


class ConflictRule(StrEnum):
    """Conditions that force abstention instead of speech mutation."""

    CUT_CHANGE = "cut_change"
    STYLE_CHANGE = "style_change"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    VAD_NEGATIVE = "vad_negative"
    OVERLAP_AMBIGUITY = "overlap_ambiguity"
    CONFIRMED_FIELD = "confirmed_field"


class EvidenceContractVersions(FrozenModel):
    """Persisted sidecar contract versions covered by the policy digest."""

    subtitle_inventory: NonBlank = "subtitle-inventory.v1"
    visual_observation: NonBlank = "visual-observation.v1"
    correction_receipt: NonBlank = "correction-receipt.v1"
    cue_provenance: NonBlank = "cue-provenance.v1"


class SubtitleEvidencePolicy(FrozenModel):
    """Exact subtitle admission and deterministic duplicate handling."""

    allowed_variants: tuple[Literal["ko-orig"], ...] = ("ko-orig",)
    allowed_source: Literal["automatic"] = "automatic"
    deduplication: Literal["sha256_then_variant"] = "sha256_then_variant"
    absence_is_optional: bool = True

    @model_validator(mode="after")
    def exact_allowlist_is_fixed(self) -> Self:
        """Reject broad or empty subtitle allowlists."""
        if self.allowed_variants != ("ko-orig",):
            message = "subtitle allowlist must contain exact ko-orig only"
            raise ValueError(message)
        return self


class VadEvidencePolicy(FrozenModel):
    """Exact existing FFmpeg speech-presence contract."""

    ffmpeg_filter: Literal["silencedetect=noise=-35dB:d=0.20"] = (
        "silencedetect=noise=-35dB:d=0.20"
    )
    lexical_authority: Literal[False] = False
    speaker_authority: Literal[False] = False


class VisualInsertionPolicy(FrozenModel):
    """Strong conditions required for a visual-only speech insertion."""

    minimum_distinct_pts: int = Field(default=2, ge=2)
    require_complete_text: bool = True
    require_vad_speech: bool = True
    require_single_turn: bool = True
    require_existing_speaker: bool = True
    require_time_progression: bool = True
    forbid_cut_style_ownership_conflicts: bool = True


class VisualEvidencePolicy(FrozenModel):
    """Canonical policy whose exact bytes bind all new evidence authority."""

    schema_version: Literal["visual-evidence-policy.v1"] = (
        "visual-evidence-policy.v1"
    )
    contract_versions: EvidenceContractVersions = EvidenceContractVersions()
    subtitle: SubtitleEvidencePolicy = SubtitleEvidencePolicy()
    roles: tuple[VisualRole, ...] = tuple(VisualRole)
    states: tuple[EvidenceState, ...] = tuple(EvidenceState)
    actions: tuple[CorrectionAction, ...] = tuple(CorrectionAction)
    normalizations: tuple[NormalizationRule, ...] = tuple(NormalizationRule)
    vad: VadEvidencePolicy = VadEvidencePolicy()
    insertion: VisualInsertionPolicy = VisualInsertionPolicy()
    conflicts: tuple[ConflictRule, ...] = tuple(ConflictRule)

    @model_validator(mode="after")
    def policy_vocabularies_are_complete(self) -> Self:
        """Prevent a persisted policy from silently omitting an enum member."""
        expected = (
            self.roles == tuple(VisualRole)
            and self.states == tuple(EvidenceState)
            and self.actions == tuple(CorrectionAction)
            and self.normalizations == tuple(NormalizationRule)
            and self.conflicts == tuple(ConflictRule)
        )
        if not expected:
            message = "policy vocabularies must contain every canonical member"
            raise ValueError(message)
        return self


def default_evidence_policy() -> VisualEvidencePolicy:
    """Return the sole current policy without hidden environment defaults."""
    return VisualEvidencePolicy()


def canonical_policy_bytes(policy: VisualEvidencePolicy) -> bytes:
    """Serialize policy JSON with fixed UTF-8, key order, and separators."""
    payload = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload.encode("utf-8")


def policy_digest(policy: VisualEvidencePolicy) -> str:
    """Return the lowercase SHA-256 identity of canonical policy bytes."""
    return hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()


def subtitle_adapter_contract(policy: VisualEvidencePolicy) -> str:
    """Bind subtitle checkpoint reuse to inventory version and policy bytes."""
    return ":".join(
        (
            BASE_SUBTITLE_ADAPTER_CONTRACT,
            policy.contract_versions.subtitle_inventory,
            policy_digest(policy),
        )
    )


__all__ = [
    "CorrectionAction",
    "EvidenceEra",
    "EvidenceState",
    "SemanticRelation",
    "VisualEvidencePolicy",
    "VisualRole",
    "canonical_policy_bytes",
    "default_evidence_policy",
    "policy_digest",
    "subtitle_adapter_contract",
]
