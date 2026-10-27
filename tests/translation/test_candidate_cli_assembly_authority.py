# Copyright 2026 Transcription Module contributors

"""Legacy candidate CLI attempt authority."""

import subprocess
from pathlib import Path

from tests.translation.candidate_authority_test_support import (
    assembled_candidate_fixture,
    candidate_plans_fixture,
    candidate_source_fixture,
    chunk_candidates_fixture,
)
from translation_module.canonical.attempts import (
    TranslationAttemptStore,
    TranslationLineRationale,
    translation_line_text_digest,
)
from translation_module.canonical.legacy_authority import (
    AttemptRationaleInput,
    project_legacy_candidate,
)
from translation_module.document import TranslationDocument
from translation_module.preparation import TranslationBatchPlan

CLI_ERROR_EXIT = 2


def test_cli_assembly_records_attempt_before_candidate_write(tmp_path: Path) -> None:
    """The real assembly CLI cannot emit a legacy candidate without authority."""
    source = candidate_source_fixture()
    plans = candidate_plans_fixture()
    expected = assembled_candidate_fixture()
    authority_root = tmp_path / "authority"
    projected = project_legacy_candidate(source, expected, authority_root).candidate
    rationale = AttemptRationaleInput(
        rationales=tuple(
            TranslationLineRationale(
                target_cue_id=cue.cue_id,
                line_number=line_number,
                speaker_id=cue.speakers[0],
                line_text_digest=translation_line_text_digest(line),
                rationale_ko="원문의 맥락과 화자 말투를 반영했습니다.",
            )
            for cue in projected.cues
            for line_number, line in enumerate(cue.text.split("\n"), start=1)
        )
    )
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    rationale_path = tmp_path / "rationales.json"
    output_path = tmp_path / "candidate.json"
    candidate_paths = tuple(tmp_path / f"chunk-{index}.json" for index in range(1, 3))
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        TranslationBatchPlan(
            plan_id="assembly-plan",
            source_digest=source.content_digest,
            chunks=plans,
        ).model_dump_json(),
        encoding="utf-8",
    )
    _ = rationale_path.write_text(rationale.model_dump_json(), encoding="utf-8")
    for path, candidate in zip(
        candidate_paths, chunk_candidates_fixture(), strict=True
    ):
        _ = path.write_text(candidate.model_dump_json(), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "assemble-candidate",
            str(source_path),
            str(plan_path),
            *(str(path) for path in candidate_paths),
            "--candidate-output",
            expected.document_id,
            str(output_path),
            "--attempt-authority",
            str(authority_root),
            "assembly-attempt",
            "initial",
            "-",
            "--rationales",
            str(rationale_path),
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert TranslationDocument.model_validate_json(output_path.read_bytes()) == expected
    assert tuple(
        item.attempt_id
        for item in TranslationAttemptStore(authority_root / "attempts").load()
    ) == ("assembly-attempt",)

    failed_root = tmp_path / "failed-assembly-authority"
    failed_output = tmp_path / "failed-candidate.json"
    invalid_rationale = rationale.model_copy(
        update={
            "rationales": tuple(
                item.model_copy(update={"rationale_ko": "English only."})
                for item in rationale.rationales
            )
        }
    )
    _ = rationale_path.write_text(invalid_rationale.model_dump_json(), encoding="utf-8")
    failed = subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "assemble-candidate",
            str(source_path),
            str(plan_path),
            *(str(path) for path in candidate_paths),
            "--candidate-output",
            expected.document_id,
            str(failed_output),
            "--attempt-authority",
            str(failed_root),
            "failed-assembly",
            "initial",
            "-",
            "--rationales",
            str(rationale_path),
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert failed.returncode == CLI_ERROR_EXIT
    assert not failed_output.exists()
    failed_attempt = TranslationAttemptStore(failed_root / "attempts").load()[0]
    failed_payload = failed_attempt.model_dump(mode="json", exclude_none=True)
    assert failed_payload["outcome"] == "failed"
    assert "candidate_digest" not in failed_payload
    assert "rationales" not in failed_payload

    missing_root = tmp_path / "missing-chunk-authority"
    missing_root.mkdir()
    missing_output = tmp_path / "missing-chunk.json"
    missing_command = [
        "uv",
        "run",
        "translation-cli",
        "assemble-candidate",
        str(source_path),
        str(plan_path),
        str(candidate_paths[0]),
        "--candidate-output",
        expected.document_id,
        str(missing_output),
        "--attempt-authority",
        str(missing_root),
        "missing-chunk-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale_path),
    ]
    missing = subprocess.run(
        missing_command,
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert missing.returncode == CLI_ERROR_EXIT
    assert not missing_output.exists()
    retried = subprocess.run(
        missing_command,
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert retried.returncode == CLI_ERROR_EXIT
    missing_attempts = TranslationAttemptStore(missing_root / "attempts").load()
    assert len(missing_attempts) == 1
    missing_payload = missing_attempts[0].model_dump(mode="json", exclude_none=True)
    assert missing_payload["outcome"] == "failed"
    assert "candidate_digest" not in missing_payload
    assert "rationales" not in missing_payload
