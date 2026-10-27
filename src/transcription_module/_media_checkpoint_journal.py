# Copyright 2026 Transcription Module contributors

"""Typed path-free WAL authority for one media stage publication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from transcription_module.media_checkpoint_models import (
    CheckpointRecoveryError,
    MediaCheckpoint,
    Sha256Digest,
    StageName,
    sha256_bytes,
)
from transcription_module.models import FrozenModel

_JOURNAL_PRIOR: Final = "journal_prior"
_JOURNAL_DIGEST: Final = "journal_digest"
_INVALID_JOURNAL: Final = "invalid media checkpoint journal"


@dataclass(frozen=True, slots=True)
class ExpectedPublication:
    """Typed stage and digests of the complete requested target state."""

    stage: StageName
    tree_digest: Sha256Digest
    checkpoint_digest: Sha256Digest


class InodeIdentity(FrozenModel):
    """Persisted device/inode identity without any lexical path."""

    device: int = Field(ge=0)
    inode: int = Field(ge=0)


class PublicationAuthority(FrozenModel):
    """Expected fixed-layout and copy-source namespace identities."""

    root: InodeIdentity
    private: InodeIdentity
    staging: InodeIdentity
    published: InodeIdentity
    source: InodeIdentity
    destination: InodeIdentity | None = None


@dataclass(frozen=True, slots=True)
class JournalIdentity:
    """All machine-consumed fields covered by the journal digest."""

    stage: StageName
    tree: Sha256Digest
    checkpoint: Sha256Digest
    prior_text: str | None
    prior_digest: Sha256Digest | None
    authority: PublicationAuthority


class PublicationJournal(FrozenModel):
    """Integrity-bound prior state and expected path-free publication state."""

    schema_version: Literal["media-checkpoint-publication.v2"] = (
        "media-checkpoint-publication.v2"
    )
    stage: StageName
    expected_tree_digest: Sha256Digest
    expected_checkpoint_digest: Sha256Digest
    prior_checkpoint_text: str | None
    prior_checkpoint_digest: Sha256Digest | None
    authority: PublicationAuthority
    record_digest: Sha256Digest

    @classmethod
    def create(
        cls,
        expected: ExpectedPublication,
        prior_checkpoint: bytes | None,
        authority: PublicationAuthority,
    ) -> PublicationJournal:
        """Create a journal from exact prior bytes and held inode identities."""
        prior_text = prior_checkpoint.decode() if prior_checkpoint is not None else None
        prior_digest = (
            sha256_bytes(prior_checkpoint) if prior_checkpoint is not None else None
        )
        return cls(
            stage=expected.stage,
            expected_tree_digest=expected.tree_digest,
            expected_checkpoint_digest=expected.checkpoint_digest,
            prior_checkpoint_text=prior_text,
            prior_checkpoint_digest=prior_digest,
            authority=authority,
            record_digest=_identity_digest(
                JournalIdentity(
                    expected.stage,
                    expected.tree_digest,
                    expected.checkpoint_digest,
                    prior_text,
                    prior_digest,
                    authority,
                )
            ),
        )

    def with_destination(self, destination: InodeIdentity) -> PublicationJournal:
        """Bind the newly created destination inode before copying bytes."""
        authority = PublicationAuthority(
            root=self.authority.root,
            private=self.authority.private,
            staging=self.authority.staging,
            published=self.authority.published,
            source=self.authority.source,
            destination=destination,
        )
        expected = ExpectedPublication(
            stage=self.stage,
            tree_digest=self.expected_tree_digest,
            checkpoint_digest=self.expected_checkpoint_digest,
        )
        prior = (
            self.prior_checkpoint_text.encode()
            if self.prior_checkpoint_text is not None
            else None
        )
        return self.create(expected, prior, authority)

    @model_validator(mode="after")
    def prior_and_record_integrity_match(self) -> Self:
        """Reject malformed prior state and any changed journal field."""
        prior_bytes = (
            self.prior_checkpoint_text.encode()
            if self.prior_checkpoint_text is not None
            else None
        )
        prior_digest = sha256_bytes(prior_bytes) if prior_bytes is not None else None
        if prior_digest != self.prior_checkpoint_digest:
            raise PydanticCustomError(
                _JOURNAL_PRIOR,
                "prior checkpoint digest mismatch",
            )
        if prior_bytes is not None:
            try:
                _ = MediaCheckpoint.model_validate_json(prior_bytes)
            except ValidationError as error:
                raise PydanticCustomError(
                    _JOURNAL_PRIOR,
                    "prior checkpoint is invalid",
                ) from error
        expected = _identity_digest(
            JournalIdentity(
                self.stage,
                self.expected_tree_digest,
                self.expected_checkpoint_digest,
                self.prior_checkpoint_text,
                self.prior_checkpoint_digest,
                self.authority,
            )
        )
        if expected != self.record_digest:
            raise PydanticCustomError(_JOURNAL_DIGEST, "journal digest mismatch")
        return self

    def payload(self) -> bytes:
        """Serialize the journal in its sole durable representation."""
        return f"{self.model_dump_json(indent=2)}\n".encode()


def _identity_digest(identity: JournalIdentity) -> Sha256Digest:
    payload = json.dumps(
        (
            identity.stage.value,
            identity.tree,
            identity.checkpoint,
            identity.prior_text,
            identity.prior_digest,
            identity.authority.model_dump(mode="json"),
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256_bytes(payload)


def parse_journal(payload: bytes) -> PublicationJournal:
    """Parse untrusted journal bytes once into strict private state."""
    try:
        return PublicationJournal.model_validate_json(payload)
    except (ValidationError, UnicodeDecodeError) as error:
        raise CheckpointRecoveryError(_INVALID_JOURNAL) from error
