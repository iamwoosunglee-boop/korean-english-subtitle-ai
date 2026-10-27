# Copyright 2026 Transcription Module contributors

"""Selected-attempt canonical delivery parity."""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.translation.canonical_delivery_support import (
    REVIEW_TIMECODE,
    delivery_case,
    stored_bytes,
)
from tests.translation.canonical_preflight_support import (
    preflight_attempt,
    preflight_context,
)
from translation_module.canonical._delivery_formats import csv_projection
from translation_module.canonical.delivery import (
    DELIVERY_CSV_NAME,
    DELIVERY_JSON_NAME,
    DELIVERY_PARITY_NAME,
    DELIVERY_SBV_NAME,
    DeliveryParityReport,
    DeliveryPayloads,
    assemble_selected_delivery,
    verify_delivery_bytes,
)
from translation_module.canonical.translation import TranslationDocumentV2

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2


def test_selected_draft_cannot_be_delivered(tmp_path: Path) -> None:
    """Selected authority alone cannot publish a translation draft."""
    case = delivery_case(tmp_path / "authority")
    output_dir = tmp_path / "delivery"
    authority_before = stored_bytes(case.workflow.root)

    with pytest.raises(ValueError, match=r"."):
        _ = assemble_selected_delivery(
            case.workflow.root,
            case.identity,
            case.dependencies,
            output_dir,
            finalized_document=case.candidate,
        )

    assert not output_dir.exists()
    assert stored_bytes(case.workflow.root) == authority_before


def test_selected_attempt_assembles_json_csv_sbv_with_parity(
    tmp_path: Path,
) -> None:
    """One immutable selection produces all delivery formats and rationales."""
    case = delivery_case(tmp_path / "authority")
    authority_before = stored_bytes(case.workflow.root)
    output_dir = tmp_path / "delivery"

    report = assemble_selected_delivery(
        case.workflow.root,
        case.identity,
        case.dependencies,
        output_dir,
        finalized_document=case.finalized,
    )

    delivered = TranslationDocumentV2.model_validate_json(
        (output_dir / DELIVERY_JSON_NAME).read_bytes()
    )
    csv_rows = tuple(
        csv.DictReader(
            io.StringIO(
                (output_dir / DELIVERY_CSV_NAME).read_text(encoding="utf-8-sig")
            )
        )
    )
    persisted_report = DeliveryParityReport.model_validate_json(
        (output_dir / DELIVERY_PARITY_NAME).read_bytes()
    )
    sbv = (output_dir / DELIVERY_SBV_NAME).read_text(encoding="utf-8")

    assert report == persisted_report
    assert report.matches
    assert delivered == case.finalized
    assert delivered.record_digest != case.candidate.record_digest
    assert delivered.content_digest == case.candidate.content_digest
    assert tuple(csv_rows[0]) == (
        "sequence",
        "start_tc",
        "end_tc",
        "speakers",
        "source_text",
        "target_text",
        "source_context",
        "rationales_ko",
    )
    assert tuple(row["start_tc"] for row in csv_rows) == ("00:00:00;00",)
    assert tuple(row["end_tc"] for row in csv_rows) == ("00:00:01;00",)
    assert all(
        row["source_text"] == case.dependencies.source.cues[0].text for row in csv_rows
    )
    assert all(row["rationales_ko"] for row in csv_rows)
    assert tuple(cue.text for cue in case.finalized.cues) == tuple(
        block.splitlines()[1] for block in sbv.strip().split("\n\n")
    )
    assert stored_bytes(case.workflow.root) == authority_before


def test_review_csv_includes_source_context_by_lineage() -> None:
    """Review rows expose source notes without internal target identifiers."""
    context = preflight_context()
    source_cue = context.source.cues[0].model_copy(
        update={"context_note": "장면별 원문 컨텍스트"}
    )
    source = context.source.model_copy(update={"cues": (source_cue,)})
    attempt = preflight_attempt(context)

    rows = csv_projection(
        context.candidate,
        attempt,
        source,
        REVIEW_TIMECODE,
    )

    assert rows[0][4] == source_cue.text
    assert rows[0][6] == "장면별 원문 컨텍스트"


