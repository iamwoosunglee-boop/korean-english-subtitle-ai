# Copyright 2026 Transcription Module contributors

"""Command-line surface for deterministic transcription stages."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import ValidationError

from transcription_module.asr import AsrBackendError
from transcription_module.boundaries import parse_run_id, parse_youtube_url
from transcription_module.cli_errors import render_error
from transcription_module.confirmation_cli import (
    confirm_command,
    confirm_visual_insertion_command,
    confirmation_consent_command,
    next_confirmation_command,
    start_confirmation_command,
    suggest_confirmation_command,
)
from transcription_module.drafting import (
    PyannoteDiarizer,
    run_optional_diarization,
)
from transcription_module.errors import (
    CliBoundaryError,
    ErrorCode,
)
from transcription_module.evidence_resume import (
    EvidenceResumeError,
    EvidenceResumeSummary,
    validate_resumable_evidence,
)
from transcription_module.evidence_status import inspect_evidence_status
from transcription_module.final_review import (
    FinalReviewError,
    apply_final_review,
    prepare_final_review,
)
from transcription_module.final_review_models import FinalReviewResult
from transcription_module.media import BrowserCookieSource, MediaToolError
from transcription_module.models import EpisodeContext
from transcription_module.protected import (
    ProtectedExecution,
    SourceChangedError,
    run_protected,
)
from transcription_module.recovery_stage import detect_and_queue_vad_recovery
from transcription_module.source_edit_cli import (
    finalize_source_edit_command,
    prepare_run_translation_assets_command,
    start_source_edit_command,
)
from transcription_module.source_edit_errors import SourceEditLifecycleError
from transcription_module.stages import (
    apply_vision_run,
    prepare_media_run,
    transcribe_media_run,
)
from transcription_module.storage import RunStore
from transcription_module.timing import TimingContractError
from transcription_module.translation_export import (
    TranslationExportError,
)
from transcription_module.translation_export import (
    export_translation_source as export_source_snapshot,
)
from transcription_module.translation_export_ass import (
    export_ass_translation_source as export_ass_source_snapshot,
)
from transcription_module.validation_cli import validate_command
from transcription_module.vision import VisionContractError
from transcription_module.workflow import (
    apply_context_to_run,
    process_fixture_after_context,
    start_run,
)
from translation_module.source import SourceMaturity

app = typer.Typer(
    add_completion=False,
    help="Create and inspect agent-driven video transcription runs.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
_ = app.command("confirm")(confirm_command)
_ = app.command("confirm-visual-insertion")(confirm_visual_insertion_command)
_ = app.command("confirmation-consent")(confirmation_consent_command)
_ = app.command("next-confirmation")(next_confirmation_command)
_ = app.command("start-confirmation")(start_confirmation_command)
_ = app.command("suggest")(suggest_confirmation_command)
_ = app.command("validate")(validate_command)
_ = app.command("finalize-source-edit")(finalize_source_edit_command)
_ = app.command("prepare-run-translation-assets")(
    prepare_run_translation_assets_command
)
_ = app.command("start-source-edit")(start_source_edit_command)
DEFAULT_RUNS_DIR: Final = Path("runs")
INVALID_CONTEXT_MESSAGE: Final = (
    "episode context must be supplied exactly once and conform to the "
    "episode context schema"
)

RunsDirOption = Annotated[
    Path,
    typer.Option(
        "--runs-dir",
        file_okay=False,
        help="Directory that owns isolated run artifacts.",
    ),
]


def _resolve_episode_context(
    text: str | None,
    context_file: Path | None,
) -> EpisodeContext:
    if (text is None) == (context_file is None):
        raise CliBoundaryError(
            ErrorCode.INVALID_EPISODE_CONTEXT,
            INVALID_CONTEXT_MESSAGE,
        )
    try:
        if text is not None:
            return EpisodeContext(raw_text=text)
        if context_file is None:
            raise CliBoundaryError(
                ErrorCode.INVALID_EPISODE_CONTEXT,
                INVALID_CONTEXT_MESSAGE,
            )
        return EpisodeContext.model_validate_json(
            context_file.read_bytes(),
            strict=True,
        )
    except (OSError, ValidationError) as error:
        raise CliBoundaryError(
            ErrorCode.INVALID_EPISODE_CONTEXT,
            INVALID_CONTEXT_MESSAGE,
        ) from error


@app.command()
def start(source_url: str, runs_dir: RunsDirOption = DEFAULT_RUNS_DIR) -> None:
    """Create a run and wait for explicit episode context."""
    try:
        parsed_url = parse_youtube_url(source_url)
    except CliBoundaryError as error:
        render_error(error)
    result = start_run(parsed_url, RunStore(runs_dir))
    typer.echo(result.model_dump_json())


@app.command("context")
def apply_context(
    run_id: str,
    text: Annotated[
        str | None,
        typer.Option("--text", help="Free-form episode context."),
    ] = None,
    context_file: Annotated[
        Path | None,
        typer.Option(
            "--context-file",
            help="Strict typed episode-context JSON.",
        ),
    ] = None,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Persist episode context and run the configured deterministic adapter."""
    try:
        parsed_run_id = parse_run_id(run_id)
        context = _resolve_episode_context(text, context_file)
    except CliBoundaryError as error:
        render_error(error)
    fixture_value = os.environ.get("TRANSCRIPTION_FIXTURE_DIR")
    if fixture_value is None:
        result = apply_context_to_run(
            parsed_run_id,
            context,
            RunStore(runs_dir),
        )
    else:
        result = process_fixture_after_context(
            parsed_run_id,
            context,
            Path(fixture_value),
            RunStore(runs_dir),
        )
    typer.echo(result.model_dump_json())


