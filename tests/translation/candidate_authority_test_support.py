# Copyright 2026 Transcription Module contributors

"""Typed access to legacy candidate fixtures for authority scenarios."""

from typing import TYPE_CHECKING, cast

from tests.translation import test_candidate as legacy_candidate_tests

if TYPE_CHECKING:
    from collections.abc import Callable

    from translation_module.candidate import ChunkCandidate
    from translation_module.document import TranslationDocument
    from translation_module.preparation import ChunkPlan
    from translation_module.source import SourceSnapshot

candidate_source_fixture = cast(
    "Callable[[], SourceSnapshot]", vars(legacy_candidate_tests)["_source"]
)
candidate_plans_fixture = cast(
    "Callable[[], tuple[ChunkPlan, ...]]", vars(legacy_candidate_tests)["_plans"]
)
chunk_candidates_fixture = cast(
    "Callable[[], tuple[ChunkCandidate, ...]]",
    vars(legacy_candidate_tests)["_candidates"],
)
assembled_candidate_fixture = cast(
    "Callable[[], TranslationDocument]", vars(legacy_candidate_tests)["_assemble"]
)
