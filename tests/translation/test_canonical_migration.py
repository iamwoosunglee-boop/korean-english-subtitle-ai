# Copyright 2026 Transcription Module contributors

"""Legacy v1 to canonical v2 migration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.translation.type_builders import context_id, evidence_id, milliseconds
from translation_module.canonical import (
    AssetLifecycleStatus,
    ContextStage,
    DocumentLifecycleStatus,
    LineageStatus,
    ReviewAcknowledgement,
    SourceTranscriptV2,
    TranslationDocumentV2,
    verify_canonical_digests,
)
from translation_module.canonical.migration import (
    CanonicalMigrationBundle,
    CanonicalMigrationReceipt,
    LegacyMigrationError,
    MigrationWarningCode,
    migrate_v1_project,
    write_migration_project,
)
from translation_module.cli import app
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    Participant,
    SourceCueId,
    TargetCueId,
)
from translation_module.document import (
    DocumentStatus,
    RetainedContext,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
)
from translation_module.preparation import bind_source_digest
from translation_module.source import (
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)
from translation_module.validation import bind_translation_digest
from translation_module.workflow import ReviewDomain

runner = CliRunner()
LEGACY_ACTOR = "legacy-migration"
LEGACY_TIMESTAMP = "1970-01-01T00:00:00Z"
CLI_ERROR_EXIT = 2
FIXTURE_CUE_COUNT = 2
FIXTURE_EVIDENCE_COUNT = 1


def _source(
    evidence_root: Path,
    *,
    maturity: SourceMaturity = SourceMaturity.FINALIZED,
) -> SourceSnapshot:
    resource = evidence_root / "transcript.ass"
    _ = resource.write_text(
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,안녕\n"
    )
    digest = hashlib.sha256(resource.read_bytes()).hexdigest()
    source = SourceSnapshot(
        document_id="source-document",
        content_digest="0" * 64,
        maturity=maturity,
        source_locale="ko-KR",
        duration_ms=milliseconds(2_000),
        context=EpisodeContext(
            summary="토크라운지\nNOVA NOVA 편",
            participants=(
                Participant(
                    participant_id="세라",
                    name="세라",
                    english_name="Sera",
                ),
            ),
        ),
        context_evidence_ids=(evidence_id("source-ass"),),
        evidence=EvidenceManifest(
            base_uri=evidence_root.as_uri() + "/",
            entries=(
                EvidenceEntry(
                    evidence_id=evidence_id("source-ass"),
                    kind=EvidenceKind.TEXT,
                    media_type="text/x-ssa",
                    resource_digest=digest,
                    locator="transcript.ass",
                ),
            ),
        ),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source:ass-dialogue:000001"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="토크 라운지에 오신",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                context_note="프로그램 도입부",
                screen_text="토크라운지",
                evidence_ids=(evidence_id("source-ass"),),
            ),
            SourceCue(
                sequence=2,
                cue_id=SourceCueId("source:ass-dialogue:000002"),
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="노바 노바",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )
    return bind_source_digest(source)


def _translation(
    source: SourceSnapshot,
    *,
    status: DocumentStatus = DocumentStatus.FINALIZED,
) -> TranslationDocument:
    unresolved: tuple[UnresolvedItem, ...] = ()
    unresolved_questions: tuple[UnresolvedQuestion, ...] = ()
    if status is DocumentStatus.NEEDS_CONFIRMATION:
        unresolved = (
            UnresolvedItem(
                unresolved_id="decision-rina",
                source_cue_ids=(SourceCueId("source:ass-dialogue:000002"),),
                target_cue_ids=(TargetCueId("target:000002"),),
                current_translation="Arina",
                why_difficult="공식명과 실제 애칭 중 선택이 필요하다.",
                alternatives=(
                    TranslationAlternative(
                        option_id="athena",
                        text="Arina",
                        meaning="공식 활동명",
                        strength="공식 표기",
                        risk="실제 애칭 발화를 잃는다.",
                    ),
                    TranslationAlternative(
                        option_id="rina",
                        text="Rina",
                        meaning="실제 애칭",
                        strength="원음 보존",
                        risk="공식 활동명과 다르다.",
                    ),
                ),
                recommendation_id="rina",
                recommendation_reason="해당 cue에서는 실제 애칭을 사용한다.",
            ),
        )
        unresolved_questions = (
            UnresolvedQuestion(
                question_id="decision-rina",
                question_text="이 cue에서 Rina를 사용할까요?",
                source_cue_ids=(SourceCueId("source:ass-dialogue:000002"),),
                target_cue_ids=(TargetCueId("target:000002"),),
                review_domain=ReviewDomain.CULTURE,
                source_digest=source.content_digest,
                candidate_digest="0" * 64,
                assignment_digest="a" * 64,
                revision_session_id="legacy-session",
            ),
        )
    document = TranslationDocument(
        document_id="translation-document",
        content_digest="0" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale="ko-KR",
        target_locale="en-US",
        context=source.context,
        retained_contexts=(
            RetainedContext(
                context_id=context_id("context-welcome"),
                summary="진행자가 게스트를 환영한다.",
                source_cue_ids=(SourceCueId("source:ass-dialogue:000001"),),
                evidence_ids=(evidence_id("source-ass"),),
            ),
        ),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target:000001"),
                source_cue_ids=(SourceCueId("source:ass-dialogue:000001"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Welcome to Talk Lounge,",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                context_ids=(context_id("context-welcome"),),
                evidence_ids=(evidence_id("source-ass"),),
            ),
            TargetCue(
                sequence=2,
                cue_id=TargetCueId("target:000002"),
                source_cue_ids=(SourceCueId("source:ass-dialogue:000002"),),
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="NOVA NOVA!",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=unresolved,
        unresolved_questions=unresolved_questions,
        status=status,
    )
    bound = bind_translation_digest(document)
    if unresolved_questions:
        questions = tuple(
            question.model_copy(update={"candidate_digest": bound.content_digest})
            for question in unresolved_questions
        )
        bound = bound.model_copy(update={"unresolved_questions": questions})
    return bound


def _warning_codes(bundle: CanonicalMigrationBundle) -> set[MigrationWarningCode]:
    return {warning.code for warning in bundle.receipt.warnings}


def test_migration_preserves_source_cues_context_and_opaque_ids(tmp_path: Path) -> None:
    """Source migration keeps editorial content and moves inline context to an asset."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)

    bundle = migrate_v1_project(source, output_dir=tmp_path / "project")

    assert bundle.source.document_id == source.document_id
    assert bundle.source.revision_id == "source-document:v1-migration"
    assert bundle.source.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert bundle.source.finalization is not None
    assert bundle.source.finalization.finalized_by == LEGACY_ACTOR
    assert bundle.source.finalization.finalized_at == LEGACY_TIMESTAMP
    assert [cue.cue_id for cue in bundle.source.cues] == [
        cue.cue_id for cue in source.cues
    ]
    assert [cue.text for cue in bundle.source.cues] == [cue.text for cue in source.cues]
    assert bundle.source.cues[0].context_note == source.cues[0].context_note
    assert bundle.source.cues[0].screen_text == source.cues[0].screen_text
    assert bundle.input_context.stage is ContextStage.INPUT
    assert bundle.input_context.lifecycle_status is AssetLifecycleStatus.READY
    assert bundle.input_context.episode_summary == source.context.summary
    assert bundle.input_context.participants[0].participant_id == "세라"
    assert bundle.input_context.participants[0].name_en == "Sera"
    assert any(
        fact.statement == source.context.summary
        and fact.evidence_ids == ("source-ass",)
        for fact in bundle.input_context.facts
    )
    assert MigrationWarningCode.PROGRAM_INFERRED_FROM_SUMMARY in _warning_codes(bundle)
    assert MigrationWarningCode.ABSOLUTE_EVIDENCE_RETAINED in _warning_codes(bundle)
    assert bundle.receipt.validation.source_input_valid
    assert bundle.receipt.validation.translation_input_valid is None
    assert bundle.receipt.validation.canonical_outputs_valid
    assert bundle.receipt.validation.source_cue_count_before == FIXTURE_CUE_COUNT
    assert bundle.receipt.validation.source_cue_count_after == FIXTURE_CUE_COUNT
    assert bundle.receipt.validation.target_cue_count_before is None
    assert bundle.receipt.validation.target_cue_count_after is None
    verify_canonical_digests(bundle.input_context)
    verify_canonical_digests(bundle.source)


