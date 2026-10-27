# Copyright 2026 Transcription Module contributors

"""Typer commands for one-at-a-time resumable confirmation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import typer

from transcription_module.boundaries import parse_run_id
from transcription_module.cli_errors import render_error
from transcription_module.confirmation import (
    apply_confirmation_answer,
    apply_visual_insertion_confirmation,
    create_confirmation_session,
    get_next_confirmation,
    record_confirmation_suggestion,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import load_confirmation_history
from transcription_module.confirmation_models import (
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationId,
    ConfirmationQueueStatus,
    VisualInsertionAction,
)
from transcription_module.errors import CliBoundaryError, ErrorCode
from transcription_module.storage import RunStore

if TYPE_CHECKING:
    from transcription_module.confirmation_models import ConfirmationSession
    from transcription_module.models import RunId

RunsDirOption = Annotated[
    Path,
    typer.Option(
        "--runs-dir",
        file_okay=False,
        help="Directory that owns isolated run artifacts.",
    ),
]
DEFAULT_RUNS_DIR: Final = Path("runs")


def confirm_command(
    run_id: str,
    confirmation_id: str,
    answer: Annotated[str, typer.Option("--answer", help="Answer current item.")],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Apply the next pending answer and resume or finalize the run."""
    try:
        parsed_run_id = parse_run_id(run_id)
        result = apply_confirmation_answer(
            RunStore(runs_dir),
            parsed_run_id,
            ConfirmationId(confirmation_id),
            answer,
        )
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(
                ErrorCode(error.code.value),
                error.message,
            )
        )
    typer.echo(result.model_dump_json())


def confirm_visual_insertion_command(
    run_id: str,
    confirmation_id: str,
    accepted: Annotated[
        bool,
        typer.Option(
            "--accept/--reject",
            help="Accept or reject the current provisional visual insertion.",
        ),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Apply an explicit typed insertion decision."""
    try:
        parsed_run_id = parse_run_id(run_id)
        result = apply_visual_insertion_confirmation(
            RunStore(runs_dir),
            parsed_run_id,
            ConfirmationId(confirmation_id),
            (
                VisualInsertionAction.ACCEPT
                if accepted
                else VisualInsertionAction.REJECT
            ),
        )
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(
                ErrorCode(error.code.value),
                error.message,
            )
        )
    typer.echo(result.model_dump_json())


def next_confirmation_command(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Show only the next unresolved confirmation item."""
    try:
        parsed_run_id = parse_run_id(run_id)
        store = RunStore(runs_dir)
        item = get_next_confirmation(store, parsed_run_id)
        history = load_confirmation_history(
            store.paths(parsed_run_id).confirmations
        )
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(
                ErrorCode(error.code.value),
                error.message,
            )
        )
    typer.echo(
        ConfirmationQueueStatus(
            pending=True,
            item=item,
            current_number=len(history.resolved_ids) + 1,
            total_count=len(history.requests),
            remaining_count=len(history.requests) - len(history.resolved_ids),
        ).model_dump_json()
    )


def confirmation_consent_command(
    run_id: str,
    accepted: Annotated[
        bool,
        typer.Option("--accept/--decline", help="Join this confirmation attempt."),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Accept or decline before any confirmation item is exposed."""
    try:
        parsed_run_id = parse_run_id(run_id)
        session = set_confirmation_consent(
            RunStore(runs_dir),
            parsed_run_id,
            accepted=accepted,
        )
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(ErrorCode(error.code.value), error.message)
        )
    typer.echo(session.model_dump_json())


def start_confirmation_command(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Start a new consent attempt after a declined or completed one."""
    try:
        parsed_run_id = parse_run_id(run_id)
        session = _start_confirmation(RunStore(runs_dir), parsed_run_id)
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(ErrorCode(error.code.value), error.message)
        )
    typer.echo(session.model_dump_json())


def _start_confirmation(
    store: RunStore,
    run_id: RunId,
) -> ConfirmationSession:
    """Refuse a consent attempt before speaker inference has run."""
    if not store.paths(run_id).speaker_inference_artifact.exists():
        raise ConfirmationError(
            ConfirmationErrorCode.SPEAKER_INFERENCE_REQUIRED,
            "speaker inference must run before confirmation",
        )
    return create_confirmation_session(store, run_id)


def suggest_confirmation_command(
    run_id: str,
    confirmation_id: str,
    suggestion: Annotated[
        str,
        typer.Option("--suggestion", help="Non-binding agent suggestion."),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Record a suggestion without resolving the current item."""
    try:
        parsed_run_id = parse_run_id(run_id)
        record_confirmation_suggestion(
            RunStore(runs_dir),
            parsed_run_id,
            ConfirmationId(confirmation_id),
            suggestion,
        )
    except CliBoundaryError as error:
        render_error(error)
    except ConfirmationError as error:
        render_error(
            CliBoundaryError(ErrorCode(error.code.value), error.message)
        )
    typer.echo('{"recorded":"suggestion"}')
