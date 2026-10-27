# Copyright 2026 Transcription Module contributors

"""Crash-consistent descriptor-copy media publication."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import transcription_module._media_checkpoint_fs as checkpoint_fs
from transcription_module._media_checkpoint_authority import (
    OpenPublication,
    assert_open,
    create_authority,
    descriptor_identity,
    target_complete,
)
from transcription_module._media_checkpoint_io import copy_directory
from transcription_module._media_checkpoint_journal import (
    ExpectedPublication,
    PublicationJournal,
)
from transcription_module._media_checkpoint_namespace import assert_name_binding
from transcription_module.media_checkpoint_models import (
    CheckpointRecoveryError,
    CheckpointStorageError,
    MediaCheckpoint,
    Sha256Digest,
    StageName,
    checkpoint_payload,
    sha256_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from transcription_module._media_checkpoint_fs import CheckpointFiles

_INCOMPLETE_TARGET: Final = "published target state is incomplete"


class FaultPoint(StrEnum):
    """Deterministic test seams around each durable publication mutation."""

    PUBLISH_BEFORE_JOURNAL = "publish_before_journal"
    PUBLISH_AFTER_JOURNAL = "publish_after_journal"
    PUBLISH_BEFORE_OUTPUT = "publish_before_output"
    PUBLISH_AFTER_OUTPUT = "publish_after_output"
    PUBLISH_BEFORE_CHECKPOINT = "publish_before_checkpoint"
    PUBLISH_AFTER_CHECKPOINT = "publish_after_checkpoint"
    PUBLISH_BEFORE_CLEANUP = "publish_before_cleanup"
    PUBLISH_AFTER_CLEANUP = "publish_after_cleanup"
    RECOVER_BEFORE_OUTPUT = "recover_before_output"
    RECOVER_AFTER_OUTPUT = "recover_after_output"
    RECOVER_BEFORE_CHECKPOINT = "recover_before_checkpoint"
    RECOVER_AFTER_CHECKPOINT = "recover_after_checkpoint"
    RECOVER_BEFORE_CLEANUP = "recover_before_cleanup"
    RECOVER_AFTER_CLEANUP = "recover_after_cleanup"


def publication_fault(point: FaultPoint) -> None:
    """Do nothing unless a deterministic test injects an interruption."""
    del point


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    """Exact prior and target state for one stage publication."""

    stage: StageName
    expected_tree_digest: Sha256Digest
    checkpoint: MediaCheckpoint
    prior_checkpoint: bytes | None


def publish_checkpoint(
    files: CheckpointFiles,
    plan: PublicationPlan,
    source_fd: int,
) -> None:
    """Copy held staged bytes and make the receipt the publication point."""
    checkpoint_bytes = checkpoint_payload(plan.checkpoint)
    journal = PublicationJournal.create(
        ExpectedPublication(
            stage=plan.stage,
            tree_digest=plan.expected_tree_digest,
            checkpoint_digest=sha256_bytes(checkpoint_bytes),
        ),
        plan.prior_checkpoint,
        create_authority(files, source_fd),
    )
    opened = OpenPublication(files, plan.stage, source_fd)
    destination_fd: int | None = None
    try:
        _edge(
            FaultPoint.PUBLISH_BEFORE_JOURNAL,
            FaultPoint.PUBLISH_AFTER_JOURNAL,
            lambda: files.write_journal(journal.payload()),
        )
        _namespace_hook(opened, checkpoint_fs.NamespacePoint.BEFORE_OUTPUT_COPY)
        publication_fault(FaultPoint.PUBLISH_BEFORE_OUTPUT)
        destination_fd = files.create_destination(plan.stage)
        opened = replace(opened, destination_fd=destination_fd)
        journal = journal.with_destination(descriptor_identity(destination_fd))
        files.write_journal(journal.payload())
        copy_directory(source_fd, destination_fd)
        publication_fault(FaultPoint.PUBLISH_AFTER_OUTPUT)
        _namespace_hook(opened, checkpoint_fs.NamespacePoint.AFTER_OUTPUT_COPY)
        _namespace_hook(opened, checkpoint_fs.NamespacePoint.BEFORE_RECEIPT)
        _edge(
            FaultPoint.PUBLISH_BEFORE_CHECKPOINT,
            FaultPoint.PUBLISH_AFTER_CHECKPOINT,
            lambda: files.write_checkpoint(checkpoint_bytes),
        )
        _namespace_hook(opened, checkpoint_fs.NamespacePoint.AFTER_RECEIPT)
        if not target_complete(opened, journal):
            raise CheckpointRecoveryError(_INCOMPLETE_TARGET)
        _cleanup(opened)
    except CheckpointStorageError:
        _rollback_consistency(opened, journal)
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)


def restore_checkpoint(
    files: CheckpointFiles,
    journal: PublicationJournal,
) -> None:
    """Restore exact prior checkpoint bytes through the held private fd."""
    if journal.prior_checkpoint_text is None:
        files.remove_checkpoint()
    else:
        files.write_checkpoint(journal.prior_checkpoint_text.encode())


def _cleanup(opened: OpenPublication) -> None:
    _namespace_hook(opened, checkpoint_fs.NamespacePoint.BEFORE_CLEANUP)
    publication_fault(FaultPoint.PUBLISH_BEFORE_CLEANUP)
    if opened.source_fd is not None:
        opened.files.remove_stage(
            opened.files.staging_fd,
            opened.stage,
            opened.source_fd,
        )
    opened.files.remove_journal()
    publication_fault(FaultPoint.PUBLISH_AFTER_CLEANUP)
    _namespace_hook(
        replace(opened, source_fd=None),
        checkpoint_fs.NamespacePoint.AFTER_CLEANUP,
    )


def _rollback_consistency(
    opened: OpenPublication,
    journal: PublicationJournal,
) -> None:
    current = opened.files.read_checkpoint()
    current_digest = sha256_bytes(current) if current is not None else None
    if current_digest == journal.expected_checkpoint_digest:
        restore_checkpoint(opened.files, journal)
    _restore_staging(opened)
    if opened.destination_fd is not None:
        opened.files.remove_stage(
            opened.files.published_fd,
            opened.stage,
            opened.destination_fd,
        )
    opened.files.remove_journal()


def _restore_staging(opened: OpenPublication) -> None:
    if opened.source_fd is None or opened.destination_fd is None:
        return
    try:
        assert_name_binding(
            opened.files.staging_fd, opened.stage.value, opened.source_fd
        )
    except CheckpointStorageError:
        restored_fd = opened.files.create_stage(
            opened.files.staging_fd, opened.stage
        )
    else:
        return
    try:
        copy_directory(opened.destination_fd, restored_fd)
    finally:
        os.close(restored_fd)


def _namespace_hook(
    opened: OpenPublication,
    point: checkpoint_fs.NamespacePoint,
) -> None:
    assert_open(opened)
    checkpoint_fs.namespace_fault(point)
    assert_open(opened)


def _edge(
    before: FaultPoint,
    after: FaultPoint,
    action: Callable[[], None],
) -> None:
    publication_fault(before)
    action()
    publication_fault(after)
