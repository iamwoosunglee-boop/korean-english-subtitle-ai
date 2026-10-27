# Copyright 2026 Transcription Module contributors

"""Deterministic policy-bound window source identities and VTT timing."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Final

from transcription_module.vad_evidence import VadEvidenceArtifact  # noqa: TC001
from transcription_module.window_evidence_models import (
    LegacyVerifiedSpeakerTurnArtifact,
    VerifiedSpeakerTurnArtifact,
    WindowSubtitleCue,
    WindowVadInterval,
    WindowVerifiedTurn,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from transcription_module._drafting_models import DiarizationRunArtifact
    from transcription_module.asr import AsrRunArtifact
    from transcription_module.media import PreparedMediaManifest, PreparedSubtitleTrack

_TIMING: Final = re.compile(r"^(\S+)\s+-->\s+(\S+)")
_TAG: Final = re.compile(r"<[^>]+>")
_MINUTE_TIMESTAMP_PARTS: Final = 2


class WindowEvidenceInputError(ValueError):
    """Stored window input evidence is malformed or leaves its run."""


@dataclass(frozen=True, slots=True)
class WindowSourceAuthority:
    """Complete run-level inputs that determine every local source identity."""

    media: PreparedMediaManifest
    asr: AsrRunArtifact
    vad: VadEvidenceArtifact
    diarization: DiarizationRunArtifact | None
    verified_speaker_turns: VerifiedSpeakerTurnArtifact | None


def window_authority_source_digest(authority: WindowSourceAuthority) -> str:
    """Bind source evidence while excluding its self-referential admission."""
    media = authority.media.model_dump(mode="json")
    inventory = media.get("subtitle_inventory")
    if isinstance(inventory, dict):
        inventory["evidence_authorities"] = []
    return _digest_payload(
        {
            "asr": authority.asr.model_dump(mode="json"),
            "diarization": (
                None
                if authority.diarization is None
                else authority.diarization.model_dump(mode="json")
            ),
            "media": media,
            "vad": authority.vad.model_dump(mode="json"),
        }
    )


def window_source_digest(authority: WindowSourceAuthority) -> str:
    """Bind source evidence and the exact independently derived turn artifact."""
    return _digest_payload(
        {
            "source_digest": window_authority_source_digest(authority),
            "verified_speaker_turns": (
                None
                if authority.verified_speaker_turns is None
                else authority.verified_speaker_turns.model_dump(mode="json")
            ),
        }
    )


def _digest_payload(payload: JsonValue) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def window_vad_intervals(
    vad: VadEvidenceArtifact,
) -> tuple[WindowVadInterval, ...]:
    """Assign stable identities to stored ordered VAD intervals."""
    return tuple(
        WindowVadInterval(
            interval_id=f"vad:{ordinal}",
            start_tc_ms=int(interval.start_tc_ms),
            end_tc_ms=int(interval.end_tc_ms),
        )
        for ordinal, interval in enumerate(vad.speech_intervals, start=1)
    )


def window_diarization_digest(
    diarization: DiarizationRunArtifact | None,
) -> str | None:
    """Return one canonical identity for optional raw diarization clues."""
    if diarization is None:
        return None
    payload = json.dumps(
        diarization.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def window_verified_turns(
    authority: (
        VerifiedSpeakerTurnArtifact
        | LegacyVerifiedSpeakerTurnArtifact
        | DiarizationRunArtifact
        | None
    ),
) -> tuple[WindowVerifiedTurn, ...]:
    """Expose only separately persisted verification, never raw diarization."""
    if not isinstance(authority, VerifiedSpeakerTurnArtifact):
        return ()
    return authority.turns


def load_window_subtitle_cues(
    run_dir: Path,
    tracks: tuple[PreparedSubtitleTrack, ...],
) -> tuple[WindowSubtitleCue, ...]:
    """Read and parse exact admitted VTT bytes for non-snapshot callers."""
    payloads: list[bytes] = []
    for track in tracks:
        path = _resolve_confined(run_dir, Path(track.path))
        try:
            payloads.append(path.read_bytes())
        except OSError as error:
            message = "window subtitle bytes cannot be read"
            raise WindowEvidenceInputError(message) from error
    return parse_window_subtitle_cues(tracks, tuple(payloads))


def parse_window_subtitle_cues(
    tracks: tuple[PreparedSubtitleTrack, ...],
    payloads: tuple[bytes, ...],
) -> tuple[WindowSubtitleCue, ...]:
    """Parse already captured VTT bytes into stable timed cue identities."""
    if len(tracks) != len(payloads):
        message = "window subtitle track and payload counts differ"
        raise WindowEvidenceInputError(message)
    cues: list[WindowSubtitleCue] = []
    for track, payload in zip(tracks, payloads, strict=True):
        if track.track_id is None:
            message = "window subtitle track lacks a stable identity"
            raise WindowEvidenceInputError(message)
        try:
            contents = payload.decode("utf-8-sig")
        except UnicodeError as error:
            message = "window subtitle bytes cannot be read"
            raise WindowEvidenceInputError(message) from error
        cues.extend(_parse_vtt(track.track_id, contents))
    return tuple(cues)


def _parse_vtt(track_id: str, contents: str) -> tuple[WindowSubtitleCue, ...]:
    normalized = contents.replace("\r\n", "\n").replace("\r", "\n")
    parsed: list[WindowSubtitleCue] = []
    for block in re.split(r"\n{2,}", normalized):
        lines = tuple(line.strip() for line in block.splitlines() if line.strip())
        timing_index = next(
            (index for index, line in enumerate(lines) if _TIMING.match(line)),
            None,
        )
        if timing_index is None:
            continue
        timing = _TIMING.match(lines[timing_index])
        if timing is None:
            continue
        text = " ".join(
            cleaned
            for raw in lines[timing_index + 1 :]
            if (cleaned := _clean_vtt_text(raw))
        )
        if not text:
            continue
        parsed.append(
            WindowSubtitleCue(
                cue_id=f"{track_id}:{len(parsed) + 1}",
                track_id=track_id,
                start_tc_ms=_timestamp_ms(timing.group(1)),
                end_tc_ms=_timestamp_ms(timing.group(2)),
                text=text,
            )
        )
    return tuple(parsed)


def _clean_vtt_text(value: str) -> str:
    return html.unescape(_TAG.sub("", value)).strip()


def _timestamp_ms(value: str) -> int:
    parts = value.replace(",", ".").split(":")
    if len(parts) not in {2, 3}:
        message = f"invalid WebVTT timestamp: {value}"
        raise WindowEvidenceInputError(message)
    try:
        hours = (
            Decimal(0) if len(parts) == _MINUTE_TIMESTAMP_PARTS else Decimal(parts[0])
        )
        minutes = Decimal(parts[-2])
        seconds = Decimal(parts[-1])
    except InvalidOperation as error:
        message = f"invalid WebVTT timestamp: {value}"
        raise WindowEvidenceInputError(message) from error
    total = (hours * 3_600 + minutes * 60 + seconds) * 1_000
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _resolve_confined(run_dir: Path, candidate: Path) -> Path:
    root = run_dir.resolve()
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    if not resolved.is_relative_to(root) or not resolved.is_file():
        message = f"window subtitle path is missing or leaves the run: {candidate}"
        raise WindowEvidenceInputError(message)
    return resolved


__all__ = [
    "WindowEvidenceInputError",
    "WindowSourceAuthority",
    "load_window_subtitle_cues",
    "parse_window_subtitle_cues",
    "window_authority_source_digest",
    "window_diarization_digest",
    "window_source_digest",
    "window_vad_intervals",
    "window_verified_turns",
]
