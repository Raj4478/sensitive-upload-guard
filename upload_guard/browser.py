"""Open a protected browser window without changing any system setting.

For a quick trial (or a locked-down PC), Upload Guard can open Chrome or Edge
with a separate profile that sends ChatGPT/Perplexity through the proxy and
trusts *only* this computer's Upload Guard certificate (by its public-key hash).
Nothing is installed and no system proxy is changed. Chrome shows a notice about
the command-line flag; that is expected in this mode.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from upload_guard import ca
from upload_guard.config import app_dir

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser() -> str | None:
    for path in CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("msedge")


def open_protected_window(pac_url: str, url: str = "https://chatgpt.com/") -> subprocess.Popen:
    exe = find_browser()
    if not exe:
        raise FileNotFoundError("Chrome or Edge was not found")
    profile = app_dir() / "protected-browser-profile"
    args = [exe, f"--user-data-dir={profile}", f"--proxy-pac-url={pac_url}",
            f"--ignore-certificate-errors-spki-list={ca.spki_hash()}",
            "--no-first-run", "--no-default-browser-check", url, "https://www.perplexity.ai/"]
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    return subprocess.Popen(args, creationflags=flags, close_fds=True)
