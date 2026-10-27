# Copyright 2026 Transcription Module contributors

"""Authentic mutation characterizations for independent-review routing."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.translation.type_builders import milliseconds
from translation_module.common import (
    ContentKind,
    SourceCueId,
    TargetCueId,
)
from translation_module.contracts import (
    DocumentStatus,
    EpisodeContext,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
    TargetCue,
    TranslationDocument,
)
from translation_module.preparation import bind_source_digest
from translation_module.review_preparation import (
    ReviewScope,
    build_review_request,
    review_assignment_from_request,
)
from translation_module.review_routing import (
    bind_review_assignment_digest,
    route_review,
    validate_review_assignment_scope,
)
from translation_module.review_state import ReviewAttempts
from translation_module.validation import (
    TranslationValidationError,
    bind_translation_digest,
    validate_translation,
)
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    CompletedDefectAudit,
    FindingSeverity,
    ReviewAssignment,
    ReviewDomain,
    ReviewFinding,
    ReviewKind,
    ReviewResult,
    RouteAction,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_IDS = tuple(SourceCueId(f"s{index}") for index in range(1, 16))
TARGET_IDS = tuple(TargetCueId(f"t{index}") for index in range(1, 16))
SESSIONS = ("translator-run", "reviser-run", "assembler-run", "reviewer-run")
ChangedDimension = tuple[TargetCueId, str]


@dataclass(frozen=True)
class Mutation:
    """One isolated candidate change and its typed reviewer verdict."""

    name: str
    domain: ReviewDomain
    severity: FindingSeverity
    source_id: SourceCueId
    target_id: TargetCueId
    defect_class: str
    changed_dimensions: frozenset[ChangedDimension]
    apply: Callable[[TranslationDocument], TranslationDocument]
    remains_structurally_valid: bool = field(default=True, kw_only=True)


SPECIAL_TIMINGS = {
    9: (32_000, 38_000),
    10: (34_000, 40_000),
    14: (52_000, 58_000),
    15: (54_000, 60_000),
}
LYRIC_SEQUENCE = 11


def _timing(index: int) -> tuple[int, int]:
    if timing := SPECIAL_TIMINGS.get(index):
        return timing
    start = (index - 1) * 4_000
    return start, start + 4_000


SOURCE_TEXTS = (
    "나는 그 의견에 동의하지 않아.",
    "손님 세 명을 초대했어.",
    "미나가 공연을 시작했어.",
    "비가 계속 와서 그녀는 남았어.",
    "한 손님이 문을 열었어.",
    "방은 조용했어.",
    "응, 다 끝냈어.",
    "눈이 내려서 길이 폐쇄됐어.",
    "내가 먼저 물었어.",
    "내가 두 번째로 답했어.",
    "Hold on to my blue moon",
    "김 선생님, 앉으세요.",
    "그가 마침내 어색함을 깼어.",
    "A는 정원 일을 말한다.",
    "B는 회계 일을 말한다.",
)
CANDIDATE_TEXTS = (
    "I do not agree.",
    "We invited three guests.",
    "Mina opened the show.",
    "She stayed because the rain continued.",
    "A guest opened the door.",
    "The room was quiet.",
    "Yes, I finished it.",
    "The road closed because snow fell.",
    "I asked first.",
    "I answered second.",
    "Hold on to my blue moon",
    "Professor Kim, please sit.",
    "He finally broke the ice.",
    "A gardens.",
    "B audits.",
)
SPEAKERS = ("A",) * 8 + ("A", "B", "A", "A", "A", "A", "B")


def _source() -> SourceSnapshot:
    source = SourceSnapshot(
        document_id="source-review-fixture",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(60_000),
        context=EpisodeContext(summary="대화와 노래가 이어지는 장면"),
        evidence=EvidenceManifest(base_uri="memory://review-fixture/"),
        cues=tuple(
            SourceCue(
                sequence=index,
                cue_id=SOURCE_IDS[index - 1],
                start_ms=milliseconds(_timing(index)[0]),
                end_ms=milliseconds(_timing(index)[1]),
                text=SOURCE_TEXTS[index - 1],
                speakers=(SPEAKERS[index - 1],),
                content_kind=(
                    ContentKind.LYRICS
                    if index == LYRIC_SEQUENCE
                    else ContentKind.DIALOGUE
                ),
            )
            for index in range(1, 16)
        ),
    )
    return bind_source_digest(source)


def _candidate(source: SourceSnapshot) -> TranslationDocument:
    candidate = TranslationDocument(
        document_id="candidate-review-fixture",
        content_digest="0" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale="ko-KR",
        target_locale="en-US",
        context=source.context,
        retained_contexts=(),
        cues=tuple(
            TargetCue(
                sequence=index,
                cue_id=TARGET_IDS[index - 1],
                source_cue_ids=(SOURCE_IDS[index - 1],),
                start_ms=milliseconds(_timing(index)[0]),
                end_ms=milliseconds(_timing(index)[1]),
                text=CANDIDATE_TEXTS[index - 1],
                speakers=(SPEAKERS[index - 1],),
                content_kind=(
                    ContentKind.LYRICS
                    if index == LYRIC_SEQUENCE
                    else ContentKind.DIALOGUE
                ),
            )
            for index in range(1, 16)
        ),
        status=DocumentStatus.FINALIZED,
    )
    return bind_translation_digest(candidate)


def _change_text(
    candidate: TranslationDocument,
    target_id: TargetCueId,
    transform: Callable[[str], str],
) -> TranslationDocument:
    cues = tuple(
        cue.model_copy(update={"text": transform(cue.text)})
        if cue.cue_id == target_id
        else cue
        for cue in candidate.cues
    )
    return bind_translation_digest(candidate.model_copy(update={"cues": cues}))


def _delete_negation(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(candidate, TARGET_IDS[0], lambda text: text.replace(" not", ""))


def _delete_quantity(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[1], lambda text: text.replace("three ", "")
    )


def _delete_proper_name(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[2], lambda text: text.replace("Mina ", "")
    )


def _delete_clause(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[3], lambda text: text.split(" because")[0] + "."
    )


def _invent_person(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[4], lambda text: text.replace("A guest", "Daniel")
    )


def _invent_fact(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate,
        TARGET_IDS[5],
        lambda text: text.replace("quiet", "filled with cameras"),
    )


def _reverse_yes_no(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[6], lambda text: text.replace("Yes", "No")
    )


def _reverse_cause_effect(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[7], lambda _text: "Snow fell because the road closed."
    )


def _swap_crosstalk_meanings(candidate: TranslationDocument) -> TranslationDocument:
    first, second = candidate.cues[8], candidate.cues[9]
    cues = (
        *candidate.cues[:8],
        first.model_copy(update={"text": second.text}),
        second.model_copy(update={"text": first.text}),
        *candidate.cues[10:],
    )
    return bind_translation_digest(candidate.model_copy(update={"cues": cues}))


def _alter_lyric_word(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[10], lambda text: text.replace("blue", "red")
    )


def _literalize_honorific(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[11], lambda text: text.replace("Professor", "Teacher")
    )


def _literalize_idiom(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[12], lambda _text: "He finally shattered frozen water."
    )


def _merge_interleaved(candidate: TranslationDocument) -> TranslationDocument:
    first, second = candidate.cues[13], candidate.cues[14]
    merged = first.model_copy(
        update={
            "source_cue_ids": (SOURCE_IDS[13], SOURCE_IDS[14]),
            "end_ms": second.end_ms,
            "text": f"{first.text} {second.text}",
            "speakers": ("A", "B"),
        }
    )
    return bind_translation_digest(
        candidate.model_copy(update={"cues": (*candidate.cues[:13], merged, second)})
    )


def _exceed_lines(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(candidate, TARGET_IDS[0], lambda _text: "one\ntwo\nthree")


def _exceed_cps(candidate: TranslationDocument) -> TranslationDocument:
    return _change_text(
        candidate, TARGET_IDS[1], lambda _text: f"{'x' * 41}\n{'x' * 41}"
    )


def _text_dimension(index: int) -> frozenset[ChangedDimension]:
    return frozenset({(TARGET_IDS[index], "text")})


MUTATIONS = (
    Mutation(
        "delete-negation",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[0],
        TARGET_IDS[0],
        "polarity",
        _text_dimension(0),
        _delete_negation,
    ),
    Mutation(
        "delete-quantity",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[1],
        TARGET_IDS[1],
        "quantity",
        _text_dimension(1),
        _delete_quantity,
    ),
    Mutation(
        "delete-proper-name",
        ReviewDomain.FIDELITY,
        FindingSeverity.MAJOR,
        SOURCE_IDS[2],
        TARGET_IDS[2],
        "proper-name",
        _text_dimension(2),
        _delete_proper_name,
    ),
    Mutation(
        "delete-clause",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[3],
        TARGET_IDS[3],
        "omission",
        _text_dimension(3),
        _delete_clause,
    ),
    Mutation(
        "invent-person",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[4],
        TARGET_IDS[4],
        "invented-person",
        _text_dimension(4),
        _invent_person,
    ),
    Mutation(
        "invent-fact",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[5],
        TARGET_IDS[5],
        "invented-fact",
        _text_dimension(5),
        _invent_fact,
    ),
    Mutation(
        "reverse-yes-no",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[6],
        TARGET_IDS[6],
        "polarity",
        _text_dimension(6),
        _reverse_yes_no,
    ),
    Mutation(
        "reverse-cause-effect",
        ReviewDomain.FIDELITY,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[7],
        TARGET_IDS[7],
        "causality",
        _text_dimension(7),
        _reverse_cause_effect,
    ),
    Mutation(
        "swap-crosstalk-speakers",
        ReviewDomain.SPEAKER,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[8],
        TARGET_IDS[8],
        "speaker-attribution",
        frozenset({(TARGET_IDS[8], "text"), (TARGET_IDS[9], "text")}),
        _swap_crosstalk_meanings,
    ),
    Mutation(
        "alter-latin-lyric",
        ReviewDomain.LYRICS,
        FindingSeverity.CRITICAL,
        SOURCE_IDS[10],
        TARGET_IDS[10],
        "lyric-words",
        _text_dimension(10),
        _alter_lyric_word,
    ),
    Mutation(
        "literalize-honorific",
        ReviewDomain.CULTURE,
        FindingSeverity.MAJOR,
        SOURCE_IDS[11],
        TARGET_IDS[11],
        "honorific",
        _text_dimension(11),
        _literalize_honorific,
    ),
    Mutation(
        "literalize-idiom",
        ReviewDomain.CULTURE,
        FindingSeverity.MAJOR,
        SOURCE_IDS[12],
        TARGET_IDS[12],
        "idiom",
        _text_dimension(12),
        _literalize_idiom,
    ),
    Mutation(
        "merge-interleaved-speech",
        ReviewDomain.SEGMENTATION,
        FindingSeverity.MAJOR,
        SOURCE_IDS[13],
        TARGET_IDS[13],
        "interleaved-merge",
        frozenset(
            {
                (TARGET_IDS[13], field)
                for field in ("source_cue_ids", "end_ms", "text", "speakers")
            }
        ),
        _merge_interleaved,
        remains_structurally_valid=False,
    ),
    Mutation(
        "exceed-line-limit",
        ReviewDomain.SEGMENTATION,
        FindingSeverity.MAJOR,
        SOURCE_IDS[0],
        TARGET_IDS[0],
        "line-limit",
        _text_dimension(0),
        _exceed_lines,
        remains_structurally_valid=False,
    ),
    Mutation(
        "exceed-cps-limit",
        ReviewDomain.SEGMENTATION,
        FindingSeverity.MAJOR,
        SOURCE_IDS[1],
        TARGET_IDS[1],
        "cps-limit",
        _text_dimension(1),
        _exceed_cps,
        remains_structurally_valid=False,
    ),
)


def _assignment(
    source: SourceSnapshot, candidate: TranslationDocument
) -> ReviewAssignment:
    request = build_review_request(
        source,
        candidate,
        ReviewScope(source_cue_ids=SOURCE_IDS, target_cue_ids=TARGET_IDS),
        SESSIONS,
    )
    return review_assignment_from_request(request)


def _finding(mutation: Mutation, candidate_digest: str) -> ReviewFinding:
    return ReviewFinding(
        finding_id=f"finding-{mutation.name}",
        candidate_digest=candidate_digest,
        affected=(
            AffectedReference(kind=AffectedKind.SOURCE_CUE, item_id=mutation.source_id),
            AffectedReference(kind=AffectedKind.TARGET_CUE, item_id=mutation.target_id),
        ),
        severity=mutation.severity,
        domain=mutation.domain,
        defect_class=mutation.defect_class,
        blocking=True,
        problem="P",
        required_change="R",
    )


def _review(
    assignment: ReviewAssignment,
    finding: ReviewFinding | None = None,
    *,
    audited: bool = False,
) -> ReviewResult:
    return ReviewResult(
        candidate_digest=assignment.current_candidate_digest,
        assignment_digest=assignment.assignment_digest,
        reviewed_source_cue_ids=SOURCE_IDS,
        reviewed_target_cue_ids=TARGET_IDS,
        reviewed_domains=tuple(ReviewDomain),
        completed_defect_audits=(
            (
                CompletedDefectAudit(
                    defect_class=finding.defect_class,
                    reviewed_source_cue_ids=SOURCE_IDS,
                ),
            )
            if finding is not None and audited
            else ()
        ),
        findings=(() if finding is None else (finding,)),
    )


def _attempts(candidate_digest: str) -> ReviewAttempts:
    return ReviewAttempts(
        root_candidate_digest=candidate_digest,
        current_candidate_digest=candidate_digest,
    )


def _changed_dimensions(
    before: TranslationDocument, after: TranslationDocument
) -> frozenset[ChangedDimension]:
    changed: set[ChangedDimension] = set()

    def dimensions(cue: TargetCue) -> dict[str, object]:
        return {
            "sequence": cue.sequence,
            "source_cue_ids": cue.source_cue_ids,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "text": cue.text,
            "speakers": cue.speakers,
            "content_kind": cue.content_kind,
            "context_ids": cue.context_ids,
            "evidence_ids": cue.evidence_ids,
        }

    before_by_id = {cue.cue_id: dimensions(cue) for cue in before.cues}
    after_by_id = {cue.cue_id: dimensions(cue) for cue in after.cues}
    for cue_id in TARGET_IDS:
        for dimension, value in before_by_id[cue_id].items():
            if after_by_id[cue_id][dimension] != value:
                changed.add((cue_id, dimension))
    return frozenset(changed)


def test_clean_dialogue_has_exact_independent_scope_and_final_route() -> None:
    """A clean exact-scope review finalizes without focused review."""
    source = _source()
    candidate = _candidate(source)
    validate_translation(source, candidate)
    assignment = _assignment(source, candidate)
    review = _review(assignment)
    attempts = _attempts(candidate.content_digest)

    route = route_review(
        review,
        expected_candidate_digest=candidate.content_digest,
        attempts=attempts,
        assignment=assignment,
    )

    assert assignment.ordered_source_cue_ids == SOURCE_IDS
    assert assignment.ordered_target_cue_ids == TARGET_IDS
    assert assignment.reviewed_domains == tuple(ReviewDomain)
    assert len(set(SESSIONS)) == len(SESSIONS)
    assert review.findings == ()
    assert attempts.focused_review is None
    assert route.action is RouteAction.FINALIZE
    assert route.focused_domains == ()


def _mutation_id(mutation: Mutation) -> str:
    return mutation.name


@pytest.mark.parametrize("mutation", MUTATIONS, ids=_mutation_id)
def test_mutation_routes_through_typed_audit_and_applicable_review(
    mutation: Mutation,
) -> None:
    """Each authentic reviewer verdict audits and never finalizes."""
    source = _source()
    candidate = mutation.apply(_candidate(source))
    assignment = _assignment(source, candidate)
    finding = _finding(mutation, candidate.content_digest)
    unaudited_review = _review(assignment, finding)
    attempts = _attempts(candidate.content_digest)

    audit_route = route_review(
        unaudited_review,
        expected_candidate_digest=candidate.content_digest,
        attempts=attempts,
        assignment=assignment,
    )

    assert finding.domain is mutation.domain
    assert finding.severity is mutation.severity
    assert finding.blocking is True
    assert tuple((item.kind, item.item_id) for item in finding.affected) == (
        (AffectedKind.SOURCE_CUE, mutation.source_id),
        (AffectedKind.TARGET_CUE, mutation.target_id),
    )
    assert audit_route.action is RouteAction.AUDIT
    assert audit_route.audit_defect_classes == (mutation.defect_class,)

    audited_review = _review(assignment, finding, audited=True)
    audit = audited_review.completed_defect_audits[0]
    assert audit.defect_class == mutation.defect_class
    assert audit.reviewed_source_cue_ids == SOURCE_IDS

    if mutation.domain in {
        ReviewDomain.FIDELITY,
        ReviewDomain.SPEAKER,
        ReviewDomain.CULTURE,
        ReviewDomain.LYRICS,
    }:
        focused_route = route_review(
            audited_review,
            expected_candidate_digest=candidate.content_digest,
            attempts=attempts,
            assignment=assignment,
        )
        assert focused_route.action is RouteAction.FOCUSED_REVIEW
        assert focused_route.focused_domains == (mutation.domain,)
        focused_assignment = bind_review_assignment_digest(
            assignment.model_copy(
                update={
                    "assignment_digest": "0" * 64,
                    "focused_source_cue_ids": (mutation.source_id,),
                    "focused_target_cue_ids": (mutation.target_id,),
                    "focused_domains": (mutation.domain,),
                }
            )
        )
        focused_review = ReviewResult(
            candidate_digest=candidate.content_digest,
            assignment_digest=focused_assignment.assignment_digest,
            reviewed_source_cue_ids=(mutation.source_id,),
            reviewed_target_cue_ids=(mutation.target_id,),
            reviewed_domains=(mutation.domain,),
            kind=ReviewKind.FOCUSED,
            findings=(finding,),
        )
        validate_review_assignment_scope(focused_review, focused_assignment)
        attempts = attempts.model_copy(
            update={
                "focused_domains_reviewed": (mutation.domain,),
                "focused_review": focused_review,
                "focused_assignment": focused_assignment,
            }
        )
        revise_route = route_review(
            audited_review,
            expected_candidate_digest=candidate.content_digest,
            attempts=attempts,
            focused_domains_reviewed=attempts.focused_domains_reviewed,
            assignment=assignment,
        )
        assert attempts.focused_review is not None
        assert attempts.focused_review.candidate_digest == candidate.content_digest
    else:
        revise_route = route_review(
            audited_review,
            expected_candidate_digest=candidate.content_digest,
            attempts=attempts,
            assignment=assignment,
        )

    assert revise_route.action is RouteAction.REVISE
    assert revise_route.action is not RouteAction.FINALIZE


def test_mutations_are_isolated_valid_and_free_of_prose_assertions() -> None:
    """Mutation dimensions are isolated and assertions do not pin prose."""
    source = _source()
    baseline = _candidate(source)
    validate_translation(source, baseline)

    for mutation in MUTATIONS:
        changed = mutation.apply(baseline)
        assert _changed_dimensions(baseline, changed) == mutation.changed_dimensions
        if mutation.remains_structurally_valid:
            validate_translation(source, changed)
        else:
            with pytest.raises(TranslationValidationError):
                validate_translation(source, changed)

    crosstalk_a, crosstalk_b = source.cues[8:10]
    lyric_source = source.cues[10]
    lyric_target = baseline.cues[10]
    assert crosstalk_a.start_ms < crosstalk_b.end_ms
    assert crosstalk_b.start_ms < crosstalk_a.end_ms
    assert lyric_source.content_kind is ContentKind.LYRICS
    assert lyric_target.content_kind is ContentKind.LYRICS

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    prose_fields = {"problem", "required_change", "text", "summary"}
    asserted_fields = {
        node.attr
        for assertion in (
            node for node in ast.walk(tree) if isinstance(node, ast.Assert)
        )
        for node in ast.walk(assertion.test)
        if isinstance(node, ast.Attribute)
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert asserted_fields.isdisjoint(prose_fields)
    assert not {"snapshot", "match_snapshot"} & called_names
