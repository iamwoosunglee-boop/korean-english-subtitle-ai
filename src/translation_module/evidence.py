# Copyright 2026 Transcription Module contributors

"""Root-confined local evidence resource resolution."""

import os
import stat
from pathlib import Path, PurePosixPath
from typing import ClassVar, final
from urllib.parse import unquote, urlsplit


@final
class FileEvidenceResolver:
    """Resolve file evidence beneath one explicitly configured root."""

    __slots__: ClassVar[tuple[str, ...]] = ("root",)
    root: Path

    def __init__(self, root: Path) -> None:
        """Bind all reads to one existing directory."""
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            message = f"invalid evidence root: {root}"
            raise OSError(message) from error
        if not resolved.is_dir():
            message = f"evidence root is not a directory: {root}"
            raise OSError(message)
        self.root = resolved

    def read(self, base_uri: str, locator: str) -> bytes:
        """Read a relative regular file without following symlinks."""
        base = urlsplit(base_uri)
        relative_root = (
            not base.scheme
            and not base.netloc
            and not base.query
            and not base.fragment
            and base.path in {".", "./"}
        )
        if relative_root:
            base_path = self.root
        else:
            if base.scheme != "file" or base.netloc or base.query or base.fragment:
                message = "evidence base URI must use the local file scheme"
                raise OSError(message)
            decoded_base_path = Path(unquote(base.path))
            if not decoded_base_path.is_absolute():
                message = "evidence base URI must identify an absolute directory"
                raise OSError(message)
            try:
                base_path = decoded_base_path.resolve(strict=True)
            except OSError as error:
                message = "evidence base URI is unavailable"
                raise OSError(message) from error
            if base_path != self.root:
                message = "evidence base URI does not match the configured root"
                raise OSError(message)

        parsed_locator = urlsplit(locator)
        locator_path = unquote(parsed_locator.path)
        path = PurePosixPath(locator_path)
        if (
            parsed_locator.scheme
            or parsed_locator.netloc
            or parsed_locator.query
            or parsed_locator.fragment
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in locator_path
        ):
            message = "evidence locator must be a relative file path"
            raise OSError(message)
        parts = path.parts
        if not parts:
            message = "evidence locator must identify a file"
            raise OSError(message)

        directory_fd = os.open(
            self.root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            resource_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                if not stat.S_ISREG(os.fstat(resource_fd).st_mode):
                    message = "evidence resource must be a regular file"
                    raise OSError(message)
                with os.fdopen(resource_fd, "rb", closefd=False) as resource:
                    return resource.read()
            finally:
                os.close(resource_fd)
        finally:
            os.close(directory_fd)
