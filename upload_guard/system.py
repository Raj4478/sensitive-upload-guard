"""The two one-time system steps, each started only by the user from the app window.

1. **Trust the local CA** so browsers accept the proxy's certificates for the
   two sites. Windows itself shows a security prompt the user must confirm.
   Stored per user (CurrentUser\\Root), no admin rights.
2. **Route the browser through the proxy** for the two sites only, by setting the
   per-user "automatic proxy configuration" (PAC) URL that Chrome, Edge and
   Firefox (with system proxy settings) follow. Turning protection off restores
   the previous setting exactly.

Both are reversible from the app (Remove certificate / Turn protection off).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from upload_guard import ca
from upload_guard.config import app_dir

log = logging.getLogger(__name__)

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, creationflags=_NO_WINDOW)


# ============================================================================ certificate

def certificate_installed() -> bool:
    if sys.platform == "win32":
        thumb = ca.thumbprint()
        result = _run(["powershell.exe", "-NoProfile", "-Command",
                       f"Test-Path Cert:\\CurrentUser\\Root\\{thumb}"])
        return result.stdout.strip().lower() == "true"
    if sys.platform == "darwin":
        result = _run(["security", "find-certificate", "-c", ca.COMMON_NAME])
        return result.returncode == 0
    return False


def install_certificate() -> tuple[bool, str]:
    """Add the CA to the user's trusted roots. Windows asks the user to confirm."""
    cert = ca.ensure_ca() / "mitmproxy-ca-cert.cer"
    if sys.platform == "win32":
        result = _run(["certutil", "-user", "-addstore", "Root", str(cert)])
    elif sys.platform == "darwin":
        keychain = str(__import__("pathlib").Path.home() / "Library/Keychains/login.keychain-db")
        result = _run(["security", "add-trusted-cert", "-r", "trustRoot", "-k", keychain,
                       str(ca.ensure_ca() / "mitmproxy-ca-cert.pem")])
    else:
        return False, "Automatic install is not supported on this OS; trust mitmproxy-ca-cert.pem manually."
    ok = result.returncode == 0 and certificate_installed()
    return ok, (result.stdout + result.stderr).strip()[-300:]


def remove_certificate() -> tuple[bool, str]:
    if sys.platform == "win32":
        result = _run(["certutil", "-user", "-delstore", "Root", ca.thumbprint()])
    elif sys.platform == "darwin":
        result = _run(["security", "delete-certificate", "-c", ca.COMMON_NAME])
    else:
        return False, "not supported on this OS"
    return result.returncode == 0, (result.stdout + result.stderr).strip()[-300:]


# ============================================================================ browser routing (Windows)

def _notify_windows() -> None:
    import ctypes
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(None, 39, None, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(None, 37, None, 0)   # INTERNET_OPTION_REFRESH


def routing_enabled(pac_url: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS) as key:
            return winreg.QueryValueEx(key, "AutoConfigURL")[0] == pac_url
    except OSError:
        return False


def enable_routing(pac_url: str) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "On macOS set System Settings > Network > Proxies > Automatic proxy configuration to " + pac_url
    import winreg
    backup = app_dir() / "previous-proxy.json"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            previous = winreg.QueryValueEx(key, "AutoConfigURL")[0]
        except OSError:
            previous = None
        if previous != pac_url and not backup.exists():
            backup.write_text(json.dumps({"AutoConfigURL": previous}), encoding="utf-8")
        winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, pac_url)
    _notify_windows()
    return True, "Browsers now send ChatGPT and Perplexity through Upload Guard."


def disable_routing(pac_url: str) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "not supported on this OS"
    import winreg
    backup = app_dir() / "previous-proxy.json"
    previous = None
    if backup.exists():
        try:
            previous = json.loads(backup.read_text(encoding="utf-8")).get("AutoConfigURL")
        except ValueError:
            previous = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current = winreg.QueryValueEx(key, "AutoConfigURL")[0]
        except OSError:
            current = None
        if current == pac_url:
            if previous:
                winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, previous)
            else:
                winreg.DeleteValue(key, "AutoConfigURL")
    try:
        backup.unlink()
    except OSError:
        pass
    _notify_windows()
    return True, "Protection is off. Your previous proxy setting was restored."
