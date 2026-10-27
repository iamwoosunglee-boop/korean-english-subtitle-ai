# Copyright 2026 Transcription Module contributors

"""Task 12 unresolved-candidate identity contract guards."""

import pytest
from pydantic import ValidationError

from transcription_module.drafting import UnresolvedItem


@pytest.mark.parametrize(
    ("receipt_ids", "message"),
    [
        (("receipt-a", "receipt-a"), "receipt IDs must be unique"),
        (("receipt-a",), "one receipt ID each"),
    ],
)
def test_candidates_require_distinct_one_to_one_receipt_identity(
    receipt_ids: tuple[str, ...],
    message: str,
) -> None:
    """Given candidate receipts, duplicates and incomplete mappings are rejected."""
    with pytest.raises(ValidationError, match=message):
        _ = UnresolvedItem(
            at_ms=0,
            field="speech",
            reason="conflicting edits",
            candidates=("first", "second"),
            candidate_receipt_ids=receipt_ids,
        )
