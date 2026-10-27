# Copyright (c) 2026
"""Executable contract for full vision coverage and VFR-safe timing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import Field

from transcription_module.models import (
    Cue,
    FrameId,
    FrozenModel,
    Milliseconds,
    SubtitleTrust,
)
from transcription_module.timing import (
    FrameManifest,
    FrameManifestEntry,
    SubtitleInterval,
    subtitle_interval_from_pts,
)
from transcription_module.vision import (
    AuditSegment,
    ScreenEvidence,
    VisionAuditRequest,
    VisionContractError,
    VisionFinding,
    VisionResolution,
    apply_screen_evidence,
    assert_full_vision_coverage,
    resolve_vision,
)

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "vision_vfr" / "run.json"
)
Resolver = Callable[[VisionAuditRequest], VisionFinding]


class VisionVfrFixture(FrozenModel):
    """Deterministic VFR frames, audit ownership, and speech cues."""

    duration_ms: Milliseconds = Field(gt=0)
    frames: tuple[FrameManifestEntry, ...]
    audit_segments: tuple[AuditSegment, ...]
    cues: tuple[Cue, ...]


def load_fixture() -> VisionVfrFixture:
    """Load the checked-in deterministic fixture through typed models."""
    return VisionVfrFixture.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )


def finding(
    *,
    resolution: VisionResolution,
    lower_third: str | None,
) -> VisionFinding:
    """Build one deterministic provider result."""
    return VisionFinding(
        scene="두 사람이 대화한다.",
        rough_lower_third=lower_third,
        resolution=resolution,
        confidence=0.9,
        first_visible_frame_id=FrameId("frame-002"),
        first_not_visible_frame_id=FrameId("frame-005"),
    )


def test_vfr_manifest_uses_actual_pts_for_half_open_subtitle_interval() -> None:
    """Use actual source PTS, including source duration for an open end."""
    fixture = load_fixture()
    manifest = FrameManifest(
        duration_ms=fixture.duration_ms,
        frames=fixture.frames,
    )

    interval = subtitle_interval_from_pts(
        manifest,
        first_visible_frame_id=FrameId("frame-002"),
        first_not_visible_frame_id=FrameId("frame-005"),
    )
    through_end = subtitle_interval_from_pts(
        manifest,
        first_visible_frame_id=FrameId("frame-004"),
        first_not_visible_frame_id=None,
    )

    assert interval == SubtitleInterval(
        start_tc_ms=Milliseconds(167),
        end_tc_ms=Milliseconds(1333),
        first_visible_frame_id=FrameId("frame-002"),
        first_not_visible_frame_id=FrameId("frame-005"),
    )
    assert through_end.end_tc_ms == Milliseconds(2000)
    assert [
        (frame.frame_id, frame.frame_index, frame.pts_ms)
        for frame in manifest.frames
    ] == [
        (FrameId("frame-000"), 0, Milliseconds(0)),
        (FrameId("frame-001"), 1, Milliseconds(83)),
        (FrameId("frame-002"), 2, Milliseconds(167)),
        (FrameId("frame-003"), 3, Milliseconds(410)),
        (FrameId("frame-004"), 4, Milliseconds(900)),
        (FrameId("frame-005"), 5, Milliseconds(1333)),
    ]


def test_audit_segments_cover_full_timeline_and_own_every_cue_once() -> None:
    """Accept full coverage and reject duplicate cue ownership."""
    fixture = load_fixture()

    assert_full_vision_coverage(
        fixture.audit_segments,
        duration_ms=fixture.duration_ms,
        cues=fixture.cues,
    )

    duplicated_owner = (
        fixture.audit_segments[0],
        fixture.audit_segments[1].model_copy(
            update={"owned_cue_sequences": (1, 2)}
        ),
    )
    with pytest.raises(VisionContractError, match="owned exactly once"):
        assert_full_vision_coverage(
            duplicated_owner,
            duration_ms=fixture.duration_ms,
            cues=fixture.cues,
        )


def test_sol_scene_and_lower_third_route_only_while_unresolved() -> None:
    """Stop the Sol-Terra-OCR route as soon as evidence resolves."""
    request = VisionAuditRequest(
        start_tc_ms=Milliseconds(0),
        end_tc_ms=Milliseconds(2000),
        frame_ids=(FrameId("frame-000"), FrameId("frame-005")),
    )
    calls: list[str] = []

    def sol(_: VisionAuditRequest) -> VisionFinding:
        calls.append("sol")
        return finding(
            resolution=VisionResolution.UNRESOLVED,
            lower_third="오늘의 주제",
        )

    def terra(_: VisionAuditRequest) -> VisionFinding:
        calls.append("terra")
        return finding(
            resolution=VisionResolution.UNRESOLVED,
            lower_third="오늘의 주제",
        )

    def ocr(_: VisionAuditRequest) -> VisionFinding:
        calls.append("ocr")
        return finding(
            resolution=VisionResolution.RESOLVED,
            lower_third="오늘의 주제",
        )

    result = resolve_vision(request, sol=sol, terra=terra, ocr=ocr)

    assert calls == ["sol", "terra", "ocr"]
    assert result.route == ("sol", "terra", "ocr")
    assert result.finding.scene == "두 사람이 대화한다."
    assert result.finding.rough_lower_third == "오늘의 주제"

    calls.clear()

    def resolved_sol(_: VisionAuditRequest) -> VisionFinding:
        calls.append("sol")
        return finding(
            resolution=VisionResolution.RESOLVED,
            lower_third="확정 문구",
        )

    resolved = resolve_vision(
        request,
        sol=resolved_sol,
        terra=terra,
        ocr=ocr,
    )

    assert calls == ["sol"]
    assert resolved.route == ("sol",)


@pytest.mark.parametrize(
    ("estimated", "override", "expected_start", "expected_end"),
    [
        (SubtitleTrust.LOW, None, 100, 700),
        (SubtitleTrust.MEDIUM, None, 100, 700),
        (SubtitleTrust.HIGH, SubtitleTrust.LOW, 100, 700),
        (SubtitleTrust.LOW, SubtitleTrust.HIGH, 167, 1333),
        (SubtitleTrust.HIGH, None, 167, 1333),
    ],
)
def test_subtitle_trust_and_override_control_timing_without_replacing_speech(
    estimated: SubtitleTrust,
    override: SubtitleTrust | None,
    expected_start: int,
    expected_end: int,
) -> None:
    """Apply override-first trust without replacing spoken words."""
    fixture = load_fixture()
    cue = fixture.cues[0]
    interval = SubtitleInterval(
        start_tc_ms=Milliseconds(167),
        end_tc_ms=Milliseconds(1333),
        first_visible_frame_id=FrameId("frame-002"),
        first_not_visible_frame_id=FrameId("frame-005"),
    )

    aligned = apply_screen_evidence(
        cue,
        ScreenEvidence(
            screen_text="짧은 요약",
            interval=interval,
            estimated_trust=estimated,
            trust_override=override,
            meaning_matches=True,
            audio_boundary_uncertain=True,
        ),
    )

    assert aligned.start_tc_ms == expected_start
    assert aligned.end_tc_ms == expected_end
    assert aligned.speech == "실제로 길게 말한 문장"
    assert aligned.screen_text == "짧은 요약"
