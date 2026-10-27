# Copyright 2026 Transcription Module contributors

"""Held-inode authority for media checkpoint publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from transcription_module._media_checkpoint_io import scan_directory
from transcription_module._media_checkpoint_journal import (
    InodeIdentity,
    PublicationAuthority,
    PublicationJournal,
)
from transcription_module._media_checkpoint_namespace import (
    InodeBinding,
    inode_binding,
)
from transcription_module.media_checkpoint_models import (
    CheckpointStorageError,
    Sha256Digest,
    StageName,
    sha256_bytes,
    tree_digest,
)

if TYPE_CHECKING:
    from transcription_module._media_checkpoint_fs import CheckpointFiles

_AUTHORITY_CHANGED: Final = "checkpoint namespace authority changed"


@dataclass(frozen=True, slots=True)
class OpenPublication:
    """Held source and optional destination inodes for one publication."""

    files: CheckpointFiles
    stage: StageName
    source_fd: int | None
    destination_fd: int | None = None


def create_authority(
    files: CheckpointFiles,
    source_fd: int,
) -> PublicationAuthority:
    """Capture persisted identities for a newly journaled publication."""
    root, private, staging, published = files.base_bindings()
    return PublicationAuthority(
        root=_persisted(root),
        private=_persisted(private),
        staging=_persisted(staging),
        published=_persisted(published),
        source=descriptor_identity(source_fd),
    )


def assert_base_authority(
    files: CheckpointFiles,
    authority: PublicationAuthority,
) -> None:
    """Require reopened fixed namespaces to match journaled identities."""
    files.assert_bound()
    expected = (
        authority.root,
        authority.private,
        authority.staging,
        authority.published,
    )
    if tuple(_persisted(item) for item in files.base_bindings()) != expected:
        raise CheckpointStorageError(_AUTHORITY_CHANGED)


def assert_recovery_authority(
    opened: OpenPublication,
    authority: PublicationAuthority,
) -> None:
    """Require reopened stage inodes to match journaled identities."""
    if (
        opened.source_fd is not None
        and descriptor_identity(opened.source_fd) != authority.source
    ):
        raise CheckpointStorageError(_AUTHORITY_CHANGED)
    if (
        opened.destination_fd is not None
        and authority.destination is not None
        and descriptor_identity(opened.destination_fd) != authority.destination
    ):
        raise CheckpointStorageError(_AUTHORITY_CHANGED)
    assert_open(opened)


def assert_open(opened: OpenPublication) -> None:
    """Require supplied descriptors to remain at their visible names."""
    opened.files.assert_stage_bound(
        opened.stage,
        opened.source_fd,
        opened.destination_fd,
    )


def target_complete(
    opened: OpenPublication,
    journal: PublicationJournal,
) -> bool:
    """Return whether receipt and copied output form the exact target."""
    checkpoint = opened.files.read_checkpoint()
    checkpoint_digest = sha256_bytes(checkpoint) if checkpoint is not None else None
    return (
        checkpoint_digest == journal.expected_checkpoint_digest
        and directory_digest(opened.destination_fd) == journal.expected_tree_digest
    )


def prior_complete(
    opened: OpenPublication,
    journal: PublicationJournal,
) -> bool:
    """Return whether exact staged bytes and prior receipt are restored."""
    checkpoint = opened.files.read_checkpoint()
    checkpoint_digest = sha256_bytes(checkpoint) if checkpoint is not None else None
    return (
        checkpoint_digest == journal.prior_checkpoint_digest
        and directory_digest(opened.source_fd) == journal.expected_tree_digest
        and opened.destination_fd is None
    )


def directory_digest(descriptor: int | None) -> Sha256Digest | None:
    """Digest direct files through one held stage descriptor."""
    if descriptor is None:
        return None
    observed = scan_directory(descriptor)
    return tree_digest(tuple((item.name, item.sha256) for item in observed))


def descriptor_identity(descriptor: int) -> InodeIdentity:
    """Convert one held descriptor identity to persisted authority."""
    return _persisted(inode_binding(descriptor))


def _persisted(binding: InodeBinding) -> InodeIdentity:
    return InodeIdentity(device=binding.device, inode=binding.inode)
