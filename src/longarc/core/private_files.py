"""Shared owner-only local JSON storage for broker credentials and evidence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class PrivateFileError(ValueError):
    """An actionable local-file error that never contains file contents."""


def write_private_json(path: Path, payload: object, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".longarc-private-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.write("\n")
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private_json(path: Path) -> dict[str, object]:
    try:
        if path.stat().st_mode & 0o077:
            raise PrivateFileError("Private file must have permissions 600.")
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        if isinstance(error, PrivateFileError):
            raise
        raise PrivateFileError(
            "Cannot read private JSON file; check setup and permissions."
        ) from None
    if not isinstance(data, dict):
        raise PrivateFileError("Private JSON file must contain an object.")
    return data