def test_review_csv_uses_drop_frame_labels_after_first_minute() -> None:
    """29.97 drop-frame TC skips frame labels 00 and 01 each minute."""
    context = preflight_context()
    cue = context.candidate.cues[0].model_copy(
        update={"start_ms": 60_060, "end_ms": 60_093}
    )
    candidate = context.candidate.model_copy(update={"cues": (cue,)})
    attempt = preflight_attempt(context)

    rows = csv_projection(
        candidate,
        attempt,
        context.source,
        REVIEW_TIMECODE,
    )

    assert rows[0][1] == "00:01:00;02"


def test_tampered_delivery_bytes_produce_failed_parity(tmp_path: Path) -> None:
    """A changed SBV cannot retain a successful machine-readable parity result."""
    case = delivery_case(tmp_path / "authority")
    output_dir = tmp_path / "delivery"
    _ = assemble_selected_delivery(
        case.workflow.root,
        case.identity,
        case.dependencies,
        output_dir,
        finalized_document=case.finalized,
    )
    tampered_sbv = (
        (output_dir / DELIVERY_SBV_NAME)
        .read_bytes()
        .replace(
            case.finalized.cues[0].text.encode(),
            b"tampered",
            1,
        )
    )
    receipt = case.workflow.attempt_store.require_selected_receipt(case.candidate)
    attempt = case.workflow.attempt_store.load()[0]

    report = verify_delivery_bytes(
        case.finalized,
        attempt,
        receipt,
        DeliveryPayloads(
            json=(output_dir / DELIVERY_JSON_NAME).read_bytes(),
            csv=(output_dir / DELIVERY_CSV_NAME).read_bytes(),
            sbv=tampered_sbv,
        ),
        case.dependencies,
    )

    assert not report.matches
    assert not report.sbv_matches


@pytest.mark.parametrize("draft", [False, True])
def test_delivery_cli_runs_selected_attempt_to_all_formats(
    tmp_path: Path,
    *,
    draft: bool,
) -> None:
    """The real command delivers the final record, never its selected draft."""
    case = delivery_case(tmp_path / "authority")
    authority_before = stored_bytes(case.workflow.root)
    source_path = tmp_path / "source.json"
    glossary_path = tmp_path / "glossary.json"
    timecode_path = tmp_path / "timecode.json"
    context_path = tmp_path / "context.json"
    review_path = tmp_path / "review.json"
    finalized_path = tmp_path / "finalized.json"
    output_dir = tmp_path / "delivery"
    for path, model in (
        (source_path, case.dependencies.source),
        (glossary_path, case.dependencies.glossary),
        (context_path, case.dependencies.context),
        (review_path, case.dependencies.reviews[0]),
        (finalized_path, case.candidate if draft else case.finalized),
    ):
        _ = path.write_text(model.model_dump_json(), encoding="utf-8")
    _ = timecode_path.write_text(
        json.dumps(
            {
                "frame_rate_numerator": 30_000,
                "frame_rate_denominator": 1_001,
                "drop_frame": True,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "assemble-delivery",
            str(finalized_path),
            str(source_path),
            str(context_path),
            str(glossary_path),
            str(review_path),
            str(timecode_path),
            "--authority-root",
            str(case.workflow.root),
            "--output-dir",
            str(output_dir),
            "--review-profile",
            case.dependencies.review_profile.profile_id,
            case.dependencies.review_profile.version,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if draft:
        assert result.returncode == CLI_ERROR_EXIT, result.stderr
        assert json.loads(result.stderr)["error"]["code"] == "delivery_assembly_failed"
        assert not output_dir.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["matches"] is True
        assert {path.name for path in output_dir.iterdir()} == {
            DELIVERY_JSON_NAME,
            DELIVERY_CSV_NAME,
            DELIVERY_SBV_NAME,
            DELIVERY_PARITY_NAME,
        }
        assert (
            TranslationDocumentV2.model_validate_json(
                (output_dir / DELIVERY_JSON_NAME).read_bytes()
            )
            == case.finalized
        )
    assert stored_bytes(case.workflow.root) == authority_before
