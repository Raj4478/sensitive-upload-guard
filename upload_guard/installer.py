"""Register the desktop app as a native-messaging host for Chrome, Edge and Brave.

A browser finds a native host through a small JSON "host manifest":

* **Windows**: a registry value under HKEY_CURRENT_USER (per user, no admin
  rights) points at the manifest file.
* **macOS / Linux**: the manifest is placed in each browser's
  ``NativeMessagingHosts`` folder.

The manifest names the program to start (a small launcher script that runs
``python -m upload_guard.host``) and the only extension allowed to talk to it
(``allowed_origins``), so other extensions and web pages cannot use the host.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from upload_guard import HOST_NAME
from upload_guard.config import app_dir

# Fixed by the "key" in extension/manifest.json, so it is the same on every machine.
EXTENSION_ID = "aioadbeodagpdhphhhodimcmfghmadoj"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

WINDOWS_KEYS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
}
MAC_DIRS = {
    "Chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
    "Edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
    "Brave": "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "Chromium": "Library/Application Support/Chromium/NativeMessagingHosts",
}
LINUX_DIRS = {
    "Chrome": ".config/google-chrome/NativeMessagingHosts",
    "Edge": ".config/microsoft-edge/NativeMessagingHosts",
    "Brave": ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
    "Chromium": ".config/chromium/NativeMessagingHosts",
}


def _write_launcher(folder: Path) -> Path:
    """A tiny script that starts the host with this Python and this source tree."""
    python = sys.executable
    if sys.platform == "win32":
        launcher = folder / "upload_guard_host.bat"
        launcher.write_text(
            "@echo off\r\n"
            f'set "PYTHONPATH={PROJECT_ROOT}"\r\n'
            f'"{python}" -u -m upload_guard.host %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "upload_guard_host.sh"
        launcher.write_text(
            "#!/bin/sh\n"
            f'export PYTHONPATH="{PROJECT_ROOT}"\n'
            f'exec "{python}" -u -m upload_guard.host "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def host_manifest(launcher: Path, extension_id: str = EXTENSION_ID) -> dict:
    return {
        "name": HOST_NAME,
        "description": "Sensitive Upload Guard desktop app",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def install(extension_id: str = EXTENSION_ID) -> list[str]:
    folder = app_dir() / "native-host"
    folder.mkdir(parents=True, exist_ok=True)
    launcher = _write_launcher(folder)
    manifest = json.dumps(host_manifest(launcher, extension_id), indent=2)
    done = []
    if sys.platform == "win32":
        import winreg
        manifest_path = folder / f"{HOST_NAME}.json"
        manifest_path.write_text(manifest, encoding="utf-8")
        for browser, key in WINDOWS_KEYS.items():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}") as handle:
                winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, str(manifest_path))
            done.append(f"{browser}: HKCU\\{key}\\{HOST_NAME}")
    else:
        dirs = MAC_DIRS if sys.platform == "darwin" else LINUX_DIRS
        for browser, rel in dirs.items():
            target = Path.home() / rel
            if not target.parent.exists():
                continue   # browser not installed
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{HOST_NAME}.json").write_text(manifest, encoding="utf-8")
            done.append(f"{browser}: {target / (HOST_NAME + '.json')}")
    return done


def uninstall() -> list[str]:
    removed = []
    if sys.platform == "win32":
        import winreg
        for browser, key in WINDOWS_KEYS.items():
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}")
                removed.append(browser)
            except FileNotFoundError:
                pass
    else:
        dirs = MAC_DIRS if sys.platform == "darwin" else LINUX_DIRS
        for browser, rel in dirs.items():
            path = Path.home() / rel / f"{HOST_NAME}.json"
            if path.exists():
                path.unlink()
                removed.append(browser)
    return removed
