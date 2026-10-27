# Copyright 2026 Transcription Module contributors

"""Pure target-to-source lineage validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from translation_module.common import SourceCueId
    from translation_module.document import TargetCue
    from translation_module.source import SourceCue


def repeated_lineage_issues(cues: tuple[TargetCue, ...]) -> tuple[str, ...]:
    """Reject nonadjacent, inconsistent, or overlapping repeated lineage."""
    positions_by_source: dict[SourceCueId, list[int]] = {}
    for position, cue in enumerate(cues):
        for source_id in dict.fromkeys(cue.source_cue_ids):
            positions_by_source.setdefault(source_id, []).append(position)
    invalid: list[SourceCueId] = []
    for source_id, positions in positions_by_source.items():
        if len(positions) == 1:
            continue
        split_cues = tuple(cues[position] for position in positions)
        adjacent = positions == list(range(positions[0], positions[-1] + 1))
        lineage = split_cues[0].source_cue_ids
        same_lineage = all(cue.source_cue_ids == lineage for cue in split_cues)
        non_overlapping = all(
            split_cues[index].end_ms <= split_cues[index + 1].start_ms
            for index in range(len(split_cues) - 1)
        )
        if not (adjacent and same_lineage and non_overlapping):
            invalid.append(source_id)
    return (f"duplicate source output: {tuple(invalid)}",) if invalid else ()


def lineage_issues(
    cue: TargetCue,
    source_cues: tuple[SourceCue, ...],
) -> tuple[str, ...]:
    """Validate timing, speaker, content-kind, and evidence lineage."""
    issues: list[str] = []
    cue_prefix = f"target cue {cue.cue_id}"
    envelope_start = min(item.start_ms for item in source_cues)
    envelope_end = max(item.end_ms for item in source_cues)
    if cue.start_ms < envelope_start or cue.end_ms > envelope_end:
        issues.append(f"{cue_prefix} is outside source envelope")
    if not any(
        cue.start_ms < item.end_ms and item.start_ms < cue.end_ms
        for item in source_cues
    ):
        issues.append(f"{cue_prefix} does not overlap referenced source timing")
    source_speakers = {speaker for item in source_cues for speaker in item.speakers}
    if not set(cue.speakers).issubset(source_speakers):
        issues.append(f"{cue_prefix} has unknown speaker")
    source_kinds = {item.content_kind for item in source_cues}
    if len(source_kinds) > 1:
        issues.append(f"{cue_prefix} has mixed source content kinds")
    elif cue.content_kind not in source_kinds:
        issues.append(f"{cue_prefix} has wrong content kind")
    source_evidence = {
        evidence_id for item in source_cues for evidence_id in item.evidence_ids
    }
    if source_evidence - set(cue.evidence_ids):
        issues.append(f"{cue_prefix} omits source evidence")
    return tuple(issues)
