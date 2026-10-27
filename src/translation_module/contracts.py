# Copyright 2026 Transcription Module contributors

"""Stable public facade for translation JSON contracts."""

from translation_module.canonical import (
    ContentContextAsset,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
    TranslationReviewArtifact,
)
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    Participant,
)
from translation_module.document import (
    DocumentStatus,
    RetainedContext,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
)
from translation_module.source import (
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)

__all__ = [
    "ContentContextAsset",
    "ContentKind",
    "DocumentStatus",
    "EpisodeContext",
    "EvidenceEntry",
    "EvidenceKind",
    "EvidenceManifest",
    "Participant",
    "RetainedContext",
    "SourceCue",
    "SourceMaturity",
    "SourceSnapshot",
    "SourceTranscriptV2",
    "TargetCue",
    "TranslationAlternative",
    "TranslationDocument",
    "TranslationDocumentV2",
    "TranslationGlossaryAsset",
    "TranslationReviewArtifact",
    "UnresolvedItem",
    "UnresolvedQuestion",
]
