# Copyright 2026 Transcription Module contributors

"""Production composition for preserved legacy review scenarios."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from translation_module.cli_io import write_models_atomic
from translation_module.document import TranslationDocument
from translation_module.preparation_errors import PreparationError
from translation_module.review_preparation import (
    ReviewRequest,
    ReviewScope,
    build_review_request,
    review_assignment_from_request,
)
from translation_module.review_state import ReviewAttempts
from translation_module.source import SourceSnapshot
from translation_module.workflow import ReviewAssignment

CLI_ERROR_EXIT: Final = 2


@dataclass(frozen=True, slots=True)
class ReviewScenario:
    """Exact legacy review inputs exercised through current domain functions."""

    source: SourceSnapshot
    candidate: TranslationDocument
    scope_json: bytes
    session_ids: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class ReviewScenarioOutcome:
    """Observable boundary outcome produced by real preparation behavior."""

    returncode: int
    stderr: str


def persist_review_scenario(
    request: ReviewRequest,
    outputs: tuple[Path, Path],
    attempts: ReviewAttempts | None = None,
) -> ReviewAssignment:
    """Bind and atomically persist production review request artifacts."""
    assignment = review_assignment_from_request(request, attempts=attempts)
    write_models_atomic(((outputs[0], request), (outputs[1], assignment)))
    return assignment


def run_review_scenario(
    scenario: ReviewScenario,
    outputs: tuple[Path, Path],
    attempts: ReviewAttempts | None = None,
) -> ReviewScenarioOutcome:
    """Run the preserved boundary against production parsing and persistence."""
    try:
        scope = ReviewScope.model_validate_json(scenario.scope_json)
        request = build_review_request(
            scenario.source,
            scenario.candidate,
            scope,
            scenario.session_ids,
        )
        _ = persist_review_scenario(request, outputs, attempts)
    except (PreparationError, ValidationError) as error:
        return ReviewScenarioOutcome(
            returncode=CLI_ERROR_EXIT,
            stderr=json.dumps(
                {
                    "error": {
                        "code": "review_preparation_failed",
                        "message": str(error),
                    }
                }
            ),
        )
    return ReviewScenarioOutcome(returncode=0, stderr="")