def test_evidence_rebase_requires_digest_verified_resources(tmp_path: Path) -> None:
    """Absolute evidence becomes relative only after every resource digest matches."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    output_dir = tmp_path / "project"

    bundle = migrate_v1_project(
        source,
        output_dir=output_dir,
        evidence_root=evidence_root,
    )

    expected = Path("..") / "evidence"
    assert bundle.source.evidence.base_uri == expected.as_posix() + "/"
    assert bundle.input_context.evidence.base_uri == expected.as_posix() + "/"
    assert MigrationWarningCode.EVIDENCE_REBASED in _warning_codes(bundle)
    assert MigrationWarningCode.ABSOLUTE_EVIDENCE_RETAINED not in _warning_codes(bundle)

    _ = (evidence_root / "transcript.ass").write_text("tampered")
    with pytest.raises(LegacyMigrationError, match="digest mismatch"):
        _ = migrate_v1_project(
            source,
            output_dir=output_dir,
            evidence_root=evidence_root,
        )


def test_translation_migration_preserves_lineage_and_retained_context(
    tmp_path: Path,
) -> None:
    """Finalized v1 translation becomes one fully bound finalized v2 revision."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source)

    bundle = migrate_v1_project(
        source,
        translation,
        output_dir=tmp_path / "project",
        evidence_root=evidence_root,
    )

    assert bundle.translation is not None
    assert bundle.prepared_context is not None
    assert bundle.glossary is not None
    migrated = bundle.translation
    assert migrated.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert migrated.finalization is not None
    assert (
        migrated.finalization.review_acknowledgement
        is ReviewAcknowledgement.LEGACY_STATUS_IMPORTED
    )
    assert [cue.cue_id for cue in migrated.cues] == [
        cue.cue_id for cue in translation.cues
    ]
    assert [cue.source_cue_ids for cue in migrated.cues] == [
        cue.source_cue_ids for cue in translation.cues
    ]
    assert all(cue.lineage_status is LineageStatus.CONFIRMED for cue in migrated.cues)
    assert bundle.prepared_context.stage is ContextStage.PREPARED
    retained = {fact.fact_id: fact for fact in bundle.prepared_context.facts}
    assert retained["context-welcome"].statement == "진행자가 게스트를 환영한다."
    assert migrated.cues[0].context_ids == ("context-welcome",)
    assert bundle.glossary.entries == ()
    assert bundle.glossary.lifecycle_status is AssetLifecycleStatus.READY
    assert migrated.source_ref.content_digest == bundle.source.content_digest
    assert (
        migrated.prepared_context_ref.content_digest
        == bundle.prepared_context.content_digest
    )
    assert migrated.glossary_ref.content_digest == bundle.glossary.content_digest
    assert bundle.receipt.validation.source_input_valid
    assert bundle.receipt.validation.translation_input_valid
    assert bundle.receipt.validation.canonical_outputs_valid
    assert bundle.receipt.validation.source_cue_count_before == FIXTURE_CUE_COUNT
    assert bundle.receipt.validation.source_cue_count_after == FIXTURE_CUE_COUNT
    assert bundle.receipt.validation.target_cue_count_before == FIXTURE_CUE_COUNT
    assert bundle.receipt.validation.target_cue_count_after == FIXTURE_CUE_COUNT
    for artifact in (
        bundle.prepared_context,
        bundle.glossary,
        migrated,
    ):
        verify_canonical_digests(artifact)


