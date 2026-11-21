"""What the confirmation dialog says, for prompts, flagged files and unreadable files."""

from __future__ import annotations

from upload_guard import MARKER, dialog
from upload_guard.inspector import Finding


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def dialog_content(site: str, finding: Finding, prompt: str = "") -> dialog.Content:
    if finding.source == "prompt":
        return dialog.Content(
            window_title="Upload Guard · sensitive text prompt",
            heading="Sensitive content in a text prompt",
            body=(f"Your prompt to {site} contains the marker {MARKER}.\n\n"
                  f"“{_short(prompt or finding.snippet, 280)}”"),
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
