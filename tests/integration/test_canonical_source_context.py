# Copyright 2026 Transcription Module contributors

"""Structured episode context survives the public canonical source handoff."""

from pathlib import Path

from tests.integration.canonical_source_export_support import canonical_inputs
from transcription_module.canonical_source_export import build_canonical_source_pair
from transcription_module.models import ContextPerson, EpisodeContext
from translation_module.canonical.asset_preparation import prepare_translation_assets
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.source import SourceFinalization
from translation_module.canonical.validation import finalize_source


def test_typed_roster_survives_export_and_translation_preparation(
    tmp_path: Path,
) -> None:
    """V2 roster identities and names reach the prepared translation context."""
    store, run_id, state, transcript, _ = canonical_inputs(tmp_path)
    episode = EpisodeContext.bind_typed(
        raw_text="루미는 빨간 옷. 한지아는 검은 옷.",
        roster=(
            ContextPerson(
                person_id="person-a",
                display_name="루미",
                appearance_hints=("빨간 옷",),
            ),
            ContextPerson(
                person_id="person-b",
                display_name="한지아",
                appearance_hints=("검은 옷",),
            ),
        ),
    )

    context, source = build_canonical_source_pair(
        store,
        run_id,
        state=state.model_copy(update={"context": episode}),
        transcript=transcript,
    )
    finalized = finalize_source(
        source,
        SourceFinalization(
            finalized_at="2026-09-08T00:00:00Z",
            finalized_by="human-editor",
        ),
        input_context=context,
    )
    prepared = prepare_translation_assets(finalized, context)

    assert tuple(
        (person.participant_id, person.name_ko, person.aliases)
        for person in context.participants
    ) == (
        ("person-a", "루미", ("루미",)),
        ("person-b", "한지아", ("한지아",)),
    )
    assert prepared.context.participants == context.participants
    assert all(person.name_en is None for person in context.participants)
    assert context.episode_summary == episode.raw_text
    assert source.cues[0].speakers == ("루미", "한지아")
    verify_canonical_digests(context)
    verify_canonical_digests(prepared.context)