@app.command()
def prepare(
    source_url: str,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Reuse an existing contextual run.",
        ),
    ] = None,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Download source media and record its real timing manifest."""
    try:
        parsed_url = parse_youtube_url(source_url)
        store = RunStore(runs_dir)
        parsed_run_id = parse_run_id(run_id) if run_id is not None else None
        manifest = prepare_media_run(
            parsed_url,
            store,
            parsed_run_id,
        )
    except CliBoundaryError as error:
        render_error(error)
    except MediaToolError as error:
        render_error(
            CliBoundaryError(
                ErrorCode.MEDIA_PREPARATION_FAILED,
                str(error),
            )
        )
    typer.echo(manifest.model_dump_json())


@app.command()
def transcribe(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Transcribe prepared audio with the pinned local mlx-whisper model."""
    try:
        parsed_run_id = parse_run_id(run_id)
        artifact = transcribe_media_run(parsed_run_id, RunStore(runs_dir))
    except CliBoundaryError as error:
        render_error(error)
    except (OSError, AsrBackendError) as error:
        render_error(CliBoundaryError(ErrorCode.ASR_FAILED, str(error)))
    typer.echo(artifact.model_dump_json())


@app.command()
def recover(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Detect VAD gaps and queue uncovered speech for confirmation."""
    try:
        parsed_run_id = parse_run_id(run_id)
        artifact = detect_and_queue_vad_recovery(
            RunStore(runs_dir),
            parsed_run_id,
        )
    except CliBoundaryError as error:
        render_error(error)
    except (MediaToolError, OSError, ValidationError) as error:
        render_error(
            CliBoundaryError(
                ErrorCode.VAD_RECOVERY_FAILED,
                str(error),
            )
        )
    typer.echo(artifact.model_dump_json())


@app.command("apply-vision")
def apply_vision(
    run_id: str,
    decisions: Annotated[
        Path,
        typer.Option(
            "--decisions",
            exists=True,
            dir_okay=False,
            help="Agent-supplied manifest-backed vision decisions.",
        ),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Validate and apply agent-supplied full-timeline vision evidence."""
    try:
        parsed_run_id = parse_run_id(run_id)
        artifact = apply_vision_run(
            parsed_run_id,
            decisions,
            RunStore(runs_dir),
        )
    except CliBoundaryError as error:
        render_error(error)
    except (
        OSError,
        TimingContractError,
        ValidationError,
        VisionContractError,
    ) as error:
        render_error(CliBoundaryError(ErrorCode.VISION_FAILED, str(error)))
    typer.echo(artifact.model_dump_json())


@app.command("prepare-final-review")
def prepare_final_review_command(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Persist the deterministic final-review assignment for a run."""
    try:
        parsed_run_id = parse_run_id(run_id)
        store = RunStore(runs_dir)
        _ = prepare_final_review(store, parsed_run_id)
        path = store.paths(parsed_run_id).final_review_assignment
    except CliBoundaryError as error:
        render_error(error)
    except (FinalReviewError, OSError, ValidationError) as error:
        render_error(CliBoundaryError(ErrorCode.VALIDATION_FAILED, str(error)))
    typer.echo(json.dumps({"assignment_path": str(path)}))


@app.command("apply-final-review")
def apply_final_review_command(
    run_id: str,
    result: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Typed FinalReviewResult JSON produced by the reviewer.",
        ),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Validate and apply a typed final-review result JSON file."""
    try:
        parsed_run_id = parse_run_id(run_id)
        typed_result = FinalReviewResult.model_validate_json(
            result.read_text(encoding="utf-8")
        )
        store = RunStore(runs_dir)
        receipt = apply_final_review(store, parsed_run_id, typed_result)
        artifact_path = store.paths(parsed_run_id).final_review_artifact
    except CliBoundaryError as error:
        render_error(error)
    except (FinalReviewError, OSError, ValidationError, ValueError) as error:
        render_error(CliBoundaryError(ErrorCode.VALIDATION_FAILED, str(error)))
    typer.echo(
        json.dumps(
            {
                "artifact_path": str(artifact_path),
                "status": "applied",
                "receipt": receipt.model_dump(mode="json"),
            }
        )
    )


@app.command("export-translation-source")
def export_translation_source(
    run_id: str,
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination source snapshot JSON."),
    ],
    source_maturity: Annotated[
        SourceMaturity,
        typer.Option(
            "--source-maturity",
            help="Downstream translation source maturity.",
        ),
    ] = SourceMaturity.FINALIZED,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Export a finalized current run for standalone translation."""
    try:
        parsed_run_id = parse_run_id(run_id)
        snapshot = export_source_snapshot(
            RunStore(runs_dir),
            parsed_run_id,
            output,
            source_maturity,
        )
    except CliBoundaryError as error:
        render_error(error)
    except SourceEditLifecycleError as error:
        render_error(CliBoundaryError(ErrorCode.SOURCE_EDIT_FAILED, str(error)))
    except TranslationExportError as error:
        render_error(CliBoundaryError(ErrorCode.VALIDATION_FAILED, str(error)))
    typer.echo(snapshot.model_dump_json())


@app.command("export-ass-translation-source")
def export_ass_translation_source(
    run_id: str,
    expected_ass_sha256: Annotated[
        str,
        typer.Option(
            "--expected-ass-sha256",
            help="Required SHA-256 of the run-owned transcript.ass.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination source snapshot JSON."),
    ],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Export authoritative ASS through the finalized translation-source adapter."""
    try:
        parsed_run_id = parse_run_id(run_id)
        snapshot = export_ass_source_snapshot(
            RunStore(runs_dir),
            parsed_run_id,
            output,
            expected_ass_sha256,
        )
    except CliBoundaryError as error:
        render_error(error)
    except SourceEditLifecycleError as error:
        render_error(CliBoundaryError(ErrorCode.SOURCE_EDIT_FAILED, str(error)))
    except TranslationExportError as error:
        render_error(CliBoundaryError(ErrorCode.VALIDATION_FAILED, str(error)))
    typer.echo(snapshot.model_dump_json())


@app.command("protected-run")
def protected_run(
    run_id: str,
    context: Annotated[
        str,
        typer.Option("--context", help="Free-form episode context or 없음."),
    ],
    fixture_dir: Annotated[
        Path | None,
        typer.Option(
            "--fixture-dir",
            exists=True,
            file_okay=False,
            help="Deterministic fixture; omit for explicit real-media stages.",
        ),
    ] = None,
    youtube_browser: Annotated[
        BrowserCookieSource,
        typer.Option(
            "--youtube-browser",
            help="Logged-in browser session read directly by yt-dlp.",
        ),
    ] = BrowserCookieSource.CHROME,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Run fixture or real evidence stages with source writes blocked."""
    try:
        parsed_run_id = parse_run_id(run_id)
        store = RunStore(runs_dir)
        state = store.load_state(parsed_run_id)

        def execute(execution: ProtectedExecution) -> None:
            del execution
            if fixture_dir is not None:
                _ = process_fixture_after_context(
                    parsed_run_id,
                    EpisodeContext(raw_text=context),
                    fixture_dir,
                    store,
                )
                return
            _ = apply_context_to_run(
                parsed_run_id,
                EpisodeContext(raw_text=context),
                store,
            )
            _ = prepare_media_run(
                state.source_url,
                store,
                existing_run_id=parsed_run_id,
                youtube_browser=youtube_browser,
            )
            _ = transcribe_media_run(parsed_run_id, store)
            _ = run_optional_diarization(
                store,
                parsed_run_id,
                PyannoteDiarizer(token=os.environ.get("HF_TOKEN")),
            )

        artifact = run_protected(
            Path(__file__).resolve().parents[2],
            store.paths(parsed_run_id).run_dir,
            execute,
        )
    except CliBoundaryError as error:
        render_error(error)
    except (
        AsrBackendError,
        MediaToolError,
        OSError,
        SourceChangedError,
        ValidationError,
    ) as error:
        render_error(CliBoundaryError(ErrorCode.PROTECTED_RUN_FAILED, str(error)))
    typer.echo(artifact.model_dump_json())


@app.command("evidence-status")
def evidence_status(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Report policy evidence reuse, absence, staleness, and lifecycle artifacts."""
    try:
        parsed_run_id = parse_run_id(run_id)
        report = inspect_evidence_status(RunStore(runs_dir), parsed_run_id)
    except CliBoundaryError as error:
        render_error(error)
    except FileNotFoundError:
        render_error(
            CliBoundaryError(ErrorCode.RUN_NOT_FOUND, "transcription run not found")
        )
    except (OSError, ValidationError) as error:
        render_error(CliBoundaryError(ErrorCode.PROTECTED_RUN_FAILED, str(error)))
    typer.echo(report.model_dump_json())


@app.command("resume-evidence")
def resume_evidence(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Validate reusable evidence from a source-change protected-run failure."""
    try:
        parsed_run_id = parse_run_id(run_id)
        store = RunStore(runs_dir)
        summaries: list[EvidenceResumeSummary] = []

        def execute(execution: ProtectedExecution) -> None:
            del execution
            summaries.append(validate_resumable_evidence(parsed_run_id, store))

        _ = run_protected(
            Path(__file__).resolve().parents[2],
            store.paths(parsed_run_id).run_dir,
            execute,
        )
    except (
        EvidenceResumeError,
        OSError,
        SourceChangedError,
        ValidationError,
    ) as error:
        render_error(CliBoundaryError(ErrorCode.PROTECTED_RUN_FAILED, str(error)))
    typer.echo(summaries[0].model_dump_json())


if __name__ == "__main__":
    app()
