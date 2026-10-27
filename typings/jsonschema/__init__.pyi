# Copyright 2026 Transcription Module contributors

from pydantic import JsonValue

class Draft202012Validator:
    def __init__(self, schema: dict[str, JsonValue]) -> None: ...

    @classmethod
    def check_schema(cls, schema: dict[str, JsonValue]) -> None: ...

    def validate(self, instance: JsonValue) -> None: ...
