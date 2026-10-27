# Copyright 2026 Transcription Module contributors

"""Repository QA entry points."""

from scripts.qa.paired_diagnostic import (
    evaluate_paired_diagnostic,
    threshold_for,
    verify_receipt_manifest,
)
from scripts.qa.paired_diagnostic_models import (
    AcceptanceReceipt,
    MetricId,
    MetricReceipt,
)

__all__ = [
    "AcceptanceReceipt",
    "MetricId",
    "MetricReceipt",
    "evaluate_paired_diagnostic",
    "threshold_for",
    "verify_receipt_manifest",
]
