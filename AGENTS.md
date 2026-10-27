# PROJECT KNOWLEDGE BASE

**Generated:** 2026-08-27
**Commit:** 236f9c9
**Branch:** local-development

## OVERVIEW

Python 3.13 monorepo for evidence-bound Korean transcription, canonical subtitle
translation, and a local operations dashboard. Hatch builds the project, `uv`
manages execution, and each runtime package exposes typed immutable contracts
through explicit public boundaries.

## STRUCTURE

```text
전사번역모듈/
├── src/
│   ├── transcription_module/       # Evidence-bound transcription authority
│   ├── translation_module/         # Translation workflow and publication
│   │   └── canonical/              # Authoritative v2 artifact graph
│   └── dashboard_module/           # Local-first operational projections
├── tests/                          # Deterministic unit through end-to-end checks
├── scripts/                        # Generators and operator utilities
│   └── qa/                         # Paired-diagnostic evaluation and receipts
├── .agents/skills/                 # Workflow-specific operating instructions
└── CONTEXT.md                      # Normative repository terminology
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Transcription contracts and recovery | `src/transcription_module/` | Authority binds run, source, policy, inventory, and evidence era |
| Translation workflow | `src/translation_module/` | Preparation, review, evidence, CLI durability, publication |
| Canonical v2 artifacts | `src/translation_module/canonical/` | Bytes, digests, revisions, attempts, review contracts |
| Dashboard projections and catalog | `src/dashboard_module/` | Local event ingestion and guarded catalog management |
| Shared test policy | `tests/` | Deterministic and adversarial expectations |
| Contract-focused transcription tests | `tests/unit/`, `tests/integration/` | Models, parsers, persistence, recovery, filesystem security |
| Translation lifecycle tests | `tests/translation/`, `tests/e2e/` | Canonical workflow, CLI/WAL, publication, user journeys |
| Dashboard tests | `tests/dashboard/` | Schema, projections, HTTP, durability, migration, native UI |
| Deterministic generators | `scripts/` | Repository generation and operator utilities |
| QA receipt verification | `scripts/qa/` | Evidence-bound paired diagnostics |
| Terminology definitions | `CONTEXT.md` | Korean workflow vocabulary is normative |

## CODE MAP

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `transcription_module` | Package | `src/transcription_module/` | unmeasured | Transcription authority and publication |
| `translation_module` | Package | `src/translation_module/` | unmeasured | Translation workflow boundary |
| `translation_module.canonical` | Package | `src/translation_module/canonical/` | unmeasured | Authoritative v2 artifact graph |
| `dashboard_module` | Package | `src/dashboard_module/` | unmeasured | Local operations dashboard |
| `transcription-cli` | Console entry | project package metadata | unmeasured | Transcription command surface |
| `translation-cli` | Console entry | project package metadata | unmeasured | Translation command surface |
| `dashboard-cli` | Console entry | project package metadata | unmeasured | Dashboard command surface |

## CONVENTIONS

- Use Python 3.13, Hatch packaging, and `uv` for project commands.
- Treat stored bytes and receipts as authoritative over caller claims.
- Keep filesystem publication confined, lock-aware, and failure-atomic.
- Canonical JSON is NFC-normalized and permits neither `null` nor floats; integer
  values must remain in the safe range.
- Maintain separate content and record digests for canonical translation data.
- Change canonical contracts as one unit: Python models, examples, generated
  schemas, digest vectors, and strict round-trip/rejection tests.
- Bound translation chunks to 64 owned cues and 64 context cues on each side.
- Keep dashboard freshness, availability, workflow state, delivery, and
  interventions as separate state dimensions.

## ANTI-PATTERNS (THIS PROJECT)

- Never let caller claims override authoritative persisted bytes or receipts.
- Never publish partial output or mutate authoritative bytes on a failed path.
- Do not publish from stale selected authority or from focused rereview alone;
  publication requires current selection and an independent complete review.
- Do not add approve, reject, retry, or cancel workflow controls to the dashboard.
- Do not use sleeps or live networks in tests.
- Do not interpret diarization labels as real-person identity.

## UNIQUE STYLES

- Repository vocabulary is fixed by `CONTEXT.md`: ASR is `기초 전사`, window
  output is `구간 초안`, and pre-confirmation output is `전체 전사본`.
- Transcription provenance is multi-dimensional: run, source, policy, inventory,
  and evidence era jointly determine authority.
- Translation separates canonical content identity from record identity and
  makes review completeness part of publication authority.
- Dashboard behavior is local-first and derives pure projections from structured
  events rather than acting as a workflow command surface.

## SKILL ROUTING

- For a YouTube URL requesting transcription, Korean source subtitles, or an
  original-language subtitle draft, load
  `.agents/skills/video-transcription/SKILL.md`.
- Its context gate is mandatory: do not start ASR or vision work until the user
  supplies episode context or explicitly answers `없음`.
- For a subtitle source snapshot requesting translation or a translation trial,
  load `.agents/skills/video-translation/SKILL.md`.
- Its bounded-chunk, separate-reviewer, revision-cap, and publication gates apply
  to every translation run, including draft-source trials.

## COMMANDS

```bash
uv run pytest
uv run ruff check .
uv run basedpyright
node --test tests/dashboard/*.test.mjs
```

## NOTES

- `src/` is only a structural container; package-level AGENTS.md files own the
  distinct runtime domains.
- Contract data directories, fixtures, and stubs inherit their nearest package or
  test guidance; route documentation work to the authoritative README or skill.
- Consult the nearest nested AGENTS.md before changing package, test, or script
  behavior; those files carry the domain-specific constraints.
