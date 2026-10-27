# Copyright 2026 Transcription Module contributors

"""Stored-media path helpers for the final-review QA runner."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic


def media_path(run_dir: Path, key: Literal["video_path", "audio_path"]) -> Path:
    """Resolve one typed media source path from the copied run manifest."""
    media = PreparedMediaManifest.model_validate_json(
        (run_dir / "media.json").read_text(encoding="utf-8")
    )
    value = {
        "video_path": media.video_path,
        "audio_path": media.audio_path,
    }[key]
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def retarget_media_paths(run_dir: Path) -> None:
    """Bind copied evidence paths to the isolated QA run directory."""
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
                            "path": str(
                                run_dir / input_dir / Path(track.path).name
                            )
                        }
                    )
                    for track in media.subtitle_tracks
                ),
                "frames": tuple(
                    frame.model_copy(
                        update={
                            "path": str(input_dir / "frames" / Path(frame.path).name)
                        }
                    )
                    for frame in media.frames
                ),
            }
        ),
    )


__all__ = ["media_path", "retarget_media_paths"]
