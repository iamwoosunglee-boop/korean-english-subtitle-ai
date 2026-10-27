# Copyright 2026 Transcription Module contributors

"""Behavioral structure checks for the natural-language transcription skill."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SKILL_PATH = PROJECT_ROOT / ".agents" / "skills" / "video-transcription" / "SKILL.md"
MAX_SKILL_LINES = 100


def test_skill_enforces_context_gate_and_fixed_stage_order() -> None:
    """The runbook gates costly work and exposes each reproducible stage."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    stages = (
        "transcription-cli start",
        "transcription-cli protected-run",
        "create_window_drafts",
        "assemble_whole_transcript",
        "transcription-cli confirmation-consent",
        "transcription-cli next-confirmation",
        "transcription-cli suggest",
        'transcription-cli confirm "$RUN_ID"',
        "prepare_final_review",
        "apply_final_review",
        "transcription-cli validate",
    )

    positions = tuple(skill.index(stage) for stage in stages)

    assert positions == tuple(sorted(positions))
    assert "--decline" in skill
    assert "ready_for_validation" in skill
    assert "--model" not in skill
    assert len(skill.splitlines()) <= MAX_SKILL_LINES


def test_repository_routes_transcription_requests_to_skill() -> None:
    """Project instructions make the repository-local skill discoverable."""
    instructions = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert ".agents/skills/video-transcription/SKILL.md" in instructions
    assert "context gate is mandatory" in instructions
