# Copyright 2026 Transcription Module contributors

"""Canonical review CLI selected-authority scenarios."""

from pathlib import Path

from typer.testing import CliRunner

from tests.translation.canonical_review_authority_support import review_authority_root
from tests.translation.test_canonical_review_assembly import (
    ReviewFixture,
)
from tests.translation.test_canonical_review_assembly import (
    review_chunk_fixture as _chunk,
)
from tests.translation.test_canonical_review_assembly import (
    review_fixture as _fixture,
)
from tests.translation.test_canonical_review_assembly import (
    review_manifest_fixture as _manifest,
)
from translation_module.canonical import (
    ReviewArtifactResult,
    ReviewAssemblyManifest,
    TranslationReviewArtifact,
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.cli import app

RUNNER = CliRunner()
CLI_ERROR_EXIT = 2


def _write_review_inputs(
    root: Path,
    fixture: ReviewFixture,
    manifest: ReviewAssemblyManifest,
) -> tuple[Path, Path, Path, Path, Path]:
    paths = (
        root / "translation.json",
        root / "source.json",
        root / "context.json",
        root / "glossary.json",
        root / "manifest.json",
    )
    source, context, glossary, translation = fixture
    models = (translation, source, context, glossary, manifest)
    for path, model in zip(paths, models, strict=True):
        _ = path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return paths


def test_assemble_review_cli_writes_bound_review(tmp_path: Path) -> None:
    """The public command writes the exact immutable review artifact."""
    source, context, glossary, translation = _fixture()
    translation = bind_canonical_digests(
        translation.model_copy(
            update={
                "cues": (
                    translation.cues[0].model_copy(update={"text": "Welcome back."}),
                    *translation.cues[1:],
                )
            }
        )
    )
    chunks = (
        _chunk(
            translation,
            chunk_id="chunk-1",
            source_ids=(source.cues[0].cue_id,),
            target_ids=(translation.cues[0].cue_id,),
            execution_id="reviewer-1",
        ),
        _chunk(
            translation,
            chunk_id="chunk-2",
            source_ids=(source.cues[1].cue_id,),
            target_ids=(translation.cues[1].cue_id,),
            execution_id="reviewer-2",
        ),
    )
    paths = _write_review_inputs(
        tmp_path,
        (source, context, glossary, translation),
        _manifest(translation, glossary, chunks),
    )
    output = tmp_path / "review.json"
    authority_root = review_authority_root(tmp_path, source, glossary, translation)

    result = RUNNER.invoke(
        app,
        [
            "assemble-review",
            *(str(path) for path in paths),
            "--output",
            str(output),
            "--authority-root",
            str(authority_root),
        ],
    )

    assert result.exit_code == 0, result.output
    review = TranslationReviewArtifact.model_validate_json(output.read_bytes())
    assert review.result is ReviewArtifactResult.PASSED
    verify_canonical_digests(review)

    linked_root = tmp_path / "review-authority-link"
    linked_root.symlink_to(authority_root, target_is_directory=True)
    linked_output = tmp_path / "linked-review.json"
    linked = RUNNER.invoke(
        app,
        [
            "assemble-review",
            *(str(path) for path in paths),
            "--output",
            str(linked_output),
            "--authority-root",
            str(linked_root),
        ],
    )
    assert linked.exit_code == CLI_ERROR_EXIT
    assert not linked_output.exists()


def test_assemble_review_cli_failure_leaves_no_output(tmp_path: Path) -> None:
    """A stale scoped report fails before the destination file is created."""
    source, context, glossary, translation = _fixture()
    translation = bind_canonical_digests(
        translation.model_copy(
            update={
                "cues": (
                    translation.cues[0].model_copy(update={"text": "Welcome back."}),
                    *translation.cues[1:],
                )
            }
        )
    )
    stale = _chunk(
        translation,
        chunk_id="chunk-1",
        source_ids=(source.cues[0].cue_id,),
        target_ids=(translation.cues[0].cue_id,),
        execution_id="reviewer-1",
    ).model_copy(update={"target_scope_digest": "f" * 64})
    paths = _write_review_inputs(
        tmp_path,
        (source, context, glossary, translation),
        _manifest(translation, glossary, (stale,)),
    )
    output = tmp_path / "review.json"

    result = RUNNER.invoke(
        app,
        [
            "assemble-review",
            *(str(path) for path in paths),
            "--output",
            str(output),
            "--authority-root",
            str(review_authority_root(tmp_path, source, glossary, translation)),
        ],
    )

    assert result.exit_code == CLI_ERROR_EXIT
    assert "scope digest" in result.stderr
    assert not output.exists()
