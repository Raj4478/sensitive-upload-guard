"""Settings, file locations and logging for the desktop app."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "UploadGuard"


def app_dir() -> Path:
    override = os.environ.get("UPLOAD_GUARD_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_DIR_NAME.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    # Seconds before an unanswered dialog cancels the action automatically.
    dialog_timeout_seconds: int = 120
    # A supported file (text/PDF/image) that could not be read - e.g. OCR failed
    # or the PDF is password-protected: "ask" (default), "allow" or "block".
    unreadable_policy: str = "ask"
    # Keep an audit trail of detections (never the content itself, only a hash).
    audit_log: bool = True

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        path = app_dir() / "config.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if isinstance(raw.get("dialog_timeout_seconds"), int) and 5 <= raw["dialog_timeout_seconds"] <= 3600:
            settings.dialog_timeout_seconds = raw["dialog_timeout_seconds"]
        if raw.get("unreadable_policy") in ("ask", "allow", "block"):
            settings.unreadable_policy = raw["unreadable_policy"]
        if isinstance(raw.get("audit_log"), bool):
            settings.audit_log = raw["audit_log"]
        return settings


def setup_logging() -> None:
    """Log to a file only: stdout is the browser channel, and nobody reads stderr."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    try:
        log_dir = app_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_dir / "host.log", maxBytes=1_000_000,
                                                       backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    except OSError:
        pass
