# Copyright 2026 Transcription Module contributors

"""Action-aware lexical ownership and deterministic correction replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from transcription_module.correction_provenance import validate_correction_receipt
from transcription_module.provenance_errors import CueProvenanceError
from transcription_module.visual_correction import (
    CandidateOrigin,
    VisualCorrectionRequest,
)
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_authority_sources import (
    is_grouped_asr_claim,
    render_asr_source,
)
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module.window_authority_allocation import AllocatedCue
    from transcription_module.window_authority_sources import WindowAuthority


@dataclass(frozen=True, slots=True)
class _LexicalClaim:
    action: CorrectionAction
    origin: CandidateOrigin | None
    subtitle_gap: bool
    before: str
    asr_source: str | None
    asr_texts: tuple[str, ...]
    subtitle_texts: tuple[str, ...]
    candidate: str | None
    observed: str | None


def validate_allocated_replay(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    """Bind all claimed lexical text, action inputs, and receipt result exactly."""
    cue = allocated.cue
    record = allocated.record
    receipt = allocated.receipt
    observation = allocated.observation
    if receipt.action is not record.requested_action:
        fail_window_evidence("window correction action differs from cue coverage")
    if (
        receipt.result_text != cue.speech
        or receipt.result_digest != record.speech_digest
    ):
        fail_window_evidence("window correction result differs from cue speech")
    before = receipt.before_text
    result = receipt.result_text
    if before is None or result is None:
        fail_window_evidence("policy-bound window receipt lacks exact text")
    _validate_lexical_action(
        _LexicalClaim(
            action=record.requested_action,
            origin=record.candidate_origin,
            subtitle_gap=(
                record.requested_action is CorrectionAction.KEEP
                and record.candidate_origin is CandidateOrigin.KO_ORIG
                and not record.asr_ordinals
                and len(record.subtitle_cue_ids) == 1
                and len(record.vad_interval_ids) == 1
                and len(record.turn_ids) == 1
                and observation.complete_text is None
                and receipt.candidate_text is None
            ),
            before=before,
            asr_source=(
                render_asr_source(authority, record.asr_ordinals)
                if record.asr_ordinals
                and (
                    record.requested_action is CorrectionAction.KEEP
                    or is_grouped_asr_claim(
                        authority,
                        record.requested_action,
                        record.asr_ordinals,
                    )
                )
                else None
            ),
            asr_texts=tuple(authority.asr[item].speech for item in record.asr_ordinals),
            subtitle_texts=tuple(
                authority.subtitles[item].text for item in record.subtitle_cue_ids
            ),
            candidate=receipt.candidate_text,
            observed=observation.complete_text,
        )
    )
    policy_sha = authority.window_input.policy_digest
    source_sha = authority.window_input.source_digest
    if policy_sha is None or source_sha is None:
        fail_window_evidence("window correction lacks current digest authority")
    request = VisualCorrectionRequest(
        policy_digest=policy_sha,
        requested_action=record.requested_action,
        observation=observation,
        candidate_origin=record.candidate_origin,
        before_text=before,
        submitted_result=result,
        source_slice=receipt.source_slice,
        candidate_slice=receipt.candidate_slice,
        speaker=cue.speaker,
        confirmed_field=record.confirmed_field,
    )
    try:
        validate_correction_receipt(receipt, source_sha, request)
    except CueProvenanceError:
        fail_window_evidence("window correction receipt replay failed")


def _validate_lexical_action(claim: _LexicalClaim) -> None:
    match claim.action:
        case CorrectionAction.KEEP:
            _validate_keep_lexical(claim)
        case CorrectionAction.HOLD_FOR_CONFIRMATION:
            _validate_hold_lexical(claim)
        case CorrectionAction.REPLACE_SPAN:
            _validate_replacement_lexical(claim)
        case CorrectionAction.INSERT_CUE:
            _validate_insertion_lexical(claim)
        case _:
            assert_never(claim.action)


def _validate_keep_lexical(claim: _LexicalClaim) -> None:
    sources = (
        *((claim.asr_source,) if claim.asr_source is not None else ()),
        *claim.subtitle_texts,
    )
    _validate_preserved_sources(claim, sources)


def _validate_hold_lexical(claim: _LexicalClaim) -> None:
    sources = (
        *((claim.asr_source,) if claim.asr_source is not None else claim.asr_texts),
        *claim.subtitle_texts,
    )
    _validate_preserved_sources(claim, sources)


def _validate_preserved_sources(
    claim: _LexicalClaim,
    sources: tuple[str, ...],
) -> None:
    if not sources or any(item != claim.before for item in sources):
        fail_window_evidence(
            "window correction before text is not referenced source text"
        )
    if claim.origin is not None and not claim.subtitle_gap:
        fail_window_evidence("window preserving action has a candidate origin")


def _validate_replacement_lexical(claim: _LexicalClaim) -> None:
    asr_sources = (
        (claim.asr_source,) if claim.asr_source is not None else claim.asr_texts
    )
    if not asr_sources or any(item != claim.before for item in asr_sources):
        fail_window_evidence(
            "window replacement ASR text differs from receipt before text"
        )
    if claim.origin not in {
        CandidateOrigin.KO_ORIG,
        CandidateOrigin.VISUAL_ONLY,
    }:
        fail_window_evidence("window replacement lacks admitted candidate origin")
    if claim.observed is None or claim.candidate != claim.observed:
        fail_window_evidence("window replacement differs from admitted candidate")
    if claim.origin is CandidateOrigin.KO_ORIG and (
        not claim.subtitle_texts
        or any(item != claim.observed for item in claim.subtitle_texts)
    ):
        fail_window_evidence(
            "window replacement candidate is not an admitted subtitle cue"
        )
    if claim.origin is CandidateOrigin.VISUAL_ONLY and claim.subtitle_texts:
        fail_window_evidence(
            "visual replacement cannot claim subtitle lexical authority"
        )


def _validate_insertion_lexical(claim: _LexicalClaim) -> None:
    if claim.asr_texts or claim.subtitle_texts or claim.before:
        fail_window_evidence("window insertion imports lexical source text")
    if claim.origin is not CandidateOrigin.VISUAL_ONLY:
        fail_window_evidence("window insertion lacks visual-only origin")
    if claim.observed is None or claim.candidate != claim.observed:
        fail_window_evidence("window insertion differs from visual candidate")


__all__ = ["validate_allocated_replay"]
