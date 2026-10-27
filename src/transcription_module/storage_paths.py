# Copyright 2026 Transcription Module contributors

"""Stable filesystem coordinates for one transcription run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunPaths:
    """All stable paths owned by one run."""

    run_dir: Path

    @property
    def state(self) -> Path:
        """Return the persisted workflow-state path."""
        return self.run_dir / "state.json"

    @property
    def transcript(self) -> Path:
        """Return the canonical transcript path."""
        return self.run_dir / "transcript.json"

    @property
    def input_context(self) -> Path:
        """Return the canonical input-context path."""
        return self.run_dir / "context.input.json"

    @property
    def canonical_source(self) -> Path:
        """Return the canonical source path."""
        return self.run_dir / "source.json"

    @property
    def source_human_edit(self) -> Path:
        """Return the run-owned canonical human-edit draft path."""
        return self.run_dir / "source.human-edit.json"

    @property
    def source_final(self) -> Path:
        """Return the create-once human-finalized canonical source path."""
        return self.run_dir / "source.final.json"

    @property
    def transcript_ass(self) -> Path:
        """Return the run-owned authoritative ASS path."""
        return self.run_dir / "transcript.ass"

    @property
    def transcript_csv(self) -> Path:
        """Return the derived CSV path."""
        return self.run_dir / "transcript.csv"

    @property
    def adapter_events(self) -> Path:
        """Return the deterministic adapter event log path."""
        return self.run_dir / "adapter-events.jsonl"

    @property
    def confirmations(self) -> Path:
        """Return the append-only confirmation record path."""
        return self.run_dir / "confirmations.jsonl"

    @property
    def confirmation_session(self) -> Path:
        """Return the current confirmation-session lifecycle."""
        return self.run_dir / "confirmation-session.json"

    @property
    def media_manifest(self) -> Path:
        """Return the persisted media-preparation manifest path."""
        return self.run_dir / "media.json"

    @property
    def asr_transcript(self) -> Path:
        """Return the persisted local-ASR artifact path."""
        return self.run_dir / "asr.json"

    @property
    def vision_artifact(self) -> Path:
        """Return the persisted vision-application artifact path."""
        return self.run_dir / "vision.json"

    @property
    def visual_policy_artifact(self) -> Path:
        """Return the exact visual-evidence policy sidecar path."""
        return self.run_dir / "visual-evidence-policy.json"

    @property
    def subtitle_inventory_artifact(self) -> Path:
        """Return the exact subtitle inventory sidecar path."""
        return self.run_dir / "subtitle-inventory.json"

    @property
    def vad_artifact(self) -> Path:
        """Return the reusable policy-bound VAD evidence path."""
        return self.run_dir / "vad.json"

    @property
    def visual_observations_artifact(self) -> Path:
        """Return the categorical visual observations sidecar path."""
        return self.run_dir / "visual-observations.json"

    @property
    def correction_receipts_artifact(self) -> Path:
        """Return the deterministic correction receipts sidecar path."""
        return self.run_dir / "correction-receipts.json"

    @property
    def cue_provenance_artifact(self) -> Path:
        """Return the complete cue provenance sidecar path."""
        return self.run_dir / "cue-provenance.json"

    @property
    def recovery_artifact(self) -> Path:
        """Return the persisted VAD recovery artifact path."""
        return self.run_dir / "recovery.json"

    @property
    def validation_artifact(self) -> Path:
        """Return the persisted final validation artifact path."""
        return self.run_dir / "validation.json"

    @property
    def final_review_assignment(self) -> Path:
        """Return the persisted final-review assignment path."""
        return self.run_dir / "final-review-assignment.json"

    @property
    def final_review_artifact(self) -> Path:
        """Return the persisted final-review receipt path."""
        return self.run_dir / "final-review.json"

    @property
    def final_review_result(self) -> Path:
        """Return the accepted complete final-review result path."""
        return self.run_dir / "final-review-result.json"

    @property
    def window_drafts(self) -> Path:
        """Return all window evidence inputs and draft results."""
        return self.run_dir / "window-drafts.json"

    @property
    def window_visual_review(self) -> Path:
        """Return cue-independent visual assignments and raw results."""
        return self.run_dir / "window-visual-review.json"

    @property
    def diarization_artifact(self) -> Path:
        """Return optional whole-audio voice-cluster evidence."""
        return self.run_dir / "diarization.json"

    @property
    def verified_speaker_turns_artifact(self) -> Path:
        """Return turns derived from admitted independent authority."""
        return self.run_dir / "verified-speaker-turns.json"

    @property
    def speaker_turn_authority_artifact(self) -> Path:
        """Return the inventory-admitted canonical speaker-turn authority."""
        return self.run_dir / "speaker-turn-authority.json"

    @property
    def speaker_turn_decisions(self) -> Path:
        """Return immutable confirmation records for speaker-turn decisions."""
        return self.run_dir / "speaker-turn-decisions.jsonl"

    @property
    def whole_transcript_artifact(self) -> Path:
        """Return whole-transcript assembly evidence."""
        return self.run_dir / "whole-transcript.json"

    @property
    def speaker_inference_artifact(self) -> Path:
        """Return whole-transcript speaker inference decisions."""
        return self.run_dir / "speaker-inference.json"

    @property
    def web_lookup_artifact(self) -> Path:
        """Return supporting external-fact lookup provenance."""
        return self.run_dir / "web-lookups.json"

__all__ = ["RunPaths"]
