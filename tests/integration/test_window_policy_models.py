# Copyright 2026 Transcription Module contributors

"""Policy-bound window contracts replace the legacy review assertion."""

from typing import Final

import pytest
from pydantic import ValidationError

from transcription_module.drafting import WindowDraft, WindowDraftInput

POLICY_SHA: Final = "a" * 64
INVENTORY_SHA: Final = "b" * 64
SOURCE_SHA: Final = "c" * 64


def _policy_identity() -> dict[str, str]:
    return {
        "era": "policy_bound",
        "policy_digest": POLICY_SHA,
        "inventory_digest": INVENTORY_SHA,
        "source_digest": SOURCE_SHA,
    }


def test_complete_policy_contract_parses_when_authority_is_explicit() -> None:
    """Given complete identities, when parsed, then coverage replaces a boolean."""
    window_input = WindowDraftInput.model_validate(
        {
            "window_index": 0,
            "run_id": "policy-model-run",
            "core_start_ms": 0,
            "core_end_ms": 1_000,
            "context_start_ms": 0,
            "context_end_ms": 1_000,
            "asr_segments": [],
            "subtitle_tracks": [],
            "frames": [],
            "subtitle_cues": [],
            "vad_intervals": [],
            "verified_turns": [],
            **_policy_identity(),
        }
    )
    draft = WindowDraft.model_validate(
        {
            "window_index": 0,
            "cues": [],
            "unresolved": [],
            "observations": [],
            "corrections": [],
            "cue_evidence": [],
            **_policy_identity(),
        }
    )

    assert window_input.era.value == "policy_bound"
    assert draft.cue_evidence == ()


def test_legacy_boolean_is_rejected_when_policy_authority_is_claimed() -> None:
    """Given policy identity, when a boolean claims review, then parsing fails."""
    with pytest.raises(ValidationError, match="legacy evidence review boolean"):
        _ = WindowDraft.model_validate(
            {
                "window_index": 0,
                "cues": [],
                "unresolved": [],
                "evidence_reviewed": True,
                "observations": [],
                "corrections": [],
                "cue_evidence": [],
                **_policy_identity(),
            }
        )
