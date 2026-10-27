# Copyright 2026 Transcription Module contributors

"""Append-only confirmation log parsing and projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module.confirmation_models import (
    CONFIRMATION_RECORD_ADAPTER,
    ConfirmationAnswerRecord,
    ConfirmationAppliedRecord,
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationRecord,
    ConfirmationRequestRecord,
    ConfirmationRetiredRecord,
    ConfirmationSuggestionRecord,
    ConfirmationVisualInsertionAnswerRecord,
    ConfirmationVisualInsertionAppliedRecord,
)
from transcription_module.errors import CliBoundaryError, ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConfirmationHistory:
    """Validated projection of the append-only confirmation log."""

    records: tuple[ConfirmationRecord, ...]
    requests: tuple[ConfirmationItem, ...]
    answered_ids: frozenset[ConfirmationId]
    applied_ids: frozenset[ConfirmationId]
    retired_ids: frozenset[ConfirmationId]
    suggestions: tuple[ConfirmationSuggestionRecord, ...]

    def next_item(self) -> ConfirmationItem | None:
        """Return the earliest unresolved item in video-time order."""
        return min(
            (
                item
                for item in self.requests
                if item.confirmation_id not in self.resolved_ids
            ),
            key=lambda item: (
                item.start_tc_ms,
                item.end_tc_ms,
                0 if item.kind.value == "visual_insertion" else 1,
                item.cue_sequence,
            ),
            default=None,
        )

    @property
    def resolved_ids(self) -> frozenset[ConfirmationId]:
        """Return applied decisions plus retired dependent requests."""
        return self.applied_ids | self.retired_ids


@dataclass(slots=True)
class _ConfirmationProjection:
    requests: list[ConfirmationItem]
    request_ids: set[ConfirmationId]
    answered_ids: set[ConfirmationId]
    applied_ids: set[ConfirmationId]
    retired_ids: set[ConfirmationId]
    suggestions: list[ConfirmationSuggestionRecord]


def assert_confirmation_log_readable(path: Path) -> None:
    """Reject malformed JSONL before any draft or state mutation."""
    try:
        _ = load_confirmation_history(path)
    except ConfirmationError as error:
        message = "confirmation log is malformed"
        raise CliBoundaryError(
            ErrorCode.CORRUPT_CONFIRMATION_LOG,
            message,
        ) from error


def append_confirmation_requests(
    path: Path,
    items: tuple[ConfirmationItem, ...],
) -> None:
    """Append unresolved requests without rewriting existing history."""
    history = load_confirmation_history(path)
    known_ids = {item.confirmation_id for item in history.requests}
    if any(item.confirmation_id in known_ids for item in items):
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "confirmation request ID already exists",
        )
    records = tuple(
        ConfirmationRequestRecord(
            ordinal=len(history.records) + offset,
            item=item,
        )
        for offset, item in enumerate(items, start=1)
    )
    append_confirmation_records(path, records)


def next_confirmation_item(path: Path) -> ConfirmationItem | None:
    """Return the single item that may be answered next."""
    return load_confirmation_history(path).next_item()


def load_confirmation_history(path: Path) -> ConfirmationHistory:
    """Parse and semantically validate the entire log before mutation."""
    if not path.exists():
        return ConfirmationHistory(
            (),
            (),
            frozenset(),
            frozenset(),
            frozenset(),
            (),
        )
    records = _parse_confirmation_records(path)
    return project_confirmation_history(records)


def _parse_confirmation_records(
    path: Path,
) -> tuple[ConfirmationRecord, ...]:
    """Parse JSONL records through the discriminated boundary model."""
    try:
        with path.open(encoding="utf-8") as source:
            return tuple(
                CONFIRMATION_RECORD_ADAPTER.validate_json(line)
                for line in source
            )
    except (UnicodeDecodeError, ValidationError) as error:
        raise ConfirmationError(
            ConfirmationErrorCode.CORRUPT_LOG,
            "confirmation log is malformed",
        ) from error


def project_confirmation_history(
    records: tuple[ConfirmationRecord, ...],
) -> ConfirmationHistory:
    """Validate record ordering and references into a resumable projection."""
    projection = _ConfirmationProjection([], set(), set(), set(), set(), [])
    for expected_ordinal, record in enumerate(records, start=1):
        if record.ordinal != expected_ordinal:
            raise ConfirmationError(
                ConfirmationErrorCode.CORRUPT_LOG,
                "confirmation log ordinal is not contiguous",
            )
        _project_record(
            record,
            projection,
        )
    return ConfirmationHistory(
        records=records,
        requests=tuple(projection.requests),
        answered_ids=frozenset(projection.answered_ids),
        applied_ids=frozenset(projection.applied_ids),
        retired_ids=frozenset(projection.retired_ids),
        suggestions=tuple(projection.suggestions),
    )


def _project_record(
    record: ConfirmationRecord,
    projection: _ConfirmationProjection,
) -> None:
    match record:
        case ConfirmationRequestRecord(item=item):
            if item.confirmation_id in projection.request_ids:
                raise ConfirmationError(
                    ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
                    "confirmation request ID already exists",
                )
            projection.requests.append(item)
            projection.request_ids.add(item.confirmation_id)
        case (
            ConfirmationAnswerRecord(confirmation_id=confirmation_id)
            | ConfirmationVisualInsertionAnswerRecord(
                confirmation_id=confirmation_id
            )
        ):
            _project_answer(confirmation_id, projection)
        case ConfirmationSuggestionRecord(confirmation_id=confirmation_id):
            if confirmation_id not in projection.request_ids:
                raise ConfirmationError(
                    ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
                    "suggestion references an unknown confirmation ID",
                )
            projection.suggestions.append(record)
        case (
            ConfirmationAppliedRecord(confirmation_id=confirmation_id)
            | ConfirmationVisualInsertionAppliedRecord(
                confirmation_id=confirmation_id
            )
        ):
            _project_applied(confirmation_id, projection)
        case ConfirmationRetiredRecord(
            confirmation_id=confirmation_id,
            depends_on_confirmation_id=depends_on_confirmation_id,
        ):
            request = next(
                (
                    item
                    for item in projection.requests
                    if item.confirmation_id == confirmation_id
                ),
                None,
            )
            if (
                request is None
                or request.depends_on_confirmation_id
                != depends_on_confirmation_id
                or confirmation_id in projection.applied_ids
                or confirmation_id in projection.retired_ids
            ):
                raise ConfirmationError(
                    ConfirmationErrorCode.CORRUPT_LOG,
                    "retired record has no matching dependent request",
                )
            projection.retired_ids.add(confirmation_id)


def _project_answer(
    confirmation_id: ConfirmationId,
    projection: _ConfirmationProjection,
) -> None:
    if confirmation_id not in projection.request_ids:
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "answer references an unknown confirmation ID",
        )
    if confirmation_id in projection.answered_ids:
        raise ConfirmationError(
            ConfirmationErrorCode.DUPLICATE_ANSWER,
            "confirmation item has multiple answers",
        )
    projection.answered_ids.add(confirmation_id)


def _project_applied(
    confirmation_id: ConfirmationId,
    projection: _ConfirmationProjection,
) -> None:
    if (
        confirmation_id not in projection.answered_ids
        or confirmation_id in projection.applied_ids
    ):
        raise ConfirmationError(
            ConfirmationErrorCode.CORRUPT_LOG,
            "applied record has no unique preceding answer",
        )
    projection.applied_ids.add(confirmation_id)


def append_confirmation_records(
    path: Path,
    records: tuple[ConfirmationRecord, ...],
) -> None:
    """Append validated records in their stable JSONL representation."""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        for record in records:
            _ = output.write(f"{record.model_dump_json()}\n")
