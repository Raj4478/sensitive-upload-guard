"""Turn an outgoing HTTP request body into "prompt text" and "files".

The proxy sees raw request bodies. Three shapes cover both sites:

* **JSON / form fields / plain text** (sending a prompt): the strings inside are
  searched; the longest one containing the marker is the prompt shown in the dialog.
* **multipart/form-data** (e.g. a presigned S3 upload): file parts are files
  (with their own file names); the other fields are text.
* **anything else** (e.g. a raw PUT to blob storage, image/*, application/pdf):
  the whole body is one file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from urllib.parse import parse_qsl, unquote

from upload_guard import detector

TEXT_TYPES = ("json", "x-www-form-urlencoded", "text/plain", "graphql")
MAX_TEXT_BODY = 2_000_000


@dataclass
class UploadedFile:
    name: str
    data: bytes
    mime: str = ""


@dataclass
class Payload:
    text: str = ""                       # all text found in the body, for matching
    prompt: str = ""                     # the string that contains the marker (best guess at the prompt)
    files: list[UploadedFile] = field(default_factory=list)

    @property
    def has_marker_text(self) -> bool:
        return detector.find_marker(self.text).found


def _strings(value) -> list[str]:
    """Every string inside a JSON value (keys excluded)."""
    if isinstance(value, str):
        out = [value]
        # Some apps send JSON inside a JSON string; look inside it too.
        if value[:1] in "{[":
            try:
                out += _strings(json.loads(value))
            except ValueError:
                pass
        return out
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _best_prompt(strings: list[str]) -> str:
    """The string to show as "the prompt": real text beats a JSON blob that wraps it."""
    hits = [s for s in strings if detector.find_marker(s).found]
    plain = [s for s in hits if s.lstrip()[:1] not in ("{", "[")]
    pool = plain or hits
    return max(pool, key=len) if pool else ""


def parse(body: bytes, content_type: str, url_path: str = "") -> Payload:
    ctype = (content_type or "").lower()
    if not body:
        return Payload()
    if "multipart/form-data" in ctype:
        return _multipart(body, content_type)
    if any(t in ctype for t in TEXT_TYPES) or (not ctype and len(body) <= MAX_TEXT_BODY and _is_text(body)):
        return _text(body, ctype)
    name = unquote(url_path.rstrip("/").rsplit("/", 1)[-1].split("?")[0]) or "uploaded file"
    return Payload(files=[UploadedFile(name, body, ctype.split(";")[0])])


def _is_text(body: bytes) -> bool:
    return b"\x00" not in body[:4096]


def _text(body: bytes, ctype: str) -> Payload:
    raw = body[:MAX_TEXT_BODY].decode("utf-8", "replace")
    strings: list[str] = []
    if "json" in ctype or raw.lstrip()[:1] in "{[":
        try:
            strings = _strings(json.loads(raw))
        except ValueError:
            strings = []
    if not strings and "x-www-form-urlencoded" in ctype:
        for _, value in parse_qsl(raw, keep_blank_values=True):
            strings += _strings(value)
            if value[:1] in "{[":
                try:
                    strings += _strings(json.loads(value))
                except ValueError:
                    pass
    if not strings:
        strings = [raw]
    return Payload(text="\n".join(strings), prompt=_best_prompt(strings))


def _multipart(body: bytes, content_type: str) -> Payload:
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    payload = Payload()
    texts: list[str] = []
    for part in message.iter_parts():
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename:
            payload.files.append(UploadedFile(filename, data, part.get_content_type()))
        else:
            texts.append(data.decode("utf-8", "replace"))
    payload.text = "\n".join(texts)
    payload.prompt = _best_prompt(texts)
    return payload


def file_names_announced(body: bytes) -> list[tuple[str, int | None]]:
    """(name, size) pairs a site announces before uploading, e.g. {"file_name": "a.pdf", "file_size": 12}."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return []
    found = []

    def walk(value):
        if isinstance(value, dict):
            name = next((value[k] for k in ("file_name", "fileName", "filename", "name") if isinstance(value.get(k), str)), None)
            size = next((value[k] for k in ("file_size", "fileSize", "size", "content_length") if isinstance(value.get(k), int)), None)
            if name and re.search(r"\.\w{1,5}$", name):
                found.append((name, size))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(data)
    return found


def urls_in(body: bytes) -> list[str]:
    """Absolute URLs in a response body (to learn where uploads will go)."""
    text = body[:500_000].decode("utf-8", "replace").replace("\\/", "/")
    return re.findall(r"https://[A-Za-z0-9.\-]+(?::\d+)?/[^\s\"'<>\\]*", text)
