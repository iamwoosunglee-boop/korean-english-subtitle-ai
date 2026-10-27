# Copyright 2026 Transcription Module contributors

"""Path confinement for explicit frame opening."""

from pathlib import Path

import pytest

from tests.translation.evidence_open_support import (
    FRAME_A,
    candidate_fixture,
    evidence_context,
    source_fixture,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_open_models import EvidenceActorRole, EvidenceOpenError
from translation_module.evidence_open_store import EvidenceOpenStore


def _store(root: Path, records: Path, locator: str) -> EvidenceOpenStore:
    source = source_fixture()
    first = source.evidence.entries[0].model_copy(update={"locator": locator})
    source = bind_canonical_digests(
        source.model_copy(
            update={
                "evidence": source.evidence.model_copy(
                    update={
                        "base_uri": root.as_uri(),
                        "entries": (first, *source.evidence.entries[1:]),
                    }
                )
            }
        )
    )
    candidate = candidate_fixture(source)
    return EvidenceOpenStore(
        records,
        evidence_context(source, candidate),
        FileEvidenceResolver(root),
    )


def test_open_frame_rejects_parent_symlink_escape(tmp_path: Path) -> None:
    """A symlinked parent cannot redirect the explicit frame read outside root."""
    root, outside, records = tmp_path / "root", tmp_path / "outside", tmp_path / "opens"
    root.mkdir()
    outside.mkdir()
    _ = (outside / "a.jpg").write_bytes(FRAME_A)
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(EvidenceOpenError, match="unavailable"):
        _ = _store(root, records, "escape/a.jpg").open_frame(
            "frame-a", EvidenceActorRole.REVIEWER, "review-1"
        )
    assert not records.exists()


def test_open_frame_rejects_final_symlink(tmp_path: Path) -> None:
    """A final symlink cannot stand in for the declared regular frame file."""
    root, outside, records = tmp_path / "root", tmp_path / "outside", tmp_path / "opens"
    root.mkdir()
    outside.mkdir()
    target = outside / "a.jpg"
    _ = target.write_bytes(FRAME_A)
    (root / "a.jpg").symlink_to(target)

    with pytest.raises(EvidenceOpenError, match="unavailable"):
        _ = _store(root, records, "a.jpg").open_frame(
            "frame-a", EvidenceActorRole.REVIEWER, "review-1"
        )
    assert not records.exists()
