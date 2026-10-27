# Copyright 2026 Transcription Module contributors

"""Typed historical fixtures for the canonical workflow proof."""

from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from transcription_module.models import Transcript

FIXTURE_PATH: Final = Path("tests/fixtures/canonical-workflow-regressions.v1.json")
EXPECTED_CASES: Final = {
    "unresolved_speech",
    "empty_speaker",
    "incomplete_confirmation_closure",
    "distinct_speaker_simultaneous_repetition",
    "stale_confirmation_after_source_edit",
    "unapproved_review_non_mutation",
}


class FixtureCase(BaseModel):
    """One permissive historical case payload."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    transcript: Transcript | None = None


class RegressionFixture(BaseModel):
    """The exact fixed historical case collection."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    cases: dict[str, FixtureCase]
