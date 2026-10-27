# Copyright 2026 Transcription Module contributors

"""Stable semantic identity for one temporary window cue."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transcription_module._drafting_models import DraftCue


def window_cue_identity(cue: DraftCue) -> str:
    """Digest every transcript-relevant cue field with canonical JSON."""
    payload = json.dumps(
        {
            "end_tc_ms": cue.end_tc_ms,
            "screen_text": cue.screen_text,
            "speaker": cue.speaker,
            "speech": cue.speech,
            "start_tc_ms": cue.start_tc_ms,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"cue:{hashlib.sha256(payload).hexdigest()}"


__all__ = ["window_cue_identity"]
