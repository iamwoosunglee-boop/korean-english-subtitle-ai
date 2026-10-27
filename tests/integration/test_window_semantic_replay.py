# Copyright 2026 Transcription Module contributors

"""Every claimed lexical and visual source owns exact cue semantics and time."""

from pathlib import Path

import pytest

from tests.integration.window_claim_support import load_policy_window_input
from tests.integration.window_keep_claim_support import asr_keep_draft
from tests.integration.window_mutation_claim_support import replacement_draft
from transcription_module.correction_provenance import text_digest
from transcription_module.visual_correction import CandidateOrigin
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
    window_cue_identity,
)


def test_same_time_unrelated_asr_cannot_be_added_to_valid_replacement(
    tmp_path: Path,
) -> None:
    """Temporal overlap alone does not establish lexical ownership."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    evidence = original.cue_evidence[0].model_copy(
        update={
            "asr_ordinals": (1, 2),
            "source_evidence_ids": (
                "asr:1",
                "asr:2",
                "subtitle:automatic-ko-orig:1",
                "vision:obs-replace",
            ),
        }
    )
    forged = original.model_copy(update={"cue_evidence": (evidence,)})

    with pytest.raises(WindowEvidenceError, match=r"ASR text.*before text"):
        validate_window_draft(data, forged)


def test_visual_caption_replacement_replays_without_subtitle_lexical_claim(
    tmp_path: Path,
) -> None:
    """A strong visual correction replays from ASR before-text plus observation."""
    data = load_policy_window_input(tmp_path)
    draft = replacement_draft(data, origin=CandidateOrigin.VISUAL_ONLY)

    validate_window_draft(data, draft)


def test_foreign_time_lexical_reference_cannot_pad_valid_coverage(
    tmp_path: Path,
) -> None:
    """Every claimed source, not merely one matching source, must overlap."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    evidence = original.cue_evidence[0].model_copy(
        update={
            "asr_ordinals": (1, 3),
            "source_evidence_ids": (
                "asr:1",
                "asr:3",
                "subtitle:automatic-ko-orig:1",
                "vision:obs-replace",
            ),
        }
    )
    forged = original.model_copy(update={"cue_evidence": (evidence,)})

    with pytest.raises(WindowEvidenceError, match=r"ASR time.*cue"):
        validate_window_draft(data, forged)


def test_keep_cannot_author_arbitrary_speech_absent_from_its_source(
    tmp_path: Path,
) -> None:
    """A self-consistent KEEP receipt cannot author stale generated text."""
    data = load_policy_window_input(tmp_path)
    original = asr_keep_draft(data)
    arbitrary = "원본 어디에도 없는 임의 발화"
    cue = original.cues[0].model_copy(update={"speech": arbitrary})
    receipt = original.corrections[0].model_copy(
        update={
            "before_text": arbitrary,
            "result_text": arbitrary,
            "before_digest": text_digest(arbitrary),
            "result_digest": text_digest(arbitrary),
        }
    )
    evidence = original.cue_evidence[0].model_copy(
        update={
            "cue_identity": window_cue_identity(cue),
            "speech_digest": text_digest(arbitrary),
        }
    )
    forged = original.model_copy(
        update={
            "cues": (cue,),
            "corrections": (receipt,),
            "cue_evidence": (evidence,),
        }
    )

    with pytest.raises(WindowEvidenceError, match="referenced source text"):
        validate_window_draft(data, forged)


def test_unknown_or_derived_subtitle_cue_never_gains_lexical_authority(
    tmp_path: Path,
) -> None:
    """Only exact admitted automatic-ko-orig identities may be referenced."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    evidence = original.cue_evidence[0].model_copy(
        update={
            "subtitle_track_ids": ("derived",),
            "subtitle_cue_ids": ("derived:1",),
            "source_evidence_ids": (
                "asr:1",
                "subtitle:derived:1",
                "vision:obs-replace",
            ),
        }
    )
    forged = original.model_copy(update={"cue_evidence": (evidence,)})

    with pytest.raises(WindowEvidenceError, match="unknown subtitle"):
        validate_window_draft(data, forged)


def test_foreign_frame_time_cannot_be_claimed_by_an_observation(
    tmp_path: Path,
) -> None:
    """Known frames still fail when they do not belong to the covered cue."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    observation = original.observations[0].model_copy(
        update={
            "frame_ids": (data.frames[1].frame_id,),
            "frame_pts_ms": (data.frames[1].pts_ms,),
        }
    )
    forged = original.model_copy(update={"observations": (observation,)})

    with pytest.raises(WindowEvidenceError, match=r"frame time.*cue"):
        validate_window_draft(data, forged)


def test_tampered_receipt_fails_deterministic_replay(tmp_path: Path) -> None:
    """A digest-shaped but stale persisted receipt cannot be published."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    stale = original.corrections[0].model_copy(update={"input_digest": "f" * 64})
    forged = original.model_copy(update={"corrections": (stale,)})

    with pytest.raises(WindowEvidenceError, match=r"receipt.*replay"):
        validate_window_draft(data, forged)


def test_direct_rewrite_without_matching_action_and_receipt_fails(
    tmp_path: Path,
) -> None:
    """Changing action metadata cannot bypass exact receipt semantics."""
    data = load_policy_window_input(tmp_path)
    original = replacement_draft(data)
    evidence = original.cue_evidence[0].model_copy(
        update={"requested_action": CorrectionAction.KEEP}
    )
    forged = original.model_copy(update={"cue_evidence": (evidence,)})

    with pytest.raises(WindowEvidenceError, match="action"):
        validate_window_draft(data, forged)
