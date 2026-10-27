# Copyright 2026 Transcription Module contributors

"""Parse untrusted CLI values into domain primitives exactly once."""

from __future__ import annotations

from typing import Final, Self
from urllib.parse import parse_qs
from uuid import UUID

from pydantic import AnyHttpUrl, ValidationError, model_validator

from transcription_module.errors import CliBoundaryError, ErrorCode
from transcription_module.models import (
    FrozenModel,
    RunId,
    YouTubeVideoUrl,
)

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    }
)
INVALID_URL_MESSAGE: Final = "source URL must be an HTTPS YouTube video URL"
DIRECT_VIDEO_PATH_LENGTH: Final = 2


class YouTubeUrlInput(FrozenModel):
    """Validated HTTPS URL for one YouTube video."""

    url: AnyHttpUrl

    @model_validator(mode="after")
    def points_to_video(self) -> Self:
        """Reject non-HTTPS, non-YouTube and non-video locations."""
        if self.url.scheme != "https" or self.url.host not in YOUTUBE_HOSTS:
            raise ValueError(INVALID_URL_MESSAGE)
        if self.url.host == "youtu.be":
            if not (self.url.path or "").strip("/"):
                raise ValueError(INVALID_URL_MESSAGE)
            return self
        watch_id = parse_qs(self.url.query or "").get("v", ())
        path_parts = tuple(
            part for part in (self.url.path or "").split("/") if part
        )
        is_direct_video = (
            len(path_parts) == DIRECT_VIDEO_PATH_LENGTH
            and path_parts[0] in {"live", "shorts"}
        )
        if not watch_id and not is_direct_video:
            raise ValueError(INVALID_URL_MESSAGE)
        return self


def parse_youtube_url(value: str) -> YouTubeVideoUrl:
    """Return a branded URL or one stable invalid-url error."""
    try:
        parsed = YouTubeUrlInput.model_validate({"url": value})
    except ValidationError as error:
        raise CliBoundaryError(ErrorCode.INVALID_URL, INVALID_URL_MESSAGE) from error
    return YouTubeVideoUrl(str(parsed.url))


def parse_run_id(value: str) -> RunId:
    """Return a canonical UUID run ID that cannot escape the run root."""
    try:
        parsed = UUID(value)
    except ValueError as error:
        message = "run ID must be a canonical UUID"
        raise CliBoundaryError(ErrorCode.INVALID_RUN_ID, message) from error
    return RunId(str(parsed))
