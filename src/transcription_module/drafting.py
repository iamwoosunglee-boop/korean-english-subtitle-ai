# Copyright 2026 Transcription Module contributors

"""Public facade for bounded window drafting and transcript assembly."""

from transcription_module._drafting_assembly import (
    assemble_whole_transcript,
    confirmation_item_for_unresolved,
    select_cue_for_unresolved,
)
from transcription_module._drafting_diarization import (
    PyannoteDiarizer,
    run_optional_diarization,
)
from transcription_module._drafting_models import (
    DiarizationError,
    DiarizationRunArtifact,
    DiarizationStatus,
    Diarizer,
    DraftCue,
    ProvisionalVisualInsertion,
    SpeakerEvidenceSegment,
    UnresolvedItem,
    UnresolvedReasonCode,
    WholeTranscriptArtifact,
    WholeTranscriptAssembler,
    WholeTranscriptDraft,
    WholeTranscriptInput,
    WindowDraft,
    WindowDraftArtifact,
    WindowDrafter,
    WindowDraftInput,
)
from transcription_module._drafting_windows import (
    create_window_drafts,
    create_window_drafts_from_visual_artifact,
)
from transcription_module.visual_review import (
    CandidateDispositionKind,
    FrameVisualObservation,
    RawCandidateDisposition,
    RawVisualCandidate,
    RawVisualRole,
    VisualContextSpan,
    VisualFrameOwnership,
    VisualReviewerIdentity,
    VisualReviewFrame,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    WindowVisualReviewer,
    WindowVisualReviewResult,
)
from transcription_module.window_evidence_models import (
    VerifiedSpeakerTurnArtifact,
    WindowCueEvidence,
    WindowCueResolution,
    WindowSubtitleCue,
    WindowVadInterval,
    WindowVerifiedTurn,
)
from transcription_module.window_speaker_authority import (
    build_verified_speaker_turn_artifact,
)

__all__ = [
    "CandidateDispositionKind",
    "DiarizationError",
    "DiarizationRunArtifact",
    "DiarizationStatus",
    "Diarizer",
    "DraftCue",
    "FrameVisualObservation",
    "ProvisionalVisualInsertion",
    "PyannoteDiarizer",
    "RawCandidateDisposition",
    "RawVisualCandidate",
    "RawVisualRole",
    "SpeakerEvidenceSegment",
    "UnresolvedItem",
    "UnresolvedReasonCode",
    "VerifiedSpeakerTurnArtifact",
    "VisualContextSpan",
    "VisualFrameOwnership",
    "VisualReviewFrame",
    "VisualReviewerIdentity",
    "WholeTranscriptArtifact",
    "WholeTranscriptAssembler",
    "WholeTranscriptDraft",
    "WholeTranscriptInput",
    "WindowCueEvidence",
    "WindowCueResolution",
    "WindowDraft",
    "WindowDraftArtifact",
    "WindowDraftInput",
    "WindowDrafter",
    "WindowSubtitleCue",
    "WindowVadInterval",
    "WindowVerifiedTurn",
    "WindowVisualReviewArtifact",
    "WindowVisualReviewAssignment",
    "WindowVisualReviewResult",
    "WindowVisualReviewer",
    "assemble_whole_transcript",
    "build_verified_speaker_turn_artifact",
    "confirmation_item_for_unresolved",
    "create_window_drafts",
    "create_window_drafts_from_visual_artifact",
    "run_optional_diarization",
    "select_cue_for_unresolved",
]
