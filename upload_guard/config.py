"""Settings, file locations and logging for the desktop app."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import os
import sys
from dataclasses import asdict, dataclass
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
    # Colour applied to AI-generated responses on both sites.
    response_color: str = "#0ea5e9"
    response_color_enabled: bool = True
    # Local ports: the inspecting proxy, and the tiny server that tells the
    # browser which sites to send through it (proxy auto-config file).
    proxy_port: int = 8899
    pac_port: int = 8898

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
        if isinstance(raw.get("response_color"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", raw["response_color"]):
            settings.response_color = raw["response_color"]
        if isinstance(raw.get("response_color_enabled"), bool):
            settings.response_color_enabled = raw["response_color_enabled"]
        for key in ("proxy_port", "pac_port"):
            if isinstance(raw.get(key), int) and 1024 <= raw[key] <= 65535:
                setattr(settings, key, raw[key])
        return settings

    def save(self) -> None:
        path = app_dir() / "config.json"
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")


def setup_logging() -> None:
    """Log to a rotating file in the app folder."""
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
