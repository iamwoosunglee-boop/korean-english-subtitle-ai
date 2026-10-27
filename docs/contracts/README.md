# Canonical subtitle JSON contracts

The Python models under `translation_module.canonical` are the authoritative
contract implementation. The committed JSON Schemas provide the portable
structural contract consumed by Subtitles for Woody and other non-Python
clients.

## Supported canonical artifacts

| Artifact | Python root model | Example | JSON Schema |
|---|---|---|---|
| `source-transcript.v2` | `SourceTranscriptV2` | `examples/source-transcript.v2.example.json` | `json-schema/source-transcript.v2.schema.json` |
| `translation-document.v2` | `TranslationDocumentV2` | `examples/translation-document.v2.example.json` | `json-schema/translation-document.v2.schema.json` |
| `content-context.v1` | `ContentContextAsset` | `examples/content-context.v1.example.json` | `json-schema/content-context.v1.schema.json` |
| `translation-glossary.v1` | `TranslationGlossaryAsset` | `examples/translation-glossary.v1.example.json` | `json-schema/translation-glossary.v1.schema.json` |
| `translation-review.v1` | `TranslationReviewArtifact` | `examples/translation-review.v1.example.json` | `json-schema/translation-review.v1.schema.json` |

The context directory also includes `content-context.input.v1.example.json`,
which is the input-stage asset referenced by the source example. The primary
context example in the table is the prepared-stage asset referenced by the
translation example.

`source-transcript.v1` and `translation-document.v1` remain legacy read-only
contracts. They are not reinterpreted as v2 by object shape.

## Automatic transcription handoff

Successful final transcription validation writes an exact run-owned pair beside
the retained transcription authority:

- `runs/<run_id>/context.input.json` is a `content-context.v1` asset at the
  `input` stage with `ready` lifecycle status.
- `runs/<run_id>/source.json` is a `source-transcript.v2` `draft`. Subtitles
  for Woody can open it directly as a read-only source.

The source binds its paired input context by exact asset ID, revision ID,
content digest, and the relative path `context.input.json`. The pair is created
only by successful final validation. Rerunning that validation repairs a
missing, malformed, or changed generated pair from the current authoritative
run data.

This handoff does not replace `transcript.json`, create a translation, or make
the source a human-finalized record. Historical runs are not backfilled. Before
production translation, a person must later finalize the canonical source.
Explicit legacy source v1 and ASS export commands remain supported compatibility
boundaries.

## Validation responsibilities

A conforming reader performs both levels below.

1. Validate the artifact against the matching committed JSON Schema selected by
   the exact `schema_version` value.
2. Enforce the cross-field invariants implemented by the corresponding Python
   model and specified in the workflow PRD.

The second level includes rules that are intentionally not duplicated as large
JSON Schema condition trees:

- explicit JSON `null` is forbidden; optional fields are omitted;
- source and target cue sequences are one-based and contiguous;
- stable IDs are unique within their owning artifact;
- cue intervals are positive and locally ordered where required;
- `lineage_status=unassigned` requires an empty `source_cue_ids` array;
- other target lineage states require one or more source cue IDs;
- input context has no source dependency while prepared context requires one;
- resolved glossary entries require `preferred_target`;
- `decision_status` agrees with the unresolved decision list;
- draft and finalized document records have the matching finalization shape;
- references to local evidence, participants, targets, and recommendations
  resolve within the owning artifact.

All public strings are NFC-normalized and nonblank. Unknown fields are rejected
at every object level. Arrays retain semantic order.

## Change discipline

Any contract change requires all of the following in the same change:

- Python model update;
- example update;
- generated JSON Schema update;
- strict round-trip and rejection tests;
- an explicit schema version decision.

The tests compare every committed schema with `model_json_schema()` and validate
every example with both Pydantic and a Draft 2020-12 JSON Schema validator.

## Migrating legacy v1 projects

`source-transcript.v1` and `translation-document.v1` are migrated explicitly;
they are never reinterpreted as v2 by object shape.

```text
translation-cli migrate-project SOURCE_V1
  [--translation TRANSLATION_V1]
  --output-dir DIRECTORY
  [--evidence-root DIRECTORY]
```

The command writes a canonical project containing input context and source v2,
plus prepared context, glossary, and translation v2 when a translation is
supplied. It validates legacy digests and references before conversion, then
runs canonical save validation on every output before the directory is
committed atomically.

An absolute file evidence URI is retained with a diagnostic unless
`--evidence-root` is supplied. With a root, every evidence file is read through
the root-confined resolver and its SHA-256 digest must match before the output
uses a relative `base_uri`.

`migration-receipt.json` records input and output digests, warnings, cue and
evidence counts before and after conversion, and the validation result. Running
the same migration again against an identical output is a no-op; a different
pre-existing project is never overwritten.

## Preparing translation assets

A finalized source and its exact ready input-context revision produce editable
prepared-context and glossary drafts:

```text
translation-cli prepare-assets SOURCE_V2 INPUT_CONTEXT \
  --output-dir DIRECTORY
```

