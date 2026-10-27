# Copyright 2026 Transcription Module contributors

"""Pure transcript quality findings for validation."""

from __future__ import annotations

from transcription_module.models import (
    Transcript,
    Uncertainty,
    ValidationFinding,
    ValidationFindingCode,
    has_pathological_repetition,
)
from transcription_module.speaker_notation import (
    SpeakerNotationError,
    parse_speaker_labels,
)

MAX_CUE_DURATION_MS = 30_000


def validation_findings(transcript: Transcript) -> tuple[ValidationFinding, ...]:
    """Return ordered machine and human findings for working source content."""
    findings: list[ValidationFinding] = []
    for index, cue in enumerate(transcript.cues):
        prefix = f"cue {cue.sequence}"
        cue_ids = (cue.cue_id,) if cue.cue_id is not None else ()
        canonical_speech = "\n".join(
            " ".join(line.split()) for line in cue.speech.split("\n")
        )
        try:
            labels = parse_speaker_labels(cue.speaker)
        except SpeakerNotationError:
            labels = ()
        checks = (
            (
                ValidationFindingCode.PATHOLOGICAL_REPETITION,
                has_pathological_repetition(cue.speech),
                "has pathological repetition",
            ),
            (
                ValidationFindingCode.UNREALISTIC_DURATION,
                int(cue.end_tc_ms) - int(cue.start_tc_ms) > MAX_CUE_DURATION_MS,
                "has unrealistic duration",
            ),
            (
                ValidationFindingCode.ABNORMAL_WHITESPACE,
                cue.speech != canonical_speech,
                "has abnormal whitespace",
            ),
            (
                ValidationFindingCode.UNRESOLVED_SPEECH,
                cue.uncertainty is Uncertainty.NEEDS_CONFIRMATION,
                "still has an unresolved item",
            ),
            (
                ValidationFindingCode.INVALID_SPEAKER,
                bool(cue.speech.strip()) and not labels,
                "has blank or unparseable speaker",
            ),
        )
        findings.extend(
            ValidationFinding(code=code, cue_ids=cue_ids, issue=f"{prefix} {issue}")
            for code, present, issue in checks
            if present
        )
        duplicate = next(
            (
                previous
                for previous in transcript.cues[:index]
                if previous.speech == cue.speech
                and previous.speaker == cue.speaker
                and cue.start_tc_ms < previous.end_tc_ms
                and previous.start_tc_ms < cue.end_tc_ms
            ),
            None,
        )
        if duplicate is not None:
            duplicate_ids = tuple(
                cue_id
                for cue_id in (duplicate.cue_id, cue.cue_id)
                if cue_id is not None
            )
            issue = (
                f"{prefix} duplicates the preceding cue"
                if duplicate is transcript.cues[index - 1]
                else f"{prefix} duplicates an earlier cue"
            )
            findings.append(
                ValidationFinding(
                    code=ValidationFindingCode.SAME_SPEAKER_DUPLICATE,
                    cue_ids=duplicate_ids,
                    issue=issue,
                )
            )
    return tuple(findings)


__all__ = ["validation_findings"]
