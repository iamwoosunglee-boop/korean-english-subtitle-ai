# Copyright 2026 Transcription Module contributors

"""CSV serialization for canonical transcript snapshots."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transcription_module.models import Transcript

CSV_COLUMNS = (
    "sequence",
    "cue_id",
    "start_tc_ms",
    "end_tc_ms",
    "speech",
    "speaker",
    "uncertainty",
    "screen_text",
    "vision_evidence_ids",
)


def transcript_as_csv(transcript: Transcript) -> str:
    """Derive the user-facing CSV from canonical cue models."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for cue in transcript.cues:
        writer.writerow(
            {
                "sequence": cue.sequence,
                "cue_id": cue.cue_id or "",
                "start_tc_ms": cue.start_tc_ms,
                "end_tc_ms": cue.end_tc_ms,
                "speech": cue.speech,
                "speaker": cue.speaker or "",
                "uncertainty": cue.uncertainty.value,
                "screen_text": cue.screen_text or "",
                "vision_evidence_ids": "|".join(cue.vision_evidence_ids),
            }
        )
    return output.getvalue()



__all__ = ["CSV_COLUMNS", "transcript_as_csv"]
