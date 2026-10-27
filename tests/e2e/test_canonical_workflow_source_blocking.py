# Copyright 2026 Transcription Module contributors

"""Historical source-finalization blockers remain typed and pure."""

import pytest

from tests.e2e.canonical_workflow_regression_support import (
    FIXTURE_PATH,
    RegressionFixture,
)
from transcription_module.source_finalization import (
    SourceFinalizationBlockedError,
    assert_source_finalizable,
)


@pytest.mark.parametrize(
    ("case_id", "code"),
    [
        ("unresolved_speech", "unresolved_speech"),
        ("empty_speaker", "invalid_speaker"),
    ],
)
def test_source_finalization_is_typed_and_non_mutating(
    case_id: str,
    code: str,
) -> None:
    """Given a historical blocker, its typed assertion preserves the source."""
    fixture = RegressionFixture.model_validate_json(FIXTURE_PATH.read_bytes())
    transcript = fixture.cases[case_id].transcript
    assert transcript is not None
    before = transcript.model_dump_json()

    with pytest.raises(SourceFinalizationBlockedError) as captured:
        assert_source_finalizable(transcript)

    assert tuple(item.code.value for item in captured.value.findings) == (code,)
    assert transcript.model_dump_json() == before
