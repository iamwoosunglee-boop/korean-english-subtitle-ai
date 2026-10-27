# Copyright 2026 Transcription Module contributors

"""Prepared context and glossary workflow tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from translation_module.canonical import (
    AssetLifecycleStatus,
    CanonicalDiagnosticCode,
    CanonicalFinalizationError,
    ContentContextAsset,
    ContextParticipant,
    ContextStage,
    GlossaryDecisionStatus,
    GlossaryEntry,
    GlossaryEntryType,
    SourceCueV2,
    SourceFinalization,
    SourceTranscriptV2,
    TranslationGlossaryAsset,
    bind_canonical_digests,
    finalize_source,
    ready_glossary_asset,
    resolve_glossary_for_cue,
    verify_canonical_digests,
)
from translation_module.canonical.asset_preparation import (
    AssetPreparationError,
    prepare_translation_assets,
)
from translation_module.cli import app
from translation_module.common import ContentKind

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
runner = CliRunner()
CLI_ERROR_EXIT = 2


def _input_context() -> ContentContextAsset:
    return ContentContextAsset.model_validate_json(
        (EXAMPLES / "content-context.input.v1.example.json").read_bytes()
    )


def _source_draft() -> SourceTranscriptV2:
    return SourceTranscriptV2.model_validate_json(
        (EXAMPLES / "source-transcript.v2.example.json").read_bytes()
    )


def _finalized_source(
    *,
    context: ContentContextAsset | None = None,
    cues: tuple[SourceCueV2, ...] | None = None,
) -> tuple[SourceTranscriptV2, ContentContextAsset]:
    input_context = context or _input_context()
    source = _source_draft()
    updates: dict[str, object] = {
        "input_context_ref": source.input_context_ref.model_copy(
            update={
                "asset_id": input_context.asset_id,
                "revision_id": input_context.revision_id,
                "content_digest": input_context.content_digest,
            }
        )
    }
    if cues is not None:
        updates.update(
            {
                "duration_ms": max(cue.end_ms for cue in cues),
                "cues": cues,
            }
        )
    source = bind_canonical_digests(source.model_copy(update=updates))
    finalized = finalize_source(
        source,
        SourceFinalization(
            finalized_at="2026-08-08T00:00:00Z",
            finalized_by="human-editor",
        ),
        input_context=input_context,
    )
    return finalized, input_context


def _fifty_source() -> tuple[SourceTranscriptV2, ContentContextAsset]:
    input_context = _input_context()
    participants = (
        *input_context.participants,
        ContextParticipant(
            participant_id="sera",
            name_ko="세라",
            name_en="Sera",
            aliases=("세라",),
            role="NOVA NOVA member",
            evidence_ids=(),
        ),
        ContextParticipant(
            participant_id="athena",
            name_ko="아리나",
            name_en="Arina",
            aliases=("아리나", "리나"),
            role="NOVA NOVA member",
            evidence_ids=(),
        ),
    )
    input_context = bind_canonical_digests(
        input_context.model_copy(update={"participants": participants})
    )
    cues = (
        SourceCueV2(
            sequence=1,
            cue_id="source-city",
            start_ms=0,
            end_ms=1_000,
            text="저희 도시의 올스타 팀에 들어갔어요.",
            speakers=("문하린",),
            content_kind=ContentKind.DIALOGUE,
            context_note="사용자는 이 범위에서 도시를 LA로 특정했다.",
            evidence_ids=(),
        ),
        SourceCueV2(
            sequence=2,
            cue_id="source-rina",
            start_ms=1_000,
            end_ms=2_000,
            text="리나, 축하해!",
            speakers=("세라",),
            content_kind=ContentKind.DIALOGUE,
            evidence_ids=(),
        ),
        SourceCueV2(
            sequence=3,
            cue_id="source-gae-pala",
            start_ms=2_000,
            end_ms=3_000,
            text="개팔아!",
            speakers=("한지아",),
            content_kind=ContentKind.DIALOGUE,
            context_note="발성 연습용 음가이며 의미 번역하지 않는다.",
            evidence_ids=(),
        ),
    )
    return _finalized_source(context=input_context, cues=cues)


def _entry(
    *,
    entry_id: str,
    source_variant: str,
    target: str,
    entry_type: GlossaryEntryType,
    **overrides: object,
) -> GlossaryEntry:
    scope = overrides.pop("scope", ())
    status = overrides.pop("status", GlossaryDecisionStatus.VERIFIED)
    payload: dict[str, object] = {
        "entry_id": entry_id,
        "source_variants": [source_variant],
        "preferred_target": target,
        "type": entry_type,
        "scope_source_cue_ids": scope,
        "decision_status": status,
        "blocking": False,
        "case_sensitive": True,
        "evidence_ids": [],
    }
    payload.update(overrides)
    return GlossaryEntry.model_validate(payload)


def test_prepare_assets_bind_finalized_source_and_seed_reusable_context() -> None:
    """Preparation copies trusted context, source notes, and official names."""
    source, input_context = _fifty_source()

    bundle = prepare_translation_assets(
        source,
        input_context,
        source_path="../source/source.json",
    )

    assert bundle.context.stage is ContextStage.PREPARED
    assert bundle.context.lifecycle_status is AssetLifecycleStatus.DRAFT
    assert bundle.glossary.lifecycle_status is AssetLifecycleStatus.DRAFT
    assert bundle.context.prepared_from_source_ref is not None
    assert bundle.context.prepared_from_source_ref.document_id == source.document_id
    assert bundle.context.prepared_from_source_ref.revision_id == source.revision_id
    assert (
        bundle.context.prepared_from_source_ref.content_digest == source.content_digest
    )
    assert bundle.context.prepared_from_source_ref.path == "../source/source.json"
    assert bundle.glossary.prepared_from_source_ref == (
        bundle.context.prepared_from_source_ref
    )
    assert {
        participant.participant_id for participant in bundle.context.participants
    } >= {
        "sera",
        "athena",
    }
    notes = {
        (fact.statement, fact.scope_source_cue_ids) for fact in bundle.context.facts
    }
    assert (
        "사용자는 이 범위에서 도시를 LA로 특정했다.",
        ("source-city",),
    ) in notes
    assert (
        "발성 연습용 음가이며 의미 번역하지 않는다.",
        ("source-gae-pala",),
    ) in notes
    names = {entry.entry_id: entry for entry in bundle.glossary.entries}
    assert names["participant:sera:name"].preferred_target == "Sera"
    assert names["participant:athena:name"].preferred_target == "Arina"
    assert names["participant:athena:name"].source_variants == ("아리나",)
    verify_canonical_digests(bundle.context)
    verify_canonical_digests(bundle.glossary)


def test_prepare_assets_rejects_finalized_source_with_unknown_speaker() -> None:
    """Migrated source authority cannot carry unresolved speaker placeholders."""
    source, input_context = _finalized_source()
    cue = source.cues[0].model_copy(update={"speakers": ("unknown",)})
    source = bind_canonical_digests(source.model_copy(update={"cues": (cue,)}))

    with pytest.raises(AssetPreparationError, match="unresolved speaker"):
        _ = prepare_translation_assets(source, input_context)


def test_prepare_assets_reject_draft_source_and_wrong_input_context() -> None:
    """Preparation cannot detach assets from the exact finalized source provenance."""
    source = _source_draft()
    input_context = _input_context()

    with pytest.raises(AssetPreparationError, match="finalized"):
        _ = prepare_translation_assets(source, input_context)

    finalized, _ = _finalized_source()
    wrong_context = bind_canonical_digests(
        input_context.model_copy(update={"asset_id": "wrong-context"})
    )
    with pytest.raises(AssetPreparationError, match="input context"):
        _ = prepare_translation_assets(finalized, wrong_context)


def test_glossary_resolution_prefers_specific_human_selected_decisions() -> None:
    """LA, Rina, and Gae-pala choices override only their exact term and scope."""
    source, input_context = _fifty_source()
    bundle = prepare_translation_assets(source, input_context)
    entries = (
        _entry(
            entry_id="city-global",
            source_variant="저희 도시",
            target="my city",
            entry_type=GlossaryEntryType.CONTEXTUAL_TRANSLATION,
        ),
        _entry(
            entry_id="city-la",
            source_variant="저희 도시",
            target="LA",
            entry_type=GlossaryEntryType.CONTEXTUAL_TRANSLATION,
            scope=("source-city",),
            status=GlossaryDecisionStatus.HUMAN_SELECTED,
        ),
        _entry(
            entry_id="rina",
            source_variant="리나",
            target="Rina",
            entry_type=GlossaryEntryType.NICKNAME,
            scope=("source-rina",),
            status=GlossaryDecisionStatus.HUMAN_SELECTED,
        ),
        _entry(
            entry_id="gae-pala-literal",
            source_variant="개팔아",
            target="Sell dogs",
            entry_type=GlossaryEntryType.PHONETIC_EXPRESSION,
        ),
        _entry(
            entry_id="gae-pala",
            source_variant="개팔아",
            target="Gae-pala",
            entry_type=GlossaryEntryType.PHONETIC_EXPRESSION,
            scope=("source-gae-pala",),
            status=GlossaryDecisionStatus.HUMAN_SELECTED,
            instruction="Do not translate semantically.",
            blocking=True,
        ),
    )
    glossary = bind_canonical_digests(
        bundle.glossary.model_copy(update={"entries": entries})
    )
    source_by_id = {cue.cue_id: cue for cue in source.cues}

    city = resolve_glossary_for_cue(source_by_id["source-city"], glossary)
    rina = resolve_glossary_for_cue(source_by_id["source-rina"], glossary)
    gae_pala = resolve_glossary_for_cue(source_by_id["source-gae-pala"], glossary)

    assert {item.entry_id: item.preferred_target for item in city} == {"city-la": "LA"}
    assert {item.entry_id: item.preferred_target for item in rina} == {"rina": "Rina"}
    assert {item.entry_id: item.preferred_target for item in gae_pala} == {
        "gae-pala": "Gae-pala"
    }


def test_equal_priority_glossary_conflict_blocks_ready_transition() -> None:
    """Two contradictory decisions at the same scope and authority cannot be ready."""
    source, input_context = _fifty_source()
    bundle = prepare_translation_assets(source, input_context)
    glossary = bind_canonical_digests(
        bundle.glossary.model_copy(
            update={
                "entries": (
                    _entry(
                        entry_id="rina-a",
                        source_variant="리나",
                        target="Rina",
                        entry_type=GlossaryEntryType.NICKNAME,
                        scope=("source-rina",),
                        status=GlossaryDecisionStatus.HUMAN_SELECTED,
                    ),
                    _entry(
                        entry_id="rina-b",
                        source_variant="리나",
                        target="Thena",
                        entry_type=GlossaryEntryType.NICKNAME,
                        scope=("source-rina",),
                        status=GlossaryDecisionStatus.HUMAN_SELECTED,
                    ),
                )
            }
        )
    )

    with pytest.raises(CanonicalFinalizationError) as error:
        _ = ready_glossary_asset(glossary, source=source)

    assert CanonicalDiagnosticCode.GLOSSARY_CONFLICT in {
        item.code for item in error.value.report.diagnostics
    }


def test_disjoint_scopes_are_not_glossary_conflicts() -> None:
    """The same variant may intentionally map differently in separate cue scopes."""
    source, input_context = _fifty_source()
    bundle = prepare_translation_assets(source, input_context)
    glossary = bind_canonical_digests(
        bundle.glossary.model_copy(
            update={
                "entries": (
                    _entry(
                        entry_id="term-city",
                        source_variant="말",
                        target="word",
                        entry_type=GlossaryEntryType.CONTEXTUAL_TRANSLATION,
                        scope=("source-city",),
                    ),
                    _entry(
                        entry_id="term-rina",
                        source_variant="말",
                        target="horse",
                        entry_type=GlossaryEntryType.CONTEXTUAL_TRANSLATION,
                        scope=("source-rina",),
                    ),
                )
            }
        )
    )

    ready = ready_glossary_asset(glossary, source=source)

    assert ready.lifecycle_status is AssetLifecycleStatus.READY


def test_ready_rejects_stale_source_scope_explicitly() -> None:
    """A cue scope from another source revision is reported rather than ignored."""
    source, input_context = _fifty_source()
    bundle = prepare_translation_assets(source, input_context)
    glossary = bind_canonical_digests(
        bundle.glossary.model_copy(
            update={
                "entries": (
                    _entry(
                        entry_id="stale",
                        source_variant="리나",
                        target="Rina",
                        entry_type=GlossaryEntryType.NICKNAME,
                        scope=("removed-source-cue",),
                    ),
                )
            }
        )
    )

    with pytest.raises(CanonicalFinalizationError) as error:
        _ = ready_glossary_asset(glossary, source=source)

    assert CanonicalDiagnosticCode.GLOSSARY_SCOPE_UNKNOWN_SOURCE in {
        item.code for item in error.value.report.diagnostics
    }


def test_prepare_and_ready_assets_cli_round_trip(tmp_path: Path) -> None:
    """The official CLI creates draft assets and atomically promotes both to ready."""
    source, input_context = _fifty_source()
    source_path = tmp_path / "source.json"
    input_path = tmp_path / "context.input.json"
    draft_dir = tmp_path / "draft-assets"
    ready_dir = tmp_path / "ready-assets"
    _ = source_path.write_text(
        source.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _ = input_path.write_text(
        input_context.model_dump_json(indent=2),
        encoding="utf-8",
    )

    prepared = runner.invoke(
        app,
        [
            "prepare-assets",
            str(source_path),
            str(input_path),
            "--output-dir",
            str(draft_dir),
        ],
    )

    assert prepared.exit_code == 0, prepared.output
    context = ContentContextAsset.model_validate_json(
        (draft_dir / "context.prepared.json").read_bytes()
    )
    glossary = TranslationGlossaryAsset.model_validate_json(
        (draft_dir / "glossary.json").read_bytes()
    )
    assert context.lifecycle_status is AssetLifecycleStatus.DRAFT
    assert glossary.lifecycle_status is AssetLifecycleStatus.DRAFT

    promoted = runner.invoke(
        app,
        [
            "ready-assets",
            str(source_path),
            str(draft_dir / "context.prepared.json"),
            str(draft_dir / "glossary.json"),
            "--output-dir",
            str(ready_dir),
        ],
    )

    assert promoted.exit_code == 0, promoted.output
    ready_context = ContentContextAsset.model_validate_json(
        (ready_dir / "context.prepared.json").read_bytes()
    )
    ready_glossary = TranslationGlossaryAsset.model_validate_json(
        (ready_dir / "glossary.json").read_bytes()
    )
    assert ready_context.lifecycle_status is AssetLifecycleStatus.READY
    assert ready_glossary.lifecycle_status is AssetLifecycleStatus.READY
    verify_canonical_digests(ready_context)
    verify_canonical_digests(ready_glossary)


def test_ready_assets_cli_failure_leaves_no_partial_outputs(tmp_path: Path) -> None:
    """A blocking glossary conflict prevents either ready asset from being written."""
    source, input_context = _fifty_source()
    bundle = prepare_translation_assets(source, input_context)
    conflicting = bind_canonical_digests(
        bundle.glossary.model_copy(
            update={
                "entries": (
                    _entry(
                        entry_id="a",
                        source_variant="리나",
                        target="Rina",
                        entry_type=GlossaryEntryType.NICKNAME,
                    ),
                    _entry(
                        entry_id="b",
                        source_variant="리나",
                        target="Thena",
                        entry_type=GlossaryEntryType.NICKNAME,
                    ),
                )
            }
        )
    )
    source_path = tmp_path / "source.json"
    context_path = tmp_path / "context.json"
    glossary_path = tmp_path / "glossary.json"
    output_dir = tmp_path / "ready"
    _ = source_path.write_text(
        source.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _ = context_path.write_text(
        bundle.context.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _ = glossary_path.write_text(
        conflicting.model_dump_json(indent=2),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "ready-assets",
            str(source_path),
            str(context_path),
            str(glossary_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == CLI_ERROR_EXIT
    assert not output_dir.exists()
