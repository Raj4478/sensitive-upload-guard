"""Decide whether a prompt or a file contains the sensitive marker.

Returns a :class:`Finding` that says where the marker was found (prompt, or a
part of a file such as "page 2" or "image text (OCR)"), with a short snippet
for the confirmation dialog.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass, field

from upload_guard import detector, extractors

MAX_FILE_BYTES = 50 * 1024 * 1024


@dataclass
class Finding:
    source: str                 # "prompt" | "file"
    detected: bool
    name: str = ""              # file name (files only)
    kind: str = ""              # "text" | "pdf" | "image" | "unknown" (files only)
    location: str = ""          # where in the file the marker was found
    snippet: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def inspected(self) -> bool:
        """False when nothing could be read (e.g. unsupported type, OCR missing)."""
        return self.detected or not self.errors

    def to_dict(self) -> dict:
        return {**asdict(self), "inspected": self.inspected}


def inspect_prompt(text: str) -> Finding:
    match = detector.find_marker(text or "")
    return Finding("prompt", match.found, snippet=match.snippet)


def inspect_file(name: str, data: bytes, mime: str = "") -> Finding:
    if len(data) > MAX_FILE_BYTES:
        return Finding("file", False, name=name, errors=[f"file is larger than {MAX_FILE_BYTES >> 20} MB"])
    extracted = extractors.extract(data, name, mime)
    for location, chunk in zip(extracted.locations, extracted.chunks):
        match = detector.find_marker(chunk)
        if match.found:
            return Finding("file", True, name=name, kind=extracted.kind, location=location,
                           snippet=match.snippet, errors=extracted.errors)
    # The marker may have been split across two chunks (e.g. the end of page 1
    # and the start of page 2): check the joined text too.
    match = detector.find_marker(extracted.text)
    return Finding("file", match.found, name=name, kind=extracted.kind,
                   location="across pages" if match.found else "", snippet=match.snippet,
                   errors=extracted.errors)


def decode_base64(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"file data is not valid base64: {exc}") from exc