The preparation step carries input facts and participants forward, scopes each
source cue `context_note` as a human-confirmed fact, and seeds verified person
names when an English name is available. Both drafts bind to the exact source
revision.

After editorial changes, promote both assets together:

```text
translation-cli ready-assets SOURCE_V2 PREPARED_CONTEXT GLOSSARY \
  --output-dir DIRECTORY
```

Ready promotion is atomic. It rejects blocking unresolved decisions, stale cue
scopes, source-binding mismatches, and contradictory glossary directives at the
same scope and authority. Cue-specific decisions outrank global ones, and
`human_selected` outranks `verified` only for the selected term and scope.

## Assembling immutable reviews

Chunk reviewers write scoped reports with the exact source/context/glossary
binding and a digest of the target cues they inspected. A separate global audit
records adjacent target-boundary and glossary-entry coverage. The orchestrator
combines them with:

```text
translation-cli assemble-review \
  TRANSLATION SOURCE PREPARED_CONTEXT GLOSSARY MANIFEST \
  --output REVIEW
```

The assembler derives `ReviewCoverage`; callers cannot provide aggregate totals
directly. Missing source, target, adjacent-boundary, or glossary coverage writes
a failed review rather than a passed one. A stale dependency binding, stale
global candidate digest, unknown ID, duplicate target ownership, or mismatched
chunk scope digest rejects assembly before the output file is created.

Chunk scope digests cover only the owned target range and relevant unresolved
decisions. An unchanged chunk report can therefore be reused after another
chunk changes, while the changed chunk and candidate-wide audit must be rerun.
The resulting `translation-review.v1` remains immutable; later human finding
dispositions live only in the translation finalization record.

## Canonical bytes and digests

`translation_module.canonical.digest` is the Python reference implementation.
It applies RFC 8785 JSON Canonicalization Scheme rules with this narrower
cross-language profile:

- UTF-8 output without BOM or whitespace;
- object keys sorted by raw UTF-16 code units;
- array order preserved;
- all strings must already be NFC and contain no lone surrogate;
- only booleans, strings, objects, arrays, and I-JSON safe integers are allowed;
- floating-point values and JSON `null` are rejected.

Every artifact has two projections:

- `content_digest` hashes semantic content and excludes revision ownership,
  lifecycle/finalization, review provenance, and locator hints as specified by
  the PRD;
- `record_digest` hashes the full stored record with only `record_digest`
  itself excluded. It therefore includes the already-bound `content_digest`.

The example files are digest-bound as one dependency graph:

```text
input context → source → prepared context/glossary → translation → review
```

C# implementations must validate against
`docs/contracts/golden/canonical-digests.v1.json`. Each vector includes the
input/projection, canonical JSON text, canonical UTF-8 hex, and SHA-256 digest.
Regenerate the bound examples and vectors with:

```text
uv run python scripts/generate_canonical_digest_vectors.py
```

The generator is deterministic; a second invocation must produce no diff.

## Validation tiers and lifecycle transitions

`translation_module.canonical.validation` keeps persistence, diagnostics, and
human lifecycle transitions separate.

### Save validation

`validate_canonical_save()` revalidates the public model from its JSON payload,
checks canonical bytes and both stored digests, and rejects target display-order
or repeated-lineage corruption. A draft with `lineage_status=unassigned` remains
saveable because incomplete editorial work is not structural corruption.

### Draft diagnostics

`draft_translation_diagnostics()` accepts a `TranslationValidationContext`
containing the resolved source, prepared context, glossary, review artifacts,
and review profile. It reports incomplete lineage, coverage, missing or
mismatched dependencies, unresolved nonblocking assets, review state, and
nonportable evidence locators without mutating the document.

### Finalization validation

`validate_translation_finalization()` upgrades finalization conditions to
blocking diagnostics. It requires exact finalized/ready dependencies, confirmed
lineage and full source coverage, valid target context/evidence references, no
open human translation decisions, and an acknowledgement matching the effective
review state.

Review state is calculated, never stored in the translation document:

- `not_run`: no related review artifact exists;
- `passed`: one unsuperseded exact review passed;
- `failed`: one unsuperseded exact review failed;
- `stale`: related reviews exist but none match the current binding.

Multiple unsuperseded exact reviews produce a separate `review_conflict`
diagnostic and block finalization. A failed exact review can be human-finalized
only when coverage is complete, the acknowledgement is
`failed_findings_disposed`, and every blocking finding has an exact
`review_id`/`finding_id` disposition. There is no blanket bypass.

Human transition helpers are pure copy operations followed by digest rebinding:

```text
finalize_source
ready_context_asset / ready_glossary_asset
finalize_translation
create_source_draft_revision / create_translation_draft_revision
```

Finalized source and translation revisions are never edited in place. Copy on
edit creates a new draft revision with `supersedes_revision_id`, clears the
finalization record, preserves semantic content digest when content is
unchanged, and changes record identity. AI review evaluation never changes
lifecycle state.
