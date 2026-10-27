# Copyright 2026 Transcription Module contributors

"""Exercise semantic final review against a copied stored-evidence run."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, cast

import anyio

from transcription_module.final_review_execution import (
    FinalReviewExecutionError,
    FinalReviewSegmentInput,
    FinalReviewSegmentReviewer,
    execute_final_review,
)
from transcription_module.final_review_models import (
    FinalReviewReceipt,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import RunId
from transcription_module.storage import RunStore, write_model_atomic

if TYPE_CHECKING:
    from collections.abc import Sequence

_VISIBLE_TEXT_BY_FRAME = {
    "f001897": ("불러볼 수 있으면 제가...", "토크라운지"),
    "f004108": ("토크라운지",),
}


class _Arguments(NamedTuple):
    """Typed command-line arguments."""

    scenario: Literal["happy", "omitted"]
    run_id: str
    runs_dir: Path
    evidence_dir: Path


def main(argv: Sequence[str] | None = None) -> int:
    """Run one copied-run semantic-review QA scenario."""
    parser = argparse.ArgumentParser(
        description="Exercise semantic final review on copied stored evidence."
    )
    _ = parser.add_argument("scenario", choices=("happy", "omitted"))
    _ = parser.add_argument("--run-id", required=True)
    _ = parser.add_argument("--runs-dir", type=Path, required=True)
    _ = parser.add_argument("--evidence-dir", type=Path, required=True)
    namespace = parser.parse_args(argv)
    args = _Arguments(
        scenario=cast("Literal['happy', 'omitted']", namespace.scenario),
        run_id=cast("str", namespace.run_id),
        runs_dir=cast("Path", namespace.runs_dir),
        evidence_dir=cast("Path", namespace.evidence_dir),
    )

    run_id = RunId(args.run_id)
    runs_dir = args.runs_dir.resolve()
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="final-review-qa-") as temporary:
        copied_runs = Path(temporary) / "runs"
        copied_runs.mkdir()
        _ = shutil.copytree(runs_dir / run_id, copied_runs / run_id)
        store = RunStore(copied_runs)
        paths = store.paths(run_id)
        _retarget_media_paths(paths.run_dir)
        paths.final_review_assignment.unlink(missing_ok=True)
        paths.final_review_artifact.unlink(missing_ok=True)
        paths.final_review_result.unlink(missing_ok=True)
        video = _media_path(paths.run_dir, "video_path")
        audio = _media_path(paths.run_dir, "audio_path")
        video_mode = stat.S_IMODE(video.stat().st_mode)
        audio_mode = stat.S_IMODE(audio.stat().st_mode)
        transcript_before = paths.transcript.read_bytes()

        async def reviewer(
            data: FinalReviewSegmentInput,
        ) -> ReviewSegmentResult:
            screen_text_by_frame: dict[str, list[str]] = {
                frame.frame_id: list(
                    _VISIBLE_TEXT_BY_FRAME.get(str(frame.frame_id), ())
                )
                for frame in data.frames
            }
            for cue in data.cues:
                if not cue.screen_text or not cue.screen_text.strip():
                    continue
                nearest = min(
                    data.frames,
                    key=lambda frame: abs(frame.pts_ms - cue.start_tc_ms),
                )
                screen_text_by_frame[nearest.frame_id].append(
                    cue.screen_text
                )
            observations = tuple(
                FrameReviewObservation(
                    frame_id=frame.frame_id,
                    visible_screen_text=tuple(
                        dict.fromkeys(
                            screen_text_by_frame[frame.frame_id]
                        )
                    ),
                    compared_cue_ids=data.segment.cue_ids,
                )
                for frame in data.frames
            )
            if (
                args.scenario == "omitted"
                and data.segment.segment_id == "segment-0001"
            ):
                return ReviewSegmentResult.model_construct(
                    segment_id=data.segment.segment_id,
                    frame_ids=data.segment.frame_ids,
                    cue_ids=data.segment.cue_ids,
                    frame_observations=observations[1:],
                    replacements=(),
                )
            return ReviewSegmentResult(
                segment_id=data.segment.segment_id,
                frame_ids=data.segment.frame_ids,
                cue_ids=data.segment.cue_ids,
                frame_observations=observations,
            )

        try:
            receipt = anyio.run(_execute, store, run_id, reviewer)
        except FinalReviewExecutionError as error:
            rejected: dict[str, object] = {
                "scenario": args.scenario,
                "status": "rejected",
                "error": str(error),
                "cause": repr(error.__cause__),
                "transcript_unchanged": (
                    paths.transcript.read_bytes() == transcript_before
                ),
                "receipt_exists": paths.final_review_artifact.exists(),
            }
            _write_result(evidence_dir, rejected)
            return 2
        except Exception as error:
            rejected = {
                "scenario": args.scenario,
                "status": "error",
                "error_type": type(error).__name__,
                "error": str(error),
                "permissions_restored": {
                    "video": stat.S_IMODE(video.stat().st_mode) == video_mode,
                    "audio": stat.S_IMODE(audio.stat().st_mode) == audio_mode,
                },
            }
            _write_result(evidence_dir, rejected)
            raise

        artifact = store.load_final_review_result(run_id)
        visible = sorted(
            {
                text
                for segment in artifact.segments
                for observation in segment.frame_observations
                for text in observation.visible_screen_text
            }
        )
        applied: dict[str, object] = {
            "scenario": args.scenario,
            "status": "applied",
            "segments": len(artifact.segments),
            "frames": sum(
                len(segment.frame_observations)
                for segment in artifact.segments
            ),
            "visible_screen_text": visible,
            "receipt_digest": receipt.result_digest,
            "permissions_restored": {
                "video": stat.S_IMODE(video.stat().st_mode) == video_mode,
                "audio": stat.S_IMODE(audio.stat().st_mode) == audio_mode,
            },
        }
        _write_result(evidence_dir, applied)
        return 0


async def _execute(
    store: RunStore,
    run_id: RunId,
    reviewer: FinalReviewSegmentReviewer,
) -> FinalReviewReceipt:
    return await execute_final_review(store, run_id, reviewer)


def _media_path(run_dir: Path, key: str) -> Path:
    media = cast(
        "dict[str, object]",
        json.loads((run_dir / "media.json").read_text(encoding="utf-8")),
    )
    value = media[key]
    if not isinstance(value, str):
        message = f"media manifest {key} must be a string"
        raise TypeError(message)
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def _retarget_media_paths(run_dir: Path) -> None:
    manifest_path = run_dir / "media.json"
    media = PreparedMediaManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    input_dir = Path("input")
    write_model_atomic(
        manifest_path,
        media.model_copy(
            update={
                "video_path": str(run_dir / input_dir / "source.mp4"),
                "audio_path": str(run_dir / input_dir / "audio.wav"),
                "subtitle_tracks": tuple(
                    track.model_copy(
                        update={
                            "path": str(run_dir / input_dir / Path(track.path).name)
                        }
                    )
                    for track in media.subtitle_tracks
                ),
                "frames": tuple(
                    frame.model_copy(
                        update={
                            "path": str(
                                input_dir / "frames" / Path(frame.path).name
                            )
                        }
                    )
                    for frame in media.frames
                ),
            }
        ),
    )


def _write_result(
    evidence_dir: Path,
    result: dict[str, object],
) -> None:
    rendered = json.dumps(result, ensure_ascii=False)
    _ = (evidence_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _ = sys.stdout.write(rendered + "\n")


if __name__ == "__main__":
    sys.exit(main())
