# Copyright 2026 Transcription Module contributors

"""Bind canonical examples and publish cross-language digest golden vectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from translation_module.canonical import (
    CanonicalArtifact,
    ContentContextAsset,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
    TranslationReviewArtifact,
    bind_canonical_digests,
    canonical_content_projection,
    canonical_json_bytes,
    canonical_record_projection,
    canonical_sha256,
)

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
GOLDEN = ROOT / "docs" / "contracts" / "golden" / "canonical-digests.v1.json"

ARTIFACT_NAMES: tuple[str, ...] = (
    "source-transcript.v2",
    "translation-document.v2",
    "content-context.v1",
    "content-context.input.v1",
    "translation-glossary.v1",
    "translation-review.v1",
)

JCS_INPUTS: tuple[tuple[str, object], ...] = (
    (
        "utf16-property-order-nfc",
        {
            "€": "Euro Sign",
            "\r": "Carriage Return",
            "\ue000": "Private Use After Surrogate Pair",
            "1": "One",
            "😀": "Emoji: Grinning Face",
            "\x80": "Control",
            "ö": "Latin Small Letter O With Diaeresis",
        },
    ),
    (
        "nested-string-escaping",
        {
            "z": [{"b": 2, "a": 1}, True],
            "string": "€$\x0f\nA'B\"\\\\\"/",
        },
    ),
    (
        "safe-integer-boundaries",
        {
            "maximum": (1 << 53) - 1,
            "minimum": -((1 << 53) - 1),
            "zero": 0,
        },
    ),
)


def _read_json(name: str) -> dict[str, object]:
    value = cast(
        "object",
        json.loads(
            (EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8")
        ),
    )
    return cast("dict[str, object]", value)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _update_reference(
    reference: object,
    *,
    identity_key: str,
    identity: str,
    revision_id: str,
    content_digest: str,
) -> None:
    mapping = cast("dict[str, object]", reference)
    mapping[identity_key] = identity
    mapping["revision_id"] = revision_id
    mapping["content_digest"] = content_digest


def _bind_dependency_graph() -> dict[str, CanonicalArtifact]:
    input_context = bind_canonical_digests(
        ContentContextAsset.model_validate(_read_json("content-context.input.v1"))
    )

    source_payload = _read_json("source-transcript.v2")
    _update_reference(
        source_payload["input_context_ref"],
        identity_key="asset_id",
        identity=input_context.asset_id,
        revision_id=input_context.revision_id,
        content_digest=input_context.content_digest,
    )
    source = bind_canonical_digests(SourceTranscriptV2.model_validate(source_payload))

    context_payload = _read_json("content-context.v1")
    _update_reference(
        context_payload["prepared_from_source_ref"],
        identity_key="document_id",
        identity=source.document_id,
        revision_id=source.revision_id,
        content_digest=source.content_digest,
    )
    context = bind_canonical_digests(
        ContentContextAsset.model_validate(context_payload)
    )

    glossary_payload = _read_json("translation-glossary.v1")
    _update_reference(
        glossary_payload["prepared_from_source_ref"],
        identity_key="document_id",
        identity=source.document_id,
        revision_id=source.revision_id,
        content_digest=source.content_digest,
    )
    glossary = bind_canonical_digests(
        TranslationGlossaryAsset.model_validate(glossary_payload)
    )

    translation_payload = _read_json("translation-document.v2")
    _update_reference(
        translation_payload["source_ref"],
        identity_key="document_id",
        identity=source.document_id,
        revision_id=source.revision_id,
        content_digest=source.content_digest,
    )
    _update_reference(
        translation_payload["prepared_context_ref"],
        identity_key="asset_id",
        identity=context.asset_id,
        revision_id=context.revision_id,
        content_digest=context.content_digest,
    )
    _update_reference(
        translation_payload["glossary_ref"],
        identity_key="asset_id",
        identity=glossary.asset_id,
        revision_id=glossary.revision_id,
        content_digest=glossary.content_digest,
    )
    translation = bind_canonical_digests(
        TranslationDocumentV2.model_validate(translation_payload)
    )

    review_payload = _read_json("translation-review.v1")
    binding = cast("dict[str, object]", review_payload["binding"])
    binding.update(
        {
            "translation_document_id": translation.document_id,
            "translation_revision_id": translation.revision_id,
            "candidate_content_digest": translation.content_digest,
            "source_document_id": source.document_id,
            "source_revision_id": source.revision_id,
            "source_content_digest": source.content_digest,
            "prepared_context_asset_id": context.asset_id,
            "prepared_context_revision_id": context.revision_id,
            "prepared_context_content_digest": context.content_digest,
            "glossary_asset_id": glossary.asset_id,
            "glossary_revision_id": glossary.revision_id,
            "glossary_content_digest": glossary.content_digest,
        }
    )
    review = bind_canonical_digests(
        TranslationReviewArtifact.model_validate(review_payload)
    )

    return {
        "content-context.input.v1": input_context,
        "source-transcript.v2": source,
        "content-context.v1": context,
        "translation-glossary.v1": glossary,
        "translation-document.v2": translation,
        "translation-review.v1": review,
    }


def _jcs_vector(name: str, value: object) -> dict[str, object]:
    canonical = canonical_json_bytes(value)
    return {
        "name": name,
        "input": value,
        "canonical_json": canonical.decode("utf-8"),
        "canonical_utf8_hex": canonical.hex(),
        "sha256": canonical_sha256(value),
    }


def _artifact_vector(
    name: str,
    artifact: CanonicalArtifact,
) -> dict[str, object]:
    bound = bind_canonical_digests(artifact)
    content_projection = canonical_content_projection(bound)
    content_bytes = canonical_json_bytes(content_projection)
    record_projection = canonical_record_projection(bound)
    record_bytes = canonical_json_bytes(record_projection)
    return {
        "artifact": name,
        "content_projection": content_projection,
        "content_canonical_json": content_bytes.decode("utf-8"),
        "content_utf8_hex": content_bytes.hex(),
        "content_digest": bound.content_digest,
        "record_projection": record_projection,
        "record_canonical_json": record_bytes.decode("utf-8"),
        "record_utf8_hex": record_bytes.hex(),
        "record_digest": bound.record_digest,
    }


def main() -> None:
    """Rebind examples and regenerate the canonical digest vector artifact."""
    artifacts = _bind_dependency_graph()
    for name, artifact in artifacts.items():
        _write_json(
            EXAMPLES / f"{name}.example.json",
            artifact.model_dump(mode="json", exclude_none=True),
        )

    vectors = {
        "schema_version": "canonical-digest-vectors.v1",
        "profile": {
            "base": "RFC 8785 JCS",
            "encoding": "UTF-8 without BOM",
            "unicode": "NFC required",
            "numbers": "I-JSON safe integers only",
            "null": "forbidden",
            "arrays": "order preserved",
            "object_keys": "raw UTF-16 code-unit order",
        },
        "jcs_vectors": [
            _jcs_vector(name, value) for name, value in JCS_INPUTS
        ],
        "artifact_vectors": [
            _artifact_vector(name, artifacts[name]) for name in ARTIFACT_NAMES
        ],
    }
    _write_json(GOLDEN, vectors)


if __name__ == "__main__":
    main()