def test_needs_confirmation_migration_preserves_human_decision(tmp_path: Path) -> None:
    """Legacy unresolved choices map to orthogonal draft and decision states."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source, status=DocumentStatus.NEEDS_CONFIRMATION)

    bundle = migrate_v1_project(
        source,
        translation,
        output_dir=tmp_path / "project",
        evidence_root=evidence_root,
    )

    assert bundle.translation is not None
    migrated = bundle.translation
    assert migrated.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert migrated.decision_status.value == "needs_confirmation"
    assert migrated.finalization is None
    decision = migrated.unresolved_decisions[0]
    assert decision.decision_id == "decision-rina"
    assert decision.domain is ReviewDomain.CULTURE
    assert decision.question == "이 cue에서 Rina를 사용할까요?"
    assert {option.option_id for option in decision.options} == {"athena", "rina"}
    assert decision.recommendation_id == "rina"


def test_draft_source_remains_editable_after_migration(tmp_path: Path) -> None:
    """Legacy draft maturity maps to draft source and input-context lifecycles."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root, maturity=SourceMaturity.DRAFT)

    bundle = migrate_v1_project(source, output_dir=tmp_path / "project")

    assert bundle.source.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert bundle.source.finalization is None
    assert bundle.input_context.lifecycle_status is AssetLifecycleStatus.DRAFT


