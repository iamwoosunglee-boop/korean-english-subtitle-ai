# Copyright 2026 Transcription Module contributors

"""Crash recovery for descriptor-copy media publication."""

from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import replace
from typing import TYPE_CHECKING, Final

import transcription_module._media_checkpoint_publication as publication_module
from transcription_module._media_checkpoint_authority import (
    OpenPublication,
    assert_base_authority,
    assert_recovery_authority,
    directory_digest,
    prior_complete,
    target_complete,
)
from transcription_module._media_checkpoint_journal import (
    PublicationJournal,
    parse_journal,
)
from transcription_module._media_checkpoint_publication import (
    FaultPoint,
    restore_checkpoint,
)
from transcription_module.media_checkpoint_models import (
    CheckpointRecoveryError,
    CheckpointStorageError,
    sha256_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from transcription_module._media_checkpoint_fs import CheckpointFiles

_UNSAFE_PENDING: Final = "unsafe pending publication path"
_AMBIGUOUS_OUTPUT: Final = "pending publication output is missing or ambiguous"
_AMBIGUOUS_CHECKPOINT: Final = "pending publication checkpoint is missing or ambiguous"
_INCOMPLETE_PRIOR: Final = "prior publication state is incomplete"


def recover_checkpoint(files: CheckpointFiles) -> None:
    """Roll forward a complete target, otherwise restore exact prior state."""
    payload = files.read_journal()
    if payload is None:
        return
    journal = parse_journal(payload)
    try:
        assert_base_authority(files, journal.authority)
        with ExitStack() as stack:
            source_fd = files.open_optional_stage(files.staging_fd, journal.stage)
            if source_fd is not None:
                _ = stack.callback(os.close, source_fd)
            destination_fd = files.open_optional_stage(
                files.published_fd, journal.stage
            )
            if destination_fd is not None:
                _ = stack.callback(os.close, destination_fd)
            opened = OpenPublication(
                files,
                journal.stage,
                source_fd,
                destination_fd,
            )
            assert_recovery_authority(opened, journal.authority)
            if target_complete(opened, journal):
                _target_cleanup(opened)
                return
            _restore_prior(opened, journal)
    except CheckpointStorageError as error:
        raise CheckpointRecoveryError(_UNSAFE_PENDING) from error


def _restore_prior(opened: OpenPublication, journal: PublicationJournal) -> None:
    expected = journal.expected_tree_digest
    current = opened.files.read_checkpoint()
    current_digest = sha256_bytes(current) if current is not None else None
    if directory_digest(opened.source_fd) != expected:
        raise CheckpointRecoveryError(_AMBIGUOUS_OUTPUT)
    destination_fd = opened.destination_fd
    if destination_fd is not None:
        _edge(
            FaultPoint.RECOVER_BEFORE_OUTPUT,
            FaultPoint.RECOVER_AFTER_OUTPUT,
            lambda: opened.files.remove_stage(
                opened.files.published_fd,
                opened.stage,
                destination_fd,
            ),
        )
        opened = replace(opened, destination_fd=None)
    if current_digest not in {
        journal.prior_checkpoint_digest,
        journal.expected_checkpoint_digest,
    }:
        raise CheckpointRecoveryError(_AMBIGUOUS_CHECKPOINT)
    if current_digest == journal.expected_checkpoint_digest:
        _edge(
            FaultPoint.RECOVER_BEFORE_CHECKPOINT,
            FaultPoint.RECOVER_AFTER_CHECKPOINT,
            lambda: restore_checkpoint(opened.files, journal),
        )
    if not prior_complete(opened, journal):
        raise CheckpointRecoveryError(_INCOMPLETE_PRIOR)
    _journal_cleanup(opened.files)


def _target_cleanup(opened: OpenPublication) -> None:
    def cleanup() -> None:
        if opened.source_fd is not None:
            opened.files.remove_stage(
                opened.files.staging_fd,
                opened.stage,
                opened.source_fd,
            )
        opened.files.remove_journal()

    _edge(
        FaultPoint.RECOVER_BEFORE_CLEANUP,
        FaultPoint.RECOVER_AFTER_CLEANUP,
        cleanup,
    )


def _journal_cleanup(files: CheckpointFiles) -> None:
    _edge(
        FaultPoint.RECOVER_BEFORE_CLEANUP,
        FaultPoint.RECOVER_AFTER_CLEANUP,
        files.remove_journal,
    )


def _edge(
    before: FaultPoint,
    after: FaultPoint,
    action: Callable[[], None],
) -> None:
    publication_module.publication_fault(before)
    action()
    publication_module.publication_fault(after)
