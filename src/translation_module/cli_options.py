# Copyright 2026 Transcription Module contributors

"""Shared Typer annotations for translation bridge commands."""

from pathlib import Path
from typing import Annotated

import typer

from translation_module.canonical.attempts import TranslationAttemptKind

Output = Annotated[Path, typer.Option("--output", help="Destination JSON path.")]
EvidenceOpenRoot = Annotated[
    Path,
    typer.Option(
        "--evidence-open-root",
        help="Directory containing immutable explicit frame-open records.",
    ),
]
EvidenceRoot = Annotated[
    Path,
    typer.Option(
        "--evidence-root", help="Only directory from which file evidence may be read."
    ),
]
SourceCandidate = Annotated[
    tuple[Path, Path], typer.Argument(metavar="SOURCE CANDIDATE")
]
RuntimeSessions = Annotated[
    tuple[str, str, str, str],
    typer.Option(
        "--runtime-session-ids", metavar="TRANSLATOR REVISER ASSEMBLER REVIEWER"
    ),
]

AuthorityRoot = Annotated[
    Path, typer.Option("--authority-root", help="Canonical attempt authority root.")
]
DependencyRoot = Annotated[
    Path | None,
    typer.Option(
        "--dependency-root",
        help="Confined canonical dependency graph root for v2 inputs.",
    ),
]
AttemptIdentity = Annotated[
    tuple[str, TranslationAttemptKind, str],
    typer.Option("--attempt", metavar="ID KIND PARENT_OR_DASH"),
]
Rationales = Annotated[Path, typer.Option("--rationales")]
SelectionIdentity = Annotated[
    tuple[str, str, str],
    typer.Option("--selection", metavar="ID SELECTED_AT SELECTED_BY"),
]

AttemptAuthority = Annotated[
    tuple[Path, str, TranslationAttemptKind, str],
    typer.Option("--attempt-authority", metavar="ROOT ID KIND PARENT_OR_DASH"),
]
CandidateOutput = Annotated[
    tuple[str, Path], typer.Option("--candidate-output", metavar="DOCUMENT_ID PATH")
]
RevisionIo = Annotated[
    tuple[Path, Path], typer.Option("--revision-io", metavar="SOURCE OUTPUT")
]
PublicationAuthority = Annotated[
    tuple[Path, Path, Path],
    typer.Option("--publication-authority", metavar="ATTEMPTS ASSIGNMENT ROOT"),
]

ReviewProfileIdentity = Annotated[
    tuple[str, str], typer.Option("--review-profile", metavar="ID VERSION")
]