def test_migration_is_deterministic_and_does_not_mutate_v1_models(
    tmp_path: Path,
) -> None:
    """Explicit revision rules make repeated migration byte-identical."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source)
    source_before = source.model_dump_json()
    translation_before = translation.model_dump_json()

    first = migrate_v1_project(
        source,
        translation,
        output_dir=tmp_path / "project",
        evidence_root=evidence_root,
    )
    second = migrate_v1_project(
        source,
        translation,
        output_dir=tmp_path / "project",
        evidence_root=evidence_root,
    )

    assert first == second
    assert source.model_dump_json() == source_before
    assert translation.model_dump_json() == translation_before


def test_write_migration_project_is_atomic_and_idempotent(tmp_path: Path) -> None:
    """A complete project appears together and identical reruns are no-ops."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source)
    output_dir = tmp_path / "project"
    bundle = migrate_v1_project(
        source,
        translation,
        output_dir=output_dir,
        evidence_root=evidence_root,
    )

    write_migration_project(bundle, output_dir)
    first_bytes = {
        path.name: path.read_bytes() for path in sorted(output_dir.iterdir())
    }
    write_migration_project(bundle, output_dir)

    assert first_bytes == {
        path.name: path.read_bytes() for path in sorted(output_dir.iterdir())
    }
    assert set(first_bytes) == {
        "context.input.json",
        "context.prepared.json",
        "glossary.json",
        "migration-receipt.json",
        "source.json",
        "translation.json",
    }
    assert (
        SourceTranscriptV2.model_validate_json((output_dir / "source.json").read_text())
        == bundle.source
    )
    assert (
        TranslationDocumentV2.model_validate_json(
            (output_dir / "translation.json").read_text()
        )
        == bundle.translation
    )

    changed = SourceTranscriptV2.model_validate_json(
        (output_dir / "source.json").read_text()
    ).model_copy(update={"document_id": "tampered"})
    _ = (output_dir / "source.json").write_text(changed.model_dump_json())
    with pytest.raises(FileExistsError, match="different migration project"):
        write_migration_project(bundle, output_dir)
    assert (
        SourceTranscriptV2.model_validate_json(
            (output_dir / "source.json").read_text()
        ).document_id
        == "tampered"
    )


def test_migration_rejects_mismatched_or_invalid_v1_inputs(tmp_path: Path) -> None:
    """Bad source identity and bad legacy digests fail before any project is written."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source)
    wrong_source = bind_source_digest(
        source.model_copy(update={"document_id": "other-source"})
    )
    invalid_source = source.model_copy(update={"content_digest": "f" * 64})

    with pytest.raises(LegacyMigrationError, match="source identity"):
        _ = migrate_v1_project(
            wrong_source,
            translation,
            output_dir=tmp_path / "wrong",
        )
    with pytest.raises(LegacyMigrationError, match="source digest"):
        _ = migrate_v1_project(invalid_source, output_dir=tmp_path / "invalid")
    assert not (tmp_path / "wrong").exists()
    assert not (tmp_path / "invalid").exists()


def test_cli_migrates_v1_project_and_emits_receipt(tmp_path: Path) -> None:
    """The public command reads v1 JSON and writes the canonical project atomically."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    translation = _translation(source)
    source_path = tmp_path / "source-v1.json"
    translation_path = tmp_path / "translation-v1.json"
    _ = source_path.write_text(source.model_dump_json(indent=2))
    _ = translation_path.write_text(translation.model_dump_json(indent=2))
    output_dir = tmp_path / "project"

    result = runner.invoke(
        app,
        [
            "migrate-project",
            str(source_path),
            "--translation",
            str(translation_path),
            "--output-dir",
            str(output_dir),
            "--evidence-root",
            str(evidence_root),
        ],
    )

    assert result.exit_code == 0, result.output
    receipt = CanonicalMigrationReceipt.model_validate_json(result.stdout)
    assert receipt.schema_version == "canonical-migration-receipt.v1"
    assert receipt.source_input_digest == source.content_digest
    assert receipt.translation_input_digest == translation.content_digest
    assert receipt.validation.model_dump(mode="json") == {
        "source_input_valid": True,
        "translation_input_valid": True,
        "canonical_outputs_valid": True,
        "source_cue_count_before": FIXTURE_CUE_COUNT,
        "source_cue_count_after": FIXTURE_CUE_COUNT,
        "target_cue_count_before": FIXTURE_CUE_COUNT,
        "target_cue_count_after": FIXTURE_CUE_COUNT,
        "evidence_entry_count_before": FIXTURE_EVIDENCE_COUNT,
        "evidence_entry_count_after": FIXTURE_EVIDENCE_COUNT,
    }
    assert receipt == CanonicalMigrationReceipt.model_validate_json(
        (output_dir / "migration-receipt.json").read_text()
    )


def test_cli_failure_leaves_no_partial_output(tmp_path: Path) -> None:
    """Evidence verification failure occurs before the output directory is committed."""
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source = _source(evidence_root)
    source_path = tmp_path / "source-v1.json"
    _ = source_path.write_text(source.model_dump_json(indent=2))
    _ = (evidence_root / "transcript.ass").write_text("tampered")
    output_dir = tmp_path / "project"

    result = runner.invoke(
        app,
        [
            "migrate-project",
            str(source_path),
            "--output-dir",
            str(output_dir),
            "--evidence-root",
            str(evidence_root),
        ],
    )

    assert result.exit_code == CLI_ERROR_EXIT
    assert "digest mismatch" in result.stderr
    assert not output_dir.exists()
