# Copyright 2026 Transcription Module contributors

"""Deterministic transcription-to-canonical-v2 handoff adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module.canonical_source_context import (
    load_visual_context,
    visual_context_facts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.speaker_notation import (
    SpeakerNotationError,
    parse_speaker_labels,
)
from transcription_module.translation_export import referenced_frames
from transcription_module.translation_export_evidence import (
    TranslationExportError,
    evidence_entries,
)
from translation_module.canonical.common import (
    AssetLifecycleStatus,
    AssetReference,
    CanonicalEvidenceEntry,
    CanonicalEvidenceKind,
    CanonicalEvidenceManifest,
    DocumentLifecycleStatus,
)
from translation_module.canonical.context import (
    ContentContextAsset,
    ContextParticipant,
    ContextStage,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.source import SourceCueV2, SourceTranscriptV2
from translation_module.canonical.validation import validate_canonical_save
from translation_module.common import ContentKind

if TYPE_CHECKING:
    from transcription_module.storage import RunStore
    from translation_module.source import EvidenceEntry


class CanonicalSourceExportError(Exception):
    """A finalized transcription cannot become a canonical source pair."""


def _participants(context: EpisodeContext) -> tuple[ContextParticipant, ...]:
    if context.roster:
        return tuple(
            ContextParticipant(
                participant_id=person.person_id,
                name_ko=person.display_name,
                aliases=(person.display_name,),
                evidence_ids=(),
            )
            for person in context.roster
        )
    names = tuple(
        dict.fromkeys(item.strip() for item in context.characters if item.strip())
    )
    return tuple(
        ContextParticipant(
            participant_id=name,
            name_ko=name,
            aliases=(name,),
            evidence_ids=(),
        )
        for name in names
    )


def _validate_inputs(
    store: RunStore,
    run_id: RunId,
    state: RunState,
    transcript: Transcript,
) -> None:
    if state.run_id != run_id or transcript.run_id != run_id:
        message = "run IDs do not match"
        raise CanonicalSourceExportError(message)
    if any(
        item.workflow_status is not WorkflowStatus.FINALIZED
        or item.validation_status is not ValidationStatus.VALID
        for item in (state, transcript)
    ):
        message = "state and transcript must be finalized and valid"
        raise CanonicalSourceExportError(message)
    if state.context is None:
        message = "episode context is required"
        raise CanonicalSourceExportError(message)
    cue_ids = tuple(cue.cue_id for cue in transcript.cues)
    if any(cue_id is None for cue_id in cue_ids) or len(cue_ids) != len(set(cue_ids)):
        message = "cue IDs must be non-null and unique"
        raise CanonicalSourceExportError(message)
    if any(cue.uncertainty is not Uncertainty.CERTAIN for cue in transcript.cues):
        message = "all cues must be certain"
        raise CanonicalSourceExportError(message)
    media = PreparedMediaManifest.model_validate_json(
        store.paths(run_id).media_manifest.read_text(encoding="utf-8")
    )
    if (
        media.run_id != run_id
        or media.source_url != state.source_url
        or media.duration_ms != transcript.duration_ms
        or media.frame_count != len(media.frames)
    ):
        message = "current media manifest does not match the supplied run"
        raise CanonicalSourceExportError(message)


def _required_context(state: RunState) -> EpisodeContext:
    context = state.context
    if context is None:
        message = "episode context is required"
        raise CanonicalSourceExportError(message)
    return context


def _verify_context_reference(
    context: ContentContextAsset,
    source: SourceTranscriptV2,
) -> None:
    if (
        source.input_context_ref.asset_id != context.asset_id
        or source.input_context_ref.revision_id != context.revision_id
        or source.input_context_ref.content_digest != context.content_digest
    ):
        message = "source context reference is inconsistent"
        raise CanonicalSourceExportError(message)


def _verify_evidence_currency(
    original: tuple[EvidenceEntry, ...],
    current: tuple[EvidenceEntry, ...],
) -> None:
    if current != original:
        message = "evidence resources changed during canonical source construction"
        raise CanonicalSourceExportError(message)


def build_canonical_source_pair(
    store: RunStore,
    run_id: RunId,
    *,
    state: RunState,
    transcript: Transcript,
) -> tuple[ContentContextAsset, SourceTranscriptV2]:
    """Build one verified canonical input-context and source draft pair."""
    try:
        _validate_inputs(store, run_id, state, transcript)
        context_value = _required_context(state)
        visual_context = load_visual_context(store, state)
        frame_ids_by_cue, included_frame_ids = referenced_frames(transcript)
        if visual_context is not None:
            included_frame_ids.extend(
                str(frame.frame_id)
                for assignment in visual_context.assignments
                for frame in assignment.frames
            )
        legacy_entries, _ = evidence_entries(
            store,
            run_id,
            int(transcript.duration_ms),
            included_frame_ids,
            ((), ()),
        )
        canonical_entries = tuple(
            CanonicalEvidenceEntry(
                evidence_id=entry.evidence_id,
                kind=CanonicalEvidenceKind.FRAME,
                media_type=entry.media_type,
                resource_digest=entry.resource_digest,
                locator=entry.locator,
                pts_ms=entry.pts_ms,
            )
            for entry in legacy_entries
        )
        facts = visual_context_facts(visual_context, legacy_entries)
        context_evidence_ids = {
            evidence_id for fact in facts for evidence_id in fact.evidence_ids
        }
        context = bind_canonical_digests(
            ContentContextAsset(
                asset_id=f"{run_id}:context",
                revision_id=f"{run_id}:context:input:transcription",
                content_digest="0" * 64,
                record_digest="0" * 64,
                stage=ContextStage.INPUT,
                lifecycle_status=AssetLifecycleStatus.READY,
                program="미상",
                episode_summary=context_value.raw_text,
                evidence=CanonicalEvidenceManifest(
                    base_uri="./",
                    entries=tuple(
                        entry
                        for entry in canonical_entries
                        if entry.evidence_id in context_evidence_ids
                    ),
                ),
                participants=_participants(context_value),
                facts=facts,
            )
        )
        source = bind_canonical_digests(
            SourceTranscriptV2(
                document_id=str(run_id),
                revision_id=f"{run_id}:source:transcription-draft",
                content_digest="0" * 64,
                record_digest="0" * 64,
                lifecycle_status=DocumentLifecycleStatus.DRAFT,
                source_locale="ko-KR",
                duration_ms=transcript.duration_ms,
                input_context_ref=AssetReference(
                    asset_id=context.asset_id,
                    revision_id=context.revision_id,
                    content_digest=context.content_digest,
                    path="context.input.json",
                ),
                evidence=CanonicalEvidenceManifest(
                    base_uri="./",
                    entries=canonical_entries,
                ),
                cues=tuple(
                    SourceCueV2.model_validate(
                        {
                            "sequence": cue.sequence,
                            "cue_id": str(cue.cue_id),
                            "start_ms": cue.start_tc_ms,
                            "end_ms": cue.end_tc_ms,
                            "text": cue.speech,
                            "speakers": parse_speaker_labels(cue.speaker),
                            "content_kind": ContentKind.UNKNOWN,
                            "evidence_ids": frame_ids,
                            **(
                                {"screen_text": cue.screen_text}
                                if cue.screen_text is not None
                                else {}
                            ),
                        }
                    )
                    for cue, frame_ids in zip(
                        transcript.cues,
                        frame_ids_by_cue,
                        strict=True,
                    )
                ),
            )
        )
        validate_canonical_save(context)
        validate_canonical_save(source)
        _verify_context_reference(context, source)
        current_entries, _ = evidence_entries(
            store,
            run_id,
            int(transcript.duration_ms),
            included_frame_ids,
            ((), ()),
        )
        _verify_evidence_currency(legacy_entries, current_entries)
    except CanonicalSourceExportError:
        raise
    except (
        OSError,
        SpeakerNotationError,
        TranslationExportError,
        ValidationError,
        ValueError,
    ) as error:
        raise CanonicalSourceExportError(str(error)) from error
    return context, source
