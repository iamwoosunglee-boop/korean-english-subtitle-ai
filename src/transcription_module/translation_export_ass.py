# Copyright 2026 Transcription Module contributors

"""Export a run-owned authoritative ASS as a finalized translation source."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from transcription_module.ass import AssDialogue, parse_ass_dialogues
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import RunId, ValidationStatus, WorkflowStatus
from transcription_module.source_edit_lifecycle import (
    assert_legacy_source_export_available,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.translation_export_evidence import (
    ContextObservations,
    TranslationExportError,
    evidence_entries,
    load_context_observations,
)
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    EvidenceId,
    Milliseconds,
    Participant,
    SourceCueId,
)
from translation_module.preparation import bind_source_digest
from translation_module.source import (
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}")


def export_ass_translation_source(
    store: RunStore,
    run_id: RunId,
    output: Path,
    expected_ass_sha256: str,
) -> SourceSnapshot:
    """Gate, map, digest-bind, and atomically export authoritative ASS bytes."""
    try:
        with store.lock_run(run_id):
            assert_legacy_source_export_available(store, run_id)
            paths = store.paths(run_id)
            if output.resolve() == paths.transcript_ass.resolve():
                message = "output must not alias transcript.ass"
                raise TranslationExportError(message)
            if _SHA256.fullmatch(expected_ass_sha256) is None:
                message = "expected ASS SHA-256 must be 64 lowercase hex"
                raise TranslationExportError(message)
            payload = paths.transcript_ass.read_bytes()
            actual_sha256 = hashlib.sha256(payload).hexdigest()
            if actual_sha256 != expected_ass_sha256:
                message = "ASS SHA-256 does not match expected digest"
                raise TranslationExportError(message)
            snapshot = _build_snapshot(store, run_id, payload, actual_sha256)
            if paths.transcript_ass.read_bytes() != payload:
                message = "transcript.ass changed during export"
                raise TranslationExportError(message)
            write_model_atomic(output, snapshot)
    except (OSError, ValidationError, ValueError) as error:
        raise TranslationExportError(str(error)) from error
    return snapshot


def _build_snapshot(
    store: RunStore,
    run_id: RunId,
    payload: bytes,
    ass_sha256: str,
) -> SourceSnapshot:
    state = store.load_state(run_id)
    if state.run_id != run_id:
        message = "run state does not match the requested run"
        raise TranslationExportError(message)
    if (
        state.workflow_status is not WorkflowStatus.FINALIZED
        or state.validation_status is not ValidationStatus.VALID
    ):
        message = "run state must be finalized and valid"
        raise TranslationExportError(message)
    if state.context is None:
        message = "episode context is required"
        raise TranslationExportError(message)

    paths = store.paths(run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    if (
        media.run_id != run_id
        or media.source_url != state.source_url
        or media.duration_ms <= 0
        or media.frame_count < 0
        or media.frame_count != len(media.frames)
    ):
        message = "current media manifest does not match the run"
        raise TranslationExportError(message)
    observations = load_context_observations(
        paths.run_dir / "context-observations.json",
        run_id,
        state.source_url,
        media.duration_ms,
    )
    observations = _validated_observations(observations)
    frame_entries, context_frame_ids = evidence_entries(
        store,
        run_id,
        media.duration_ms,
        [],
        (observations.source_frame_ids, observations.source_intervals_ms),
    )
    events = sorted(
        parse_ass_dialogues(payload),
        key=lambda event: (event.start_ms, event.end_ms, event.event_index),
    )
    run_dir = paths.run_dir.resolve()
    return bind_source_digest(
        SourceSnapshot(
            document_id=str(run_id),
            content_digest="0" * 64,
            maturity=SourceMaturity.FINALIZED,
            source_locale="ko-KR",
            duration_ms=cast("Milliseconds", media.duration_ms),
            context=_episode_context(observations, state.context.raw_text),
            context_evidence_ids=tuple(
                cast("EvidenceId", frame_id) for frame_id in context_frame_ids
            ),
            evidence=EvidenceManifest(
                base_uri=f"{run_dir.as_uri()}/",
                entries=(
                    EvidenceEntry(
                        evidence_id=cast(
                            "EvidenceId", cast("object", "source-ass")
                        ),
                        kind=EvidenceKind.TEXT,
                        media_type="text/x-ssa",
                        resource_digest=ass_sha256,
                        locator="transcript.ass",
                    ),
                    *frame_entries,
                ),
            ),
            cues=tuple(
                _source_cue(run_id, event, sequence)
                for sequence, event in enumerate(events, start=1)
            ),
        )
    )


def _validated_observations(
    observations: ContextObservations | None,
) -> ContextObservations:
    if observations is None:
        message = "context observations are required"
        raise TranslationExportError(message)
    semantic_fields = (
        observations.episode,
        observations.structure,
        observations.identity_rule,
    )
    if any(value is None or not value.strip() for value in semantic_fields):
        message = "semantic context observations are required"
        raise TranslationExportError(message)
    names = tuple(person.name for person in observations.people)
    if not names or len(names) != len(set(names)):
        message = "observed people must be non-empty and unique"
        raise TranslationExportError(message)
    return observations


def _episode_context(
    observations: ContextObservations,
    state_context: str,
) -> EpisodeContext:
    episode = cast("str", observations.episode)
    structure = cast("str", observations.structure)
    identity_rule = cast("str", observations.identity_rule)
    lines = [
        f"회차: {episode}",
        f"구성: {structure}",
        f"신원 규칙: {identity_rule}",
        *(
            f"인물: {person.name} | {person.role} | {person.appearance}"
            for person in observations.people
        ),
        f"보충: {state_context}",
    ]
    return EpisodeContext(
        summary="\n".join(lines),
        participants=tuple(
            Participant(participant_id=person.name, name=person.name)
            for person in observations.people
        ),
    )


def _source_cue(run_id: RunId, event: AssDialogue, sequence: int) -> SourceCue:
    return SourceCue(
        sequence=sequence,
        cue_id=SourceCueId(
            f"{run_id}:ass-dialogue:{event.event_index:06d}"
        ),
        start_ms=cast("Milliseconds", event.start_ms),
        end_ms=cast("Milliseconds", event.end_ms),
        text=event.text,
        speakers=_ass_speaker_labels(event.name),
        content_kind=ContentKind.UNKNOWN,
        context_note=event.effect,
    )


def _ass_speaker_labels(name: str | None) -> tuple[str, ...]:
    """Normalize the supported external ASS multi-speaker delimiter."""
    if name is None:
        return ("unknown",)
    labels = tuple(
        dict.fromkeys(
            label.strip()
            for label in name.replace("\uff5c", "|").split("|")
            if label.strip()
        )
    )
    return labels or ("unknown",)
