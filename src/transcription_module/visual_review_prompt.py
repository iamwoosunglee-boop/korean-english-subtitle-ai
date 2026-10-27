# Copyright 2026 Transcription Module contributors

"""Immutable generic prompt adapter for cue-independent visual review."""

from __future__ import annotations

import hashlib
from typing import Final

from pydantic import Field

from transcription_module.models import FrozenModel
from transcription_module.visual_review import (
    VisualReviewerIdentity,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
)

VISUAL_REVIEW_PROMPT_VERSION: Final = "visual-review-generic.v8"
_VISUAL_REVIEW_PROMPT: Final = """You are the cue-independent visual reviewer.

Inspect every assigned frame in chronological order by reading each supplied image
path. The episode context explains the content, but it is not evidence that a
person, text, action, or relationship appears in a frame.

Your result becomes machine-consumed evidence before ASR, subtitles, VAD, speaker
turns, or DraftCue are available. Never infer or invent those later-stage facts.

For every assigned frame:
- return exactly one frame_observation in the same order;
- copy every complete visible string, or return an empty visible_screen_text list;
- inspect that frame independently at readable resolution and zoom when needed;
- include small but legible persistent signs, equipment labels, and branding;
- perform one foreground-overlay text sweep and a separate background, signage,
  equipment, and object-label text sweep before finalizing that frame;
- never carry a string from the preceding or following frame unless it is visibly
  present in the current frame;
- do not record screen coordinates or style geometry.

After drafting all observations, re-read every adjacent pair where any visible
string starts, ends, or changes. Correct each support boundary against the pixels
in both frames. Before output, require exact internal consistency: every string in
frame_observations appears in a candidate whose supporting_frame_ids includes that
frame, and every candidate string is visibly present in every listed support frame.

Build ordered context_spans for contiguous frame ranges. Preserve setup, action,
referent, reaction, and cross-cut meaning even when a span contains no text
candidate. Context may cross camera cuts when one semantic event continues.
Do not flatten distinct action changes into a generic summary. Split or add a
context span when a gesture, action, referent, or reaction materially changes,
even when there is no camera cut. State the observable sequence specifically.

Create a raw candidate for each meaningful visible-text occurrence. Link it to
supporting frame IDs and context span IDs. Propose only one visual role:
speech_caption, non_speech_text, or unknown. Base the proposal only on chronological
visual evidence and list evidence_for, evidence_against, and missing_evidence.
Propose unknown when one indivisible visible string itself combines a plausible
spoken answer or utterance fragment with additional editorial description or
modification, so that neither role describes the whole string. Do not choose
unknown merely because a complete conversational sentence might be an editorial
paraphrase; choose its best visual role and preserve that possibility as
counterevidence.
Repeated text in different events remains distinct. A candidate belongs to this
window only when its first supporting frame has core ownership.

Do not claim speech presence, audio timing, speaker identity, semantic correction,
or confidence scores. Frame PTS is provenance only.

Return one strict JSON object matching window-visual-review-result.v1. Use the
assignment window_index and reviewer_input_digest exactly. Return no Markdown,
code fence, commentary, or fields outside the schema. Stop only after every
assigned frame has one ordered observation and every referenced frame/span ID is
valid.

Use exactly this output shape and field vocabulary:
{
  "schema_version": "window-visual-review-result.v1",
  "window_index": <assignment window_index>,
  "reviewer_input_digest": "<assignment reviewer_input_digest>",
  "frame_observations": [
    {"frame_id": "<assigned ID>", "visible_screen_text": ["<complete string>"]}
  ],
  "context_spans": [
    {
      "context_span_id": "<unique ID>",
      "supporting_frame_ids": ["<ordered assigned ID>"],
      "context": "<semantic context>"
    }
  ],
  "candidates": [
    {
      "candidate_id": "<unique ID>",
      "complete_text": "<complete visible string>",
      "proposed_role": "speech_caption",
      "supporting_frame_ids": ["<ordered assigned ID>"],
      "context_span_ids": ["<known context span ID>"],
      "evidence_for": ["<visual evidence>"],
      "evidence_against": ["<visual counterevidence>"],
      "missing_evidence": ["<unavailable evidence>"]
    }
  ]
}
Frame observations have only frame_id and visible_screen_text. Context spans have
only context_span_id, supporting_frame_ids, and context. Candidates have only the
fields shown above. The top-level object has only the six fields shown above.
"""
VISUAL_REVIEW_PROMPT_DIGEST: Final = hashlib.sha256(
    _VISUAL_REVIEW_PROMPT.encode()
).hexdigest()


class VisualReviewPromptError(Exception):
    """A prompt request is not bound to the selected immutable template."""


class VisualReviewPromptRequest(FrozenModel):
    """Rendered provider input plus its exact typed assignment."""

    template_version: str = Field(min_length=1)
    template_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignment: WindowVisualReviewAssignment
    rendered_prompt: str = Field(min_length=1)


def selected_visual_reviewer_identity(
    model_id: str,
    settings_digest: str,
) -> VisualReviewerIdentity:
    """Bind provider identity to the selected immutable generic prompt."""
    return VisualReviewerIdentity(
        prompt_version=VISUAL_REVIEW_PROMPT_VERSION,
        prompt_digest=VISUAL_REVIEW_PROMPT_DIGEST,
        model_id=model_id,
        settings_digest=settings_digest,
    )


def build_visual_review_prompt(
    assignment: WindowVisualReviewAssignment,
) -> VisualReviewPromptRequest:
    """Render one exact assignment below the immutable generic template."""
    if (
        assignment.reviewer.prompt_version != VISUAL_REVIEW_PROMPT_VERSION
        or assignment.reviewer.prompt_digest != VISUAL_REVIEW_PROMPT_DIGEST
    ):
        message = "visual reviewer identity does not match selected prompt"
        raise VisualReviewPromptError(message)
    rendered = (
        f"{_VISUAL_REVIEW_PROMPT}\n"
        "<assignment-json>\n"
        f"{assignment.model_dump_json(indent=2)}\n"
        "</assignment-json>"
    )
    return VisualReviewPromptRequest(
        template_version=VISUAL_REVIEW_PROMPT_VERSION,
        template_digest=VISUAL_REVIEW_PROMPT_DIGEST,
        assignment=assignment,
        rendered_prompt=rendered,
    )


def parse_visual_review_output(raw_json: str) -> WindowVisualReviewResult:
    """Parse one strict provider result without prose or fence recovery."""
    return WindowVisualReviewResult.model_validate_json(raw_json)


__all__ = [
    "VISUAL_REVIEW_PROMPT_DIGEST",
    "VISUAL_REVIEW_PROMPT_VERSION",
    "VisualReviewPromptError",
    "VisualReviewPromptRequest",
    "build_visual_review_prompt",
    "parse_visual_review_output",
    "selected_visual_reviewer_identity",
]
