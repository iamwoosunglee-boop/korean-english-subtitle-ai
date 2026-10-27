# Copyright 2026 Transcription Module contributors

"""Pure lexical admission tests for CLI model output targets."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from translation_module.cli_io import write_models_atomic


class Payload(BaseModel):
    """Serializable lexical-admission fixture."""

    value: str


@pytest.mark.parametrize(
    "raw",
    ["C:/authority", "C:relative", "//server/share"],
)
@pytest.mark.parametrize("root_exists", [False, True])
def test_drive_unc_and_non_json_leaf_reject_before_mutation(
    tmp_path: Path,
    raw: str,
    *,
    root_exists: bool,
) -> None:
    """Drive, UNC, and normalized non-JSON leaves never mutate roots."""
    root = tmp_path / "root"
    if root_exists:
        root.mkdir()
        _ = (root / "marker").write_bytes(b"marker")
    before = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in tmp_path.rglob("*")
    )
    target = Path(raw) if raw.startswith("//") else root / raw
    with pytest.raises(OSError, match="invalid CLI output path"):
        write_models_atomic(((target, Payload(value="blocked")),))
    after = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in tmp_path.rglob("*")
    )
    assert after == before


@pytest.mark.parametrize("root_exists", [False, True])
def test_terminal_dot_normalization_cannot_publish_parent_as_file(
    tmp_path: Path,
    *,
    root_exists: bool,
) -> None:
    """A terminal dot collapsed by Path still fails the JSON leaf contract."""
    root = tmp_path / "root"
    if root_exists:
        root.mkdir()
    target = root / "final-dot" / "."
    with pytest.raises(OSError, match="invalid CLI output path"):
        write_models_atomic(((target, Payload(value="blocked")),))
    assert not (root / "final-dot").exists()


@pytest.mark.parametrize(
    "character",
    ["\x7f", "\x85", "\u200b", "\ud800", "\ue000", "\u0378"],
)
@pytest.mark.parametrize("root_exists", [False, True])
def test_unicode_other_categories_reject_before_root_mutation(
    tmp_path: Path,
    character: str,
    *,
    root_exists: bool,
) -> None:
    """Cc, Cf, Cs, Co, and Cn components never reach filesystem handling."""
    root = tmp_path / "root"
    if root_exists:
        root.mkdir()
        _ = (root / "marker").write_bytes(b"marker")
    before = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in tmp_path.rglob("*")
    )
    target = root / f"segment{character}" / "output.json"
    with pytest.raises(OSError, match="invalid CLI output path"):
        write_models_atomic(((target, Payload(value="blocked")),))
    after = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in tmp_path.rglob("*")
    )
    assert after == before


@pytest.mark.parametrize(
    "component",
    [
        "COM¹",
        "com².txt",
        "LPT³",
        "\uff23\uff2f\uff2d\uff11",
        "\uff4c\uff50\uff54\uff19.log",
        "CoM¹. ",
    ],
)
@pytest.mark.parametrize("root_exists", [False, True])
def test_nfkc_dos_device_alias_rejects_before_mutation(
    tmp_path: Path,
    component: str,
    *,
    root_exists: bool,
) -> None:
    """Compatibility device aliases cannot enter an output authority path."""
    root = tmp_path / "root"
    if root_exists:
        root.mkdir()
        _ = (root / "marker").write_bytes(b"marker")
    before = tuple(
        (path.relative_to(tmp_path), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )
    with pytest.raises(OSError, match="invalid CLI output path"):
        write_models_atomic(
            ((root / component / "output.json", Payload(value="blocked")),)
        )
    after = tuple(
        (path.relative_to(tmp_path), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )
    assert after == before


@pytest.mark.parametrize(
    "name",
    [
        f".cli-io-stage.{'A' * 64}.{'b' * 64}.tmp",
        f".CLI-IO-STAGE.{'a' * 64}.{'b' * 64}.tmp",
        f".cli-io-stage.{'g' * 64}.{'b' * 64}.tmp",
        f".cli-io-stage.{'a' * 63}.{'b' * 64}.tmp",
        f".cli-io-stage.{'a' * 64}.extra.{'b' * 64}.tmp",
        f".cli-io-wal.{'A' * 64}.tmp",
        f".cli-io-wal.{'a' * 63}.tmp",
        f".cli-io-rollback.{'A' * 64}.{'b' * 64}.tmp",
        ".cli-io-internal.foreign.tmp",
    ],
)
def test_malformed_internal_names_are_conflicts_not_orphans(
    tmp_path: Path,
    name: str,
) -> None:
    """Malformed reserved names remain byte-for-byte untouched on rejection."""
    output = tmp_path / "output.json"
    malformed = tmp_path / name
    _ = output.write_bytes(b"old")
    _ = malformed.write_bytes(b"foreign")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(OSError, match="foreign CLI staging name"):
        write_models_atomic(((output, Payload(value="new")),))
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
