# Copyright 2026 Transcription Module contributors

"""Policy-bound visual evidence and legacy sidecar contracts."""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import pytest
from pydantic import ValidationError

from transcription_module.correction_receipts import (
    CorrectionReceipt,
    CorrectionReceiptSet,
    EvidenceSidecarBundle,
)
from transcription_module.models import FrameId, RunId
from transcription_module.subtitle_inventory import (
    SubtitleInventory,
    SubtitleTrackIdentity,
)
from transcription_module.visual_correction import TextSlice
from transcription_module.visual_evidence_policy import (
    CorrectionAction,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
    canonical_policy_bytes,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import (
    VisualObservation,
    VisualObservationSet,
)
from translation_module.canonical import SourceTranscriptV2, TranslationDocumentV2

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
MINIMUM_DISTINCT_PTS = 2
SOURCE_V2_FIELDS = (
    "schema_version",
    "document_id",
    "revision_id",
    "supersedes_revision_id",
    "content_digest",
    "record_digest",
    "lifecycle_status",
    "source_locale",
    "duration_ms",
    "input_context_ref",
    "evidence",
    "cues",
    "finalization",
)
TRANSLATION_V2_FIELDS = (
    "schema_version",
    "document_id",
    "revision_id",
    "supersedes_revision_id",
    "content_digest",
    "record_digest",
    "source_locale",
    "target_locale",
    "source_ref",
    "prepared_context_ref",
    "glossary_ref",
    "lifecycle_status",
    "decision_status",
    "cues",
    "unresolved_decisions",
    "finalization",
)


def _inventory(policy_sha: str) -> SubtitleInventory:
    return SubtitleInventory(
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        tracks=(
            SubtitleTrackIdentity(
                track_id="automatic-ko-orig",
                variant="ko-orig",
                source="automatic",
                path="media/subtitles/automatic.ko.ko-orig.vtt",
                sha256=DIGEST_A,
                eligible_for_speech=True,
            ),
            SubtitleTrackIdentity(
                track_id="track-ko-duplicate",
                variant="ko",
                source="automatic",
                path="media/subtitles/automatic.ko.ko.vtt",
                sha256=DIGEST_A,
                eligible_for_speech=False,
                duplicate_of="automatic-ko-orig",
            ),
        ),
    )


def _observation_set(policy_sha: str) -> VisualObservationSet:
    return VisualObservationSet(
        run_id=RunId("run-policy-bound"),
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        observations=(
            VisualObservation(
                observation_id="observation-1",
                era=EvidenceEra.POLICY_BOUND,
                policy_digest=policy_sha,
                role=VisualRole.SPEECH_SUPPORTING,
                state=EvidenceState.SUPPORTED,
                semantic_relation=SemanticRelation.EXACT,
                frame_ids=(FrameId("f001"), FrameId("f002")),
                frame_pts_ms=(1_000, 1_200),
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                complete_text="서울에서 다시 만났어요",
                vad_interval_ids=("vad-1",),
                turn_ids=("turn-1",),
                caption_regime_supported=True,
                temporal_alignment_supported=True,
                speech_presence_supported=True,
                evidence_for=("two_distinct_pts", "vad_speech", "single_turn"),
            ),
        ),
    )


def _receipt_set(policy_sha: str) -> CorrectionReceiptSet:
    return CorrectionReceiptSet(
        run_id=RunId("run-policy-bound"),
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        receipts=(
            CorrectionReceipt(
                receipt_id="receipt-1",
                era=EvidenceEra.POLICY_BOUND,
                policy_digest=policy_sha,
                action=CorrectionAction.REPLACE_SPAN,
                source_digest=DIGEST_A,
                input_digest=DIGEST_B,
                before_text="서울에서 다시 만났어",
                result_text="서울에서 다시 만났어요",
                before_digest=hashlib.sha256(
                    "서울에서 다시 만났어".encode()
                ).hexdigest(),
                result_digest=hashlib.sha256(
                    "서울에서 다시 만났어요".encode()
                ).hexdigest(),
                observation_ids=("observation-1",),
                candidate_text="서울에서 다시 만났어요",
                candidate_digest=hashlib.sha256(
                    "서울에서 다시 만났어요".encode()
                ).hexdigest(),
                source_slice=TextSlice(start=10, end=11),
                candidate_slice=TextSlice(start=10, end=12),
            ),
        ),
    )


def test_default_policy_is_complete_canonical_and_digest_stable() -> None:
    """The exact policy serializes identically regardless of input key order."""
    policy = default_evidence_policy()
    reversed_payload = OrderedDict(reversed(policy.model_dump(mode="json").items()))
    reparsed = type(policy).model_validate(reversed_payload)

    assert policy.subtitle.allowed_variants == ("ko-orig",)
    assert policy.subtitle.deduplication == "sha256_then_variant"
    assert policy.roles == tuple(VisualRole)
    assert policy.states == tuple(EvidenceState)
    assert policy.actions == tuple(CorrectionAction)
    assert policy.normalizations == ("NFC", "line_join", "whitespace_collapse")
    assert policy.vad.ffmpeg_filter == "silencedetect=noise=-35dB:d=0.20"
    assert policy.insertion.minimum_distinct_pts == MINIMUM_DISTINCT_PTS
    assert policy.insertion.require_vad_speech is True
    assert policy.insertion.require_single_turn is True
    assert policy.insertion.require_existing_speaker is True
    assert policy.insertion.require_time_progression is True
    assert canonical_policy_bytes(reparsed) == canonical_policy_bytes(policy)
    assert policy_digest(reparsed) == policy_digest(policy)


def test_any_policy_field_or_contract_version_changes_the_digest() -> None:
    """Policy identity covers behavior fields and every sidecar version."""
    policy = default_evidence_policy()
    stricter = policy.model_copy(
        update={
            "insertion": policy.insertion.model_copy(
                update={"minimum_distinct_pts": 3}
            )
        }
    )
    newer = policy.model_copy(
        update={
            "contract_versions": policy.contract_versions.model_copy(
                update={"visual_observation": "visual-observation.v2"}
            )
        }
    )

    assert policy_digest(stricter) != policy_digest(policy)
    assert policy_digest(newer) != policy_digest(policy)


def test_unknown_action_and_malformed_digest_are_rejected() -> None:
    """Unknown policy vocabulary and non-SHA bindings fail at parse time."""
    payload = default_evidence_policy().model_dump(mode="json")
    payload["actions"] = ["KEEP", "REWRITE_FREELY"]

    with pytest.raises(ValidationError):
        _ = type(default_evidence_policy()).model_validate(payload)
    with pytest.raises(ValidationError):
        _ = SubtitleInventory(
            era=EvidenceEra.POLICY_BOUND,
            policy_digest="not-a-digest",
            tracks=(),
        )


def test_policy_bound_inventory_admits_only_exact_automatic_ko_orig() -> None:
    """Derived, manual, unknown, and duplicate tracks cannot become speech input."""
    policy_sha = policy_digest(default_evidence_policy())
    inventory = _inventory(policy_sha)

    assert tuple(track.track_id for track in inventory.eligible_tracks) == (
        "automatic-ko-orig",
    )
    derived = inventory.tracks[0].model_copy(
        update={"variant": "ko-en", "sha256": DIGEST_B}
    )
    manual = inventory.tracks[0].model_copy(update={"source": "manual"})

    derived_payload = inventory.model_dump(mode="json")
    derived_payload["tracks"] = [derived.model_dump(mode="json")]
    manual_payload = inventory.model_dump(mode="json")
    manual_payload["tracks"] = [manual.model_dump(mode="json")]

    with pytest.raises(ValidationError):
        _ = SubtitleInventory.model_validate(derived_payload)
    with pytest.raises(ValidationError):
        _ = SubtitleInventory.model_validate(manual_payload)


def test_observation_and_correction_sidecars_bind_policy_and_vocabularies() -> None:
    """New-era observations and receipts retain exact policy identity."""
    policy_sha = policy_digest(default_evidence_policy())
    observations = _observation_set(policy_sha)
    receipts = _receipt_set(policy_sha)

    assert observations.observations[0].role is VisualRole.SPEECH_SUPPORTING
    assert observations.observations[0].state is EvidenceState.SUPPORTED
    assert receipts.receipts[0].action is CorrectionAction.REPLACE_SPAN
    mismatched = observations.model_dump(mode="json")
    mismatched["policy_digest"] = DIGEST_B
    with pytest.raises(ValidationError):
        _ = VisualObservationSet.model_validate(mismatched)


def test_legacy_sidecars_parse_but_policy_bound_bundle_requires_all_matches() -> None:
    """Legacy reads remain permissive while new runs fail closed."""
    legacy = EvidenceSidecarBundle(era=EvidenceEra.LEGACY)
    policy = default_evidence_policy()
    policy_sha = policy_digest(policy)

    assert legacy.policy is None
    with pytest.raises(ValidationError):
        _ = EvidenceSidecarBundle(
            era=EvidenceEra.POLICY_BOUND,
            policy=policy,
            inventory=_inventory(policy_sha),
            observations=_observation_set(policy_sha),
        )
    bundle = EvidenceSidecarBundle(
        era=EvidenceEra.POLICY_BOUND,
        policy=policy,
        inventory=_inventory(policy_sha),
        observations=_observation_set(policy_sha),
        corrections=_receipt_set(policy_sha),
    )
    assert bundle.policy_digest == policy_sha

    with pytest.raises(ValidationError):
        _ = EvidenceSidecarBundle(
            era=EvidenceEra.POLICY_BOUND,
            policy=policy,
            inventory=_inventory(DIGEST_B),
            observations=_observation_set(policy_sha),
            corrections=_receipt_set(policy_sha),
        )


def test_public_canonical_v2_wire_fields_remain_unchanged() -> None:
    """Visual sidecars add no required field to either canonical v2 document."""
    assert tuple(SourceTranscriptV2.model_fields) == SOURCE_V2_FIELDS
    assert tuple(TranslationDocumentV2.model_fields) == TRANSLATION_V2_FIELDS
