# Copyright 2026 Transcription Module contributors

"""Assemble selected canonical translation delivery formats with parity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import model_validator

from translation_module.canonical._attempt_store import TranslationAttemptStore
from translation_module.canonical._delivery_formats import (
    ReviewTimecodeSpec,
    csv_projection,
    parse_csv,
    parse_sbv,
    render_csv,
    render_sbv,
    sbv_projection,
)
from translation_module.canonical._selected_store import load_selected_candidate
from translation_module.canonical.common import (
    CanonicalId,
    CanonicalModel,
    DocumentLifecycleStatus,
)
from translation_module.canonical.runtime import finalize_selected_translation
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.canonical.validation import (
    EffectiveReviewState,
    TranslationValidationContext,
    evaluate_review_state,
    validate_canonical_save,
)
from translation_module.common import Sha256Digest

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical._attempt_models import (
        TranslationAttempt,
        TranslationSelectionReceipt,
    )
    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.review import (
        ReviewProfile,
        TranslationReviewArtifact,
    )
    from translation_module.canonical.source import SourceTranscriptV2

DELIVERY_JSON_NAME = "translation.json"
DELIVERY_CSV_NAME = "translation.review.csv"
DELIVERY_SBV_NAME = "translation.youtube.sbv"
DELIVERY_PARITY_NAME = "translation.parity.json"


@dataclass(frozen=True, slots=True)
class SelectedTranslationIdentity:
    """One selected canonical translation revision."""

    document_id: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class DeliveryDependencies:
    """Canonical dependencies required to verify selected finalization."""

    source: SourceTranscriptV2
    glossary: TranslationGlossaryAsset
    review_timecode: ReviewTimecodeSpec
    context: ContentContextAsset
    reviews: tuple[TranslationReviewArtifact, ...]
    review_profile: ReviewProfile


@dataclass(frozen=True, slots=True)
class DeliveryPayloads:
    """Rendered delivery bytes compared by the parity gate."""

    json: bytes
    csv: bytes
    sbv: bytes


class DeliveryParityReport(CanonicalModel):
    """Machine-readable parity result for one selected translation delivery."""

    schema_version: Literal["translation-delivery-parity.v1"] = (
        "translation-delivery-parity.v1"
    )
    selection_id: CanonicalId
    attempt_id: CanonicalId
    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    translation_content_digest: Sha256Digest
    cue_count: int
    json_sha256: Sha256Digest
    csv_sha256: Sha256Digest
    sbv_sha256: Sha256Digest
    attempt_matches: bool
    json_matches: bool
    csv_matches: bool
    sbv_matches: bool
    matches: bool

    @model_validator(mode="after")
    def aggregate_matches_components(self) -> DeliveryParityReport:
        """Keep the aggregate completion flag derived from every parity check."""
        expected = (
            self.attempt_matches
            and self.json_matches
            and self.csv_matches
            and self.sbv_matches
        )
        if self.matches != expected:
            message = "delivery parity aggregate does not match component results"
            raise ValueError(message)
        return self


_ = DeliveryParityReport.model_rebuild(
    _types_namespace={"Sha256Digest": Sha256Digest}
)


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def verify_delivery_bytes(
    candidate: TranslationDocumentV2,
    attempt: TranslationAttempt,
    receipt: TranslationSelectionReceipt,
    payloads: DeliveryPayloads,
    dependencies: DeliveryDependencies,
) -> DeliveryParityReport:
    """Parse each rendered format and compare it with one selected attempt."""
    try:
        delivered = TranslationDocumentV2.model_validate_json(payloads.json)
    except ValueError:
        delivered = None
    attempt_matches = (
        receipt.attempt_id == attempt.attempt_id
        and receipt.translation_document_digest == candidate.content_digest
        and attempt.candidate_digest == candidate.content_digest
    )
    json_matches = delivered == candidate
    csv_matches = parse_csv(payloads.csv) == csv_projection(
        candidate,
        attempt,
        dependencies.source,
        dependencies.review_timecode,
    )
    sbv_matches = parse_sbv(payloads.sbv) == sbv_projection(candidate)
    matches = attempt_matches and json_matches and csv_matches and sbv_matches
    return DeliveryParityReport(
        selection_id=receipt.selection_id,
        attempt_id=receipt.attempt_id,
        translation_document_id=candidate.document_id,
        translation_revision_id=candidate.revision_id,
        translation_content_digest=candidate.content_digest,
        cue_count=len(candidate.cues),
        json_sha256=_digest(payloads.json),
        csv_sha256=_digest(payloads.csv),
        sbv_sha256=_digest(payloads.sbv),
        attempt_matches=attempt_matches,
        json_matches=json_matches,
        csv_matches=csv_matches,
        sbv_matches=sbv_matches,
        matches=matches,
    )


def _write_delivery_bundle(outputs: tuple[tuple[Path, bytes], ...]) -> None:
    """Stage every delivery file and publish the completion receipt last."""
    staged: list[tuple[Path, Path]] = []
    try:
        for target, payload in outputs:
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            with temporary.open("xb") as handle:
                _ = handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((target, temporary))
        for target, temporary in staged:
            _ = temporary.replace(target)
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def assemble_selected_delivery(
    authority_root: Path,
    identity: SelectedTranslationIdentity,
    dependencies: DeliveryDependencies,
    output_dir: Path,
    *,
    finalized_document: TranslationDocumentV2,
) -> DeliveryParityReport:
    """Deliver only the exact reviewed finalization of the persisted selection."""
    validate_canonical_save(finalized_document)
    if (
        finalized_document.lifecycle_status is not DocumentLifecycleStatus.FINALIZED
        or finalized_document.finalization is None
    ):
        message = "delivery requires a finalized translation document"
        raise ValueError(message)
    candidate = load_selected_candidate(
        authority_root,
        identity.document_id,
        identity.revision_id,
        dependencies.source,
        dependencies.glossary,
    )
    evaluation = evaluate_review_state(
        candidate,
        dependencies.reviews,
        dependencies.review_profile,
        source=dependencies.source,
        glossary=dependencies.glossary,
    )
    if evaluation.state in {EffectiveReviewState.NOT_RUN, EffectiveReviewState.STALE}:
        message = "delivery requires a current complete review"
        raise ValueError(message)
    reconstructed = finalize_selected_translation(
        authority_root,
        identity.document_id,
        identity.revision_id,
        finalized_document.finalization,
        TranslationValidationContext(
            source=dependencies.source,
            context=dependencies.context,
            glossary=dependencies.glossary,
            reviews=dependencies.reviews,
            review_profile=dependencies.review_profile,
        ),
    )
    if reconstructed != finalized_document:
        message = "finalized document does not match the selected finalization"
        raise ValueError(message)
    store = TranslationAttemptStore(authority_root / "attempts")
    receipt = store.require_selected_receipt(candidate)
    attempt = next(
        item for item in store.load() if item.attempt_id == receipt.attempt_id
    )
    payloads = DeliveryPayloads(
        json=f"{finalized_document.model_dump_json(indent=2)}\n".encode(),
        csv=render_csv(
            finalized_document,
            attempt,
            dependencies.source,
            dependencies.review_timecode,
        ),
        sbv=render_sbv(finalized_document),
    )
    report = verify_delivery_bytes(
        finalized_document,
        attempt,
        receipt,
        payloads,
        dependencies,
    )
    if not report.matches:
        message = "delivery format parity failed"
        raise ValueError(message)
    output_dir.mkdir(parents=True, exist_ok=True)
    parity_payload = f"{report.model_dump_json(indent=2)}\n".encode()
    _write_delivery_bundle(
        (
            (output_dir / DELIVERY_JSON_NAME, payloads.json),
            (output_dir / DELIVERY_CSV_NAME, payloads.csv),
            (output_dir / DELIVERY_SBV_NAME, payloads.sbv),
            (output_dir / DELIVERY_PARITY_NAME, parity_payload),
        )
    )
    return report


__all__ = [
    "DELIVERY_CSV_NAME",
    "DELIVERY_JSON_NAME",
    "DELIVERY_PARITY_NAME",
    "DELIVERY_SBV_NAME",
    "DeliveryDependencies",
    "DeliveryParityReport",
    "DeliveryPayloads",
    "ReviewTimecodeSpec",
    "SelectedTranslationIdentity",
    "assemble_selected_delivery",
    "verify_delivery_bytes",
]
