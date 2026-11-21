"""Chrome native-messaging host: the desktop side of the extension.

Chrome starts this program when the extension calls
``chrome.runtime.sendNativeMessage``. Messages travel over stdin/stdout, each
one a 4-byte little-endian length followed by UTF-8 JSON (Chrome's protocol).
Nothing else may ever be written to stdout.

Requests (from extension/background.js)::

    {"type": "ping"}
    {"type": "inspect_prompt", "site": "ChatGPT", "text": "..."}
    {"type": "inspect_file", "site": "Perplexity", "name": "a.pdf", "mime": "application/pdf",
     "size": 1234, "data": "<base64>"}

Reply::

    {"decision": "allow" | "continue" | "cancel", "finding": {...}}

"allow" means no marker (no dialog was shown). "continue"/"cancel" is the
user's answer in the native dialog.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import sys
import time
from typing import Any, BinaryIO

from upload_guard import MARKER, __version__, dialog, extractors, inspector
from upload_guard.config import Settings, app_dir, setup_logging

log = logging.getLogger(__name__)

MAX_MESSAGE = 64 * 1024 * 1024    # Chrome's limit for messages sent to a host
SUPPORTED_KINDS = {"text", "pdf", "image"}


# ----------------------------------------------------------------------------- protocol

def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if len(header) < 4:
        return None   # Chrome closed the pipe
    (length,) = struct.unpack("<I", header)
    if length > MAX_MESSAGE:
        raise ValueError(f"message too large: {length} bytes")
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            raise EOFError("pipe closed mid-message")
        body += chunk
    return json.loads(body.decode("utf-8"))


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(body)) + body)
    stream.flush()


# ----------------------------------------------------------------------------- decisions

def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def dialog_content(site: str, finding: inspector.Finding, prompt: str = "") -> dialog.Content:
    """The words in the confirmation dialog for each case."""
    if finding.source == "prompt":
        return dialog.Content(
            window_title="Upload Guard · sensitive text prompt",
            heading="Sensitive content in a text prompt",
            body=(f"Your prompt to {site} contains the marker {MARKER}.\n\n"
                  f"“{_short(prompt, 280)}”"),
            continue_note="Send the prompt unchanged",
            cancel_note="Don’t send it. Your text stays in the box so you can edit it.")
    kind = {"text": "text file", "pdf": "PDF", "image": "image"}.get(finding.kind, "file")
    if finding.detected:
        return dialog.Content(
            window_title=f"Upload Guard · sensitive file: {finding.name}",
            heading="Sensitive content in a file",
            body=(f"{finding.name} ({kind}) is being uploaded to {site} and contains the marker {MARKER}.\n\n"
                  f"Found in: {finding.location}\n"
                  f"“{_short(finding.snippet, 160)}”"),
            continue_note="Upload the file unchanged",
            cancel_note="Don’t upload it. You can choose another file.")
    return dialog.Content(
        window_title=f"Upload Guard · file could not be checked: {finding.name}",
        heading="This file could not be checked",
        body=(f"Upload Guard could not read {finding.name} ({kind}), which is being uploaded to {site}, "
              f"so it cannot tell whether it contains {MARKER}.\n\n"
              f"Reason: {_short('; '.join(finding.errors), 200)}"),
        continue_note="Upload it anyway",
        cancel_note="Don’t upload it")


def handle(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    kind = request.get("type")
    site = str(request.get("site") or "the website")[:40]
    if kind == "ping":
        return {"ok": True, "version": __version__, "platform": sys.platform}
    if kind == "inspect_prompt":
        text = str(request.get("text") or "")
        finding = inspector.inspect_prompt(text)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    elif kind == "inspect_file":
        name = str(request.get("name") or "file")[:200]
        mime = str(request.get("mime") or "")
        text = ""
        if request.get("too_large"):
            # The browser did not send the bytes (over 50 MB): it cannot be checked.
            finding = inspector.Finding("file", False, name=name, kind=extractors.sniff(b"", name, mime),
                                        errors=[f"the file is too large to check ({request.get('size')} bytes)"])
            content_hash = ""
        else:
            data = inspector.decode_base64(str(request.get("data") or ""))
            finding = inspector.inspect_file(name, data, mime)
            content_hash = hashlib.sha256(data).hexdigest()
    else:
        return {"error": f"unknown request type {kind!r}"}

    needs_decision = finding.detected or (
        finding.source == "file" and not finding.inspected and finding.kind in SUPPORTED_KINDS)
    if not needs_decision:
        decision = "allow"
    elif not finding.detected and settings.unreadable_policy != "ask":
        decision = "continue" if settings.unreadable_policy == "allow" else "cancel"
    else:
        decision = dialog.confirm(dialog_content(site, finding, text), settings.dialog_timeout_seconds)

    log.info("%s %s %s -> %s (%s)", site, finding.source, finding.name or "prompt", decision,
             finding.location or ("marker" if finding.detected else "clean"))
    if settings.audit_log and needs_decision:
        _audit(site, finding, decision, content_hash)
    return {"decision": decision, "finding": finding.to_dict()}


def _audit(site: str, finding: inspector.Finding, decision: str, content_hash: str) -> None:
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": site, "source": finding.source,
             "name": finding.name, "kind": finding.kind, "detected": finding.detected,
             "location": finding.location, "decision": decision, "sha256": content_hash}
    try:
        with (app_dir() / "audit.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("could not write the audit log")


# ----------------------------------------------------------------------------- main loop

def main() -> int:
    setup_logging()
    settings = Settings.load()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    # Anything printed by accident must not corrupt the channel.
    sys.stdout = sys.stderr
    log.info("host started (args: %s)", sys.argv[1:])
    while True:
        try:
            request = read_message(stdin)
        except (ValueError, EOFError) as exc:
            log.error("bad message: %s", exc)
            return 1
        if request is None:
            return 0
        try:
            reply = handle(request, settings)
        except Exception as exc:
            log.exception("request failed")
            reply = {"error": str(exc)}
        if "id" in request:
            reply["id"] = request["id"]
        write_message(stdout, reply)


if __name__ == "__main__":
    raise SystemExit(main())
