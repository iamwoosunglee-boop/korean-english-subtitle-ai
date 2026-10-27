# Copyright 2026 Transcription Module contributors

"""Deterministic legacy v1 to canonical v2 project migration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from urllib.parse import unquote, urlsplit

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    AssetReference,
    CanonicalEvidenceEntry,
    CanonicalEvidenceManifest,
    CanonicalId,
    CanonicalModel,
    DocumentLifecycleStatus,
    DocumentReference,
)
from translation_module.canonical.context import (
    ContentContextAsset,
    ContextDecisionStatus,
    ContextFact,
    ContextParticipant,
    ContextStage,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.source import (
    SourceCueV2,
    SourceFinalization,
    SourceTranscriptV2,
)
from translation_module.canonical.translation import (
    DecisionStatus,
    LineageStatus,
    ReviewAcknowledgement,
    TargetCueV2,
    TranslationDocumentV2,
    TranslationFinalization,
    TranslationOption,
    UnresolvedDecision,
)
from translation_module.canonical.validation import validate_canonical_save
from translation_module.document import (
    DocumentStatus,
    TranslationDocument,
    translation_digest,
)
from translation_module.evidence import FileEvidenceResolver
from translation_module.preparation import source_digest
from translation_module.source import SourceMaturity, SourceSnapshot

if TYPE_CHECKING:
    from translation_module.common import EpisodeContext, Participant
    from translation_module.document import RetainedContext

LEGACY_FINALIZED_AT = "1970-01-01T00:00:00Z"
LEGACY_FINALIZED_BY = "legacy-migration"


def _is_none(value: object) -> bool:
    return value is None


class LegacyMigrationError(ValueError):
    """Raised before any output is committed when legacy migration is unsafe."""


class MigrationWarningCode(StrEnum):
    """Stable warnings retained in the migration receipt."""

    PROGRAM_INFERRED_FROM_SUMMARY = "program_inferred_from_summary"
    ABSOLUTE_EVIDENCE_RETAINED = "absolute_evidence_retained"
    EVIDENCE_REBASED = "evidence_rebased"
    LEGACY_TRANSLATION_METADATA_FOLDED = "legacy_translation_metadata_folded"


class MigrationWarning(CanonicalModel):
    """One nonblocking legacy compatibility note."""

    code: MigrationWarningCode
    message: str = Field(min_length=1)


class MigrationOutputRole(StrEnum):
    """Fixed canonical files created by one project migration."""

    INPUT_CONTEXT = "input_context"
    SOURCE = "source"
    PREPARED_CONTEXT = "prepared_context"
    GLOSSARY = "glossary"
    TRANSLATION = "translation"


class MigrationOutputRecord(CanonicalModel):
    """Identity and relative path for one migrated canonical artifact."""

    role: MigrationOutputRole
    path: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MigrationValidationReport(CanonicalModel):
    """Before/after validation summary for one migration."""

    source_input_valid: Literal[True] = True
    translation_input_valid: Literal[True] | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    canonical_outputs_valid: Literal[True] = True
    source_cue_count_before: int = Field(ge=0, strict=True)
    source_cue_count_after: int = Field(ge=0, strict=True)
    target_cue_count_before: int | None = Field(
        default=None,
        ge=0,
        strict=True,
        exclude_if=_is_none,
    )
    target_cue_count_after: int | None = Field(
        default=None,
        ge=0,
        strict=True,
        exclude_if=_is_none,
    )
    evidence_entry_count_before: int = Field(ge=0, strict=True)
    evidence_entry_count_after: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def optional_translation_counts_are_complete(self) -> Self:
        """Require translation validation and counts together or omit all."""
        values = (
            self.translation_input_valid,
            self.target_cue_count_before,
            self.target_cue_count_after,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            message = "translation validation fields must be provided together"
            raise ValueError(message)
        return self


class CanonicalMigrationReceipt(CanonicalModel):
    """Deterministic audit record for one completed v1 project migration."""

    schema_version: Literal["canonical-migration-receipt.v1"] = (
        "canonical-migration-receipt.v1"
    )
    source_input_document_id: CanonicalId
    source_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    translation_input_document_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    translation_input_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        exclude_if=_is_none,
    )
    evidence_rebased: bool
    validation: MigrationValidationReport
    outputs: tuple[MigrationOutputRecord, ...]
    warnings: tuple[MigrationWarning, ...]

    @model_validator(mode="after")
    def translation_input_is_complete(self) -> Self:
        """Require translation identity and digest together or omit both."""
        has_id = self.translation_input_document_id is not None
        has_digest = self.translation_input_digest is not None
        if has_id != has_digest:
            message = "translation input identity and digest must be provided together"
            raise ValueError(message)
        return self


@dataclass(frozen=True, slots=True)
class CanonicalMigrationBundle:
    """All canonical artifacts produced before atomic project commit."""

    input_context: ContentContextAsset
    source: SourceTranscriptV2
    prepared_context: ContentContextAsset | None
    glossary: TranslationGlossaryAsset | None
    translation: TranslationDocumentV2 | None
    receipt: CanonicalMigrationReceipt


def _validate_legacy_source(source: SourceSnapshot) -> SourceSnapshot:
    try:
        validated = SourceSnapshot.model_validate_json(source.model_dump_json())
    except ValueError as error:
        message = f"legacy source is invalid: {error}"
        raise LegacyMigrationError(message) from error
    expected = source_digest(validated)
    if validated.content_digest != expected:
        message = (
            "legacy source digest mismatch: "
            f"stored={validated.content_digest} expected={expected}"
        )
        raise LegacyMigrationError(message)
    return validated


def _validate_legacy_translation(
    source: SourceSnapshot,
    translation: TranslationDocument,
) -> TranslationDocument:
    try:
        validated = TranslationDocument.model_validate_json(
            translation.model_dump_json()
        )
    except ValueError as error:
        message = f"legacy translation is invalid: {error}"
        raise LegacyMigrationError(message) from error
    expected = translation_digest(validated)
    if validated.content_digest != expected:
        message = (
            "legacy translation digest mismatch: "
            f"stored={validated.content_digest} expected={expected}"
        )
        raise LegacyMigrationError(message)
    if validated.source_document_id != source.document_id:
        message = "legacy translation source identity does not match source document"
        raise LegacyMigrationError(message)
    if validated.source_digest != source.content_digest:
        message = "legacy translation source digest does not match source document"
        raise LegacyMigrationError(message)
    if validated.context != source.context:
        message = "legacy translation context does not match source document"
        raise LegacyMigrationError(message)
    known_source_ids = {cue.cue_id for cue in source.cues}
    unknown_source_ids = {
        source_id
        for cue in validated.cues
        for source_id in cue.source_cue_ids
        if source_id not in known_source_ids
    }
    if unknown_source_ids:
        message = (
            "legacy translation references unknown source cues: "
            f"{sorted(unknown_source_ids)}"
        )
        raise LegacyMigrationError(message)
    source_positions = {
        cue.cue_id: position for position, cue in enumerate(source.cues)
    }
    if any(
        tuple(source_positions[source_id] for source_id in cue.source_cue_ids)
        != tuple(
            sorted(source_positions[source_id] for source_id in cue.source_cue_ids)
        )
        for cue in validated.cues
    ):
        message = "legacy translation source lineage violates source order"
        raise LegacyMigrationError(message)
    known_evidence_ids = {entry.evidence_id for entry in source.evidence.entries}
    unknown_evidence_ids = {
        evidence_id
        for evidence_ids in (
            *(cue.evidence_ids for cue in validated.cues),
            *(context.evidence_ids for context in validated.retained_contexts),
            *(item.evidence_ids for item in validated.unresolved),
        )
        for evidence_id in evidence_ids
        if evidence_id not in known_evidence_ids
    }
    if unknown_evidence_ids:
        message = (
            "legacy translation references unknown evidence: "
            f"{sorted(unknown_evidence_ids)}"
        )
        raise LegacyMigrationError(message)
    if (
        validated.status is DocumentStatus.FINALIZED
        and source.maturity is not SourceMaturity.FINALIZED
    ):
        message = "finalized legacy translation requires a finalized source"
        raise LegacyMigrationError(message)
    return validated


def _program_from_summary(summary: str) -> str:
    return next(
        (line.strip() for line in summary.splitlines() if line.strip()), summary
    )


def _unique_fact_id(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}:{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _participant(participant: Participant) -> ContextParticipant:
    aliases = [participant.name]
    if participant.english_name and participant.english_name not in aliases:
        aliases.append(participant.english_name)
    payload: dict[str, object] = {
        "participant_id": participant.participant_id,
        "name_ko": participant.name,
        "aliases": aliases,
        "evidence_ids": [],
    }
    if participant.english_name:
        payload["name_en"] = participant.english_name
    return ContextParticipant.model_validate(payload)


def _context_facts(
    context: EpisodeContext,
    *,
    context_evidence_ids: tuple[str, ...] = (),
    retained_contexts: tuple[RetainedContext, ...] = (),
) -> tuple[ContextFact, ...]:
    facts: list[ContextFact] = []
    used: set[str] = set()
    if context_evidence_ids:
        facts.append(
            ContextFact(
                fact_id=_unique_fact_id("legacy-episode-context", used),
                statement=context.summary,
                participant_ids=(),
                scope_source_cue_ids=(),
                evidence_ids=context_evidence_ids,
                decision_status=ContextDecisionStatus.VERIFIED,
                blocking=False,
            )
        )
    facts.extend(
        ContextFact(
            fact_id=_unique_fact_id(str(item.context_id), used),
            statement=item.summary,
            participant_ids=(),
            scope_source_cue_ids=tuple(str(value) for value in item.source_cue_ids),
            evidence_ids=tuple(str(value) for value in item.evidence_ids),
            decision_status=ContextDecisionStatus.VERIFIED,
            blocking=False,
        )
        for item in retained_contexts
    )
    return tuple(facts)


def _file_uri_path(base_uri: str) -> Path | None:
    parsed = urlsplit(base_uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def _relative_base_uri(output_dir: Path, evidence_root: Path) -> str:
    relative = Path(os.path.relpath(evidence_root, start=output_dir)).as_posix()
    return "./" if relative == "." else relative.rstrip("/") + "/"


def _verify_evidence_root(source: SourceSnapshot, evidence_root: Path) -> Path:
    try:
        root = evidence_root.resolve(strict=True)
    except OSError as error:
        message = f"evidence root is unavailable: {evidence_root}"
        raise LegacyMigrationError(message) from error
    resolver = FileEvidenceResolver(root)
    for entry in source.evidence.entries:
        try:
            resource = resolver.read(root.as_uri(), entry.locator)
        except OSError as error:
            message = f"evidence resource is unavailable: {entry.locator}"
            raise LegacyMigrationError(message) from error
        actual = hashlib.sha256(resource).hexdigest()
        if actual != entry.resource_digest:
            message = (
                f"evidence digest mismatch for {entry.locator}: "
                f"expected={entry.resource_digest} actual={actual}"
            )
            raise LegacyMigrationError(message)
    return root


def _evidence_manifest(
    source: SourceSnapshot,
    *,
    output_dir: Path,
    evidence_root: Path | None,
) -> tuple[CanonicalEvidenceManifest, MigrationWarning | None]:
    entries = tuple(
        CanonicalEvidenceEntry.model_validate(
            entry.model_dump(mode="json", exclude_none=True)
        )
        for entry in source.evidence.entries
    )
    original_base = str(source.evidence.base_uri)
    if evidence_root is not None:
        verified = _verify_evidence_root(source, evidence_root)
        manifest = CanonicalEvidenceManifest(
            base_uri=_relative_base_uri(output_dir, verified),
            entries=entries,
        )
        warning = MigrationWarning(
            code=MigrationWarningCode.EVIDENCE_REBASED,
            message=(
                "absolute evidence locator was replaced with a relative locator "
                "after every resource digest was verified"
            ),
        )
        return manifest, warning
    manifest = CanonicalEvidenceManifest(base_uri=original_base, entries=entries)
    if _file_uri_path(original_base) is None:
        return manifest, None
    return manifest, MigrationWarning(
        code=MigrationWarningCode.ABSOLUTE_EVIDENCE_RETAINED,
        message=(
            "legacy evidence base URI remains absolute because no evidence root "
            "was supplied"
        ),
    )


def _asset_reference(
    asset: ContentContextAsset | TranslationGlossaryAsset,
    path: str,
) -> AssetReference:
    return AssetReference(
        asset_id=asset.asset_id,
        revision_id=asset.revision_id,
        content_digest=asset.content_digest,
        path=path,
    )


def _document_reference(
    source: SourceTranscriptV2,
    path: str,
) -> DocumentReference:
    return DocumentReference(
        document_id=source.document_id,
        revision_id=source.revision_id,
        content_digest=source.content_digest,
        path=path,
    )


def _input_context(
    source: SourceSnapshot,
    evidence: CanonicalEvidenceManifest,
) -> ContentContextAsset:
    lifecycle = (
        AssetLifecycleStatus.READY
        if source.maturity is SourceMaturity.FINALIZED
        else AssetLifecycleStatus.DRAFT
    )
    asset = ContentContextAsset(
        asset_id=f"{source.document_id}:context",
        revision_id=f"{source.document_id}:context:input:v1-migration",
        content_digest="0" * 64,
        record_digest="0" * 64,
        stage=ContextStage.INPUT,
        lifecycle_status=lifecycle,
        program=source.context.program or _program_from_summary(source.context.summary),
        episode_summary=source.context.summary,
        evidence=evidence,
        participants=tuple(_participant(item) for item in source.context.participants),
        facts=_context_facts(
            source.context,
            context_evidence_ids=tuple(
                str(item) for item in source.context_evidence_ids
            ),
        ),
    )
    return bind_canonical_digests(asset)


def _source_v2(
    source: SourceSnapshot,
    input_context: ContentContextAsset,
    evidence: CanonicalEvidenceManifest,
) -> SourceTranscriptV2:
    lifecycle = (
        DocumentLifecycleStatus.FINALIZED
        if source.maturity is SourceMaturity.FINALIZED
        else DocumentLifecycleStatus.DRAFT
    )
    payload: dict[str, object] = {
        "document_id": source.document_id,
        "revision_id": f"{source.document_id}:v1-migration",
        "content_digest": "0" * 64,
        "record_digest": "0" * 64,
        "lifecycle_status": lifecycle,
        "source_locale": source.source_locale,
        "duration_ms": source.duration_ms,
        "input_context_ref": _asset_reference(input_context, "context.input.json"),
        "evidence": evidence,
        "cues": tuple(
            SourceCueV2.model_validate(cue.model_dump(mode="json", exclude_none=True))
            for cue in source.cues
        ),
    }
    if lifecycle is DocumentLifecycleStatus.FINALIZED:
        payload["finalization"] = SourceFinalization(
            finalized_at=LEGACY_FINALIZED_AT,
            finalized_by=LEGACY_FINALIZED_BY,
        )
    return bind_canonical_digests(SourceTranscriptV2.model_validate(payload))


def _prepared_context(
    translation: TranslationDocument,
    source: SourceTranscriptV2,
    evidence: CanonicalEvidenceManifest,
) -> ContentContextAsset:
    lifecycle = (
        AssetLifecycleStatus.READY
        if source.lifecycle_status is DocumentLifecycleStatus.FINALIZED
        else AssetLifecycleStatus.DRAFT
    )
    asset = ContentContextAsset(
        asset_id=f"{source.document_id}:context",
        revision_id=f"{source.document_id}:context:prepared:v1-migration",
        content_digest="0" * 64,
        record_digest="0" * 64,
        stage=ContextStage.PREPARED,
        lifecycle_status=lifecycle,
        prepared_from_source_ref=_document_reference(source, "source.json"),
        program=(
            translation.context.program
            or _program_from_summary(translation.context.summary)
        ),
        episode_summary=translation.context.summary,
        evidence=evidence,
        participants=tuple(
            _participant(item) for item in translation.context.participants
        ),
        facts=_context_facts(
            translation.context,
            retained_contexts=translation.retained_contexts,
        ),
    )
    return bind_canonical_digests(asset)


def _empty_glossary(
    source: SourceTranscriptV2,
    evidence: CanonicalEvidenceManifest,
) -> TranslationGlossaryAsset:
    lifecycle = (
        AssetLifecycleStatus.READY
        if source.lifecycle_status is DocumentLifecycleStatus.FINALIZED
        else AssetLifecycleStatus.DRAFT
    )
    glossary = TranslationGlossaryAsset(
        asset_id=f"{source.document_id}:glossary",
        revision_id=f"{source.document_id}:glossary:v1-migration",
        content_digest="0" * 64,
        record_digest="0" * 64,
        lifecycle_status=lifecycle,
        prepared_from_source_ref=_document_reference(source, "source.json"),
        evidence=evidence,
        entries=(),
    )
    return bind_canonical_digests(glossary)


def _unresolved_decisions(
    translation: TranslationDocument,
    warnings: list[MigrationWarning],
) -> tuple[UnresolvedDecision, ...]:
    questions = {item.question_id: item for item in translation.unresolved_questions}
    decisions: list[UnresolvedDecision] = []
    for item in translation.unresolved:
        question = questions[item.unresolved_id]
        folded = tuple(
            TranslationOption(
                option_id=option.option_id,
                text=option.text,
                meaning=f"{option.meaning}\nLegacy strength: {option.strength}",
                risk=option.risk,
            )
            for option in item.alternatives
        )
        warnings.append(
            MigrationWarning(
                code=MigrationWarningCode.LEGACY_TRANSLATION_METADATA_FOLDED,
                message=(
                    f"decision {item.unresolved_id} retained legacy rationale in "
                    f"this receipt: why_difficult={item.why_difficult!r}; "
                    f"recommendation_reason={item.recommendation_reason!r}"
                ),
            )
        )
        decisions.append(
            UnresolvedDecision(
                decision_id=item.unresolved_id,
                source_cue_ids=item.source_cue_ids,
                target_cue_ids=item.target_cue_ids,
                domain=question.review_domain,
                question=question.question_text,
                options=folded,
                recommendation_id=item.recommendation_id,
            )
        )
    return tuple(decisions)


def _translation_v2(
    translation: TranslationDocument,
    source: SourceTranscriptV2,
    context: ContentContextAsset,
    glossary: TranslationGlossaryAsset,
    warnings: list[MigrationWarning],
) -> TranslationDocumentV2:
    finalized = translation.status is DocumentStatus.FINALIZED
    decisions = _unresolved_decisions(translation, warnings)
    payload: dict[str, object] = {
        "document_id": translation.document_id,
        "revision_id": f"{translation.document_id}:v1-migration",
        "content_digest": "0" * 64,
        "record_digest": "0" * 64,
        "source_ref": _document_reference(source, "source.json"),
        "prepared_context_ref": _asset_reference(context, "context.prepared.json"),
        "glossary_ref": _asset_reference(glossary, "glossary.json"),
        "lifecycle_status": (
            DocumentLifecycleStatus.FINALIZED
            if finalized
            else DocumentLifecycleStatus.DRAFT
        ),
        "decision_status": (
            DecisionStatus.NEEDS_CONFIRMATION if decisions else DecisionStatus.CLEAR
        ),
        "cues": tuple(
            TargetCueV2(
                sequence=cue.sequence,
                cue_id=cue.cue_id,
                source_cue_ids=cue.source_cue_ids,
                lineage_status=LineageStatus.CONFIRMED,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                speakers=cue.speakers,
                content_kind=cue.content_kind,
                context_ids=cue.context_ids,
                evidence_ids=cue.evidence_ids,
            )
            for cue in translation.cues
        ),
        "unresolved_decisions": decisions,
    }
    if finalized:
        payload["finalization"] = TranslationFinalization(
            finalized_at=LEGACY_FINALIZED_AT,
            finalized_by=LEGACY_FINALIZED_BY,
            review_acknowledgement=ReviewAcknowledgement.LEGACY_STATUS_IMPORTED,
            finding_dispositions=(),
        )
    return bind_canonical_digests(TranslationDocumentV2.model_validate(payload))


def _output_record(
    role: MigrationOutputRole,
    path: str,
    artifact: (
        ContentContextAsset
        | TranslationGlossaryAsset
        | SourceTranscriptV2
        | TranslationDocumentV2
    ),
) -> MigrationOutputRecord:
    return MigrationOutputRecord(
        role=role,
        path=path,
        schema_version=artifact.schema_version,
        content_digest=artifact.content_digest,
        record_digest=artifact.record_digest,
    )


def migrate_v1_project(
    source: SourceSnapshot,
    translation: TranslationDocument | None = None,
    *,
    output_dir: Path,
    evidence_root: Path | None = None,
) -> CanonicalMigrationBundle:
    """Build one deterministic canonical project without writing any files."""
    legacy_source = _validate_legacy_source(source)
    legacy_translation = (
        None
        if translation is None
        else _validate_legacy_translation(legacy_source, translation)
    )
    evidence, evidence_warning = _evidence_manifest(
        legacy_source,
        output_dir=output_dir,
        evidence_root=evidence_root,
    )
    warnings: list[MigrationWarning] = []
    if legacy_source.context.program is None:
        warnings.append(
            MigrationWarning(
                code=MigrationWarningCode.PROGRAM_INFERRED_FROM_SUMMARY,
                message=(
                    "program was inferred from the first nonblank legacy context "
                    "summary line"
                ),
            )
        )
    if evidence_warning is not None:
        warnings.append(evidence_warning)
    input_context = _input_context(legacy_source, evidence)
    migrated_source = _source_v2(legacy_source, input_context, evidence)
    prepared: ContentContextAsset | None = None
    glossary: TranslationGlossaryAsset | None = None
    migrated_translation: TranslationDocumentV2 | None = None
    outputs = [
        _output_record(
            MigrationOutputRole.INPUT_CONTEXT,
            "context.input.json",
            input_context,
        ),
        _output_record(MigrationOutputRole.SOURCE, "source.json", migrated_source),
    ]
    canonical_outputs: list[
        ContentContextAsset
        | TranslationGlossaryAsset
        | SourceTranscriptV2
        | TranslationDocumentV2
    ] = [input_context, migrated_source]
    if legacy_translation is not None:
        prepared = _prepared_context(legacy_translation, migrated_source, evidence)
        glossary = _empty_glossary(migrated_source, evidence)
        migrated_translation = _translation_v2(
            legacy_translation,
            migrated_source,
            prepared,
            glossary,
            warnings,
        )
        outputs.extend(
            (
                _output_record(
                    MigrationOutputRole.PREPARED_CONTEXT,
                    "context.prepared.json",
                    prepared,
                ),
                _output_record(MigrationOutputRole.GLOSSARY, "glossary.json", glossary),
                _output_record(
                    MigrationOutputRole.TRANSLATION,
                    "translation.json",
                    migrated_translation,
                ),
            )
        )
        canonical_outputs.extend((prepared, glossary, migrated_translation))
    try:
        for artifact in canonical_outputs:
            validate_canonical_save(artifact)
    except ValueError as error:
        message = f"migrated canonical output failed save validation: {error}"
        raise LegacyMigrationError(message) from error
    validation_payload: dict[str, object] = {
        "source_input_valid": True,
        "canonical_outputs_valid": True,
        "source_cue_count_before": len(legacy_source.cues),
        "source_cue_count_after": len(migrated_source.cues),
        "evidence_entry_count_before": len(legacy_source.evidence.entries),
        "evidence_entry_count_after": len(migrated_source.evidence.entries),
    }
    if legacy_translation is not None and migrated_translation is not None:
        validation_payload.update(
            {
                "translation_input_valid": True,
                "target_cue_count_before": len(legacy_translation.cues),
                "target_cue_count_after": len(migrated_translation.cues),
            }
        )
    validation = MigrationValidationReport.model_validate(validation_payload)
    receipt_payload: dict[str, object] = {
        "source_input_document_id": legacy_source.document_id,
        "source_input_digest": legacy_source.content_digest,
        "evidence_rebased": evidence_root is not None,
        "validation": validation,
        "outputs": tuple(outputs),
        "warnings": tuple(warnings),
    }
    if legacy_translation is not None:
        receipt_payload["translation_input_document_id"] = (
            legacy_translation.document_id
        )
        receipt_payload["translation_input_digest"] = legacy_translation.content_digest
    receipt = CanonicalMigrationReceipt.model_validate(receipt_payload)
    return CanonicalMigrationBundle(
        input_context=input_context,
        source=migrated_source,
        prepared_context=prepared,
        glossary=glossary,
        translation=migrated_translation,
        receipt=receipt,
    )


def _json_bytes(model: CanonicalModel) -> bytes:
    payload = model.model_dump(mode="json", exclude_none=True)
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _project_files(bundle: CanonicalMigrationBundle) -> dict[str, bytes]:
    files = {
        "context.input.json": _json_bytes(bundle.input_context),
        "source.json": _json_bytes(bundle.source),
        "migration-receipt.json": _json_bytes(bundle.receipt),
    }
    if bundle.prepared_context is not None:
        files["context.prepared.json"] = _json_bytes(bundle.prepared_context)
    if bundle.glossary is not None:
        files["glossary.json"] = _json_bytes(bundle.glossary)
    if bundle.translation is not None:
        files["translation.json"] = _json_bytes(bundle.translation)
    return files


def _existing_project_bytes(output_dir: Path) -> dict[str, bytes]:
    if not output_dir.is_dir():
        return {}
    return {
        path.name: path.read_bytes()
        for path in sorted(output_dir.iterdir())
        if path.is_file()
    }


def write_migration_project(
    bundle: CanonicalMigrationBundle,
    output_dir: Path,
) -> None:
    """Commit all migrated files together or leave the destination untouched."""
    planned = _project_files(bundle)
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.migration-",
            dir=parent,
        )
    )
    try:
        for name, content in planned.items():
            _ = (temporary / name).write_bytes(content)
        if output_dir.exists():
            if _existing_project_bytes(output_dir) == planned:
                return
            message = (
                f"output directory contains a different migration project: {output_dir}"
            )
            raise FileExistsError(message)
        _ = temporary.replace(output_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


__all__ = [
    "CanonicalMigrationBundle",
    "CanonicalMigrationReceipt",
    "LegacyMigrationError",
    "MigrationOutputRecord",
    "MigrationOutputRole",
    "MigrationValidationReport",
    "MigrationWarning",
    "MigrationWarningCode",
    "migrate_v1_project",
    "write_migration_project",
]
