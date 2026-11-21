"""Native desktop confirmation dialog with exactly two actions: Continue / Cancel.

* **Windows**: a Task Dialog (taskdialog.py) - the modern native dialog Windows
  uses for its own security prompts: warning banner, heading, and two command-link
  buttons with explanations. If it is unavailable, a Win32 ``MessageBox`` whose
  OK button is renamed to "Continue" from a CBT hook. Both stay on top of the
  browser, and Cancel is the default so a stray Enter never sends sensitive data.
* **macOS**: ``display dialog`` via osascript (an NSAlert-style system dialog).
* **Linux**: ``zenity``; Tk as a last resort.

Every variant auto-cancels after ``timeout`` seconds (the safe outcome).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

CONTINUE, CANCEL = "continue", "cancel"


@dataclass
class Content:
    """What the dialog says. Rich dialogs use every field; simple ones use plain()."""
    window_title: str
    heading: str
    body: str
    continue_note: str
    cancel_note: str

    def plain(self) -> str:
        return (f"{self.heading}\n\n{self.body}\n\n"
                f"Continue: {self.continue_note}\nCancel: {self.cancel_note}")


def confirm(content: Content, timeout: int = 120) -> str:
    """Show the dialog and return "continue" or "cancel"."""
    # Automated tests set this to answer without a person at the screen.
    scripted = os.environ.get("UPLOAD_GUARD_AUTO_ANSWER")
    if scripted in (CONTINUE, CANCEL):
        log.info("auto-answered %s (UPLOAD_GUARD_AUTO_ANSWER)", scripted)
        return scripted
    title, message = content.window_title, content.plain()
    try:
        if sys.platform == "win32":
            try:
                from upload_guard import taskdialog
                _dpi_aware()
                return taskdialog.show(content, timeout)
            except Exception:
                log.exception("Task Dialog unavailable; using a message box")
            return _windows(title, message, timeout)
        if sys.platform == "darwin":
            return _mac(title, message, timeout)
        if shutil.which("zenity"):
            return _zenity(title, message, timeout)
        return _tk(title, message, timeout)
    except Exception:
        log.exception("dialog failed; treating it as Cancel")
        return CANCEL


# ----------------------------------------------------------------------------- Windows

def _dpi_aware() -> None:
    """Crisp text on high-DPI screens."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def _windows(title: str, message: str, timeout: int) -> str:
    """Fallback: a MessageBox with its OK button renamed to Continue."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dpi_aware()

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.SetDlgItemTextW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPCWSTR]

    WH_CBT, HCBT_ACTIVATE, IDOK, IDCANCEL, TIMED_OUT = 5, 5, 1, 2, 32000
    hook = None

    @HOOKPROC
    def on_cbt(code, wparam, lparam):
        nonlocal hook
        if code == HCBT_ACTIVATE and hook:
            hwnd = wintypes.HWND(wparam)
            user32.SetDlgItemTextW(hwnd, IDOK, "&Continue")
            user32.SetDlgItemTextW(hwnd, IDCANCEL, "Cancel")
            user32.UnhookWindowsHookEx(hook)
            hook = None
        return user32.CallNextHookEx(None, code, wparam, lparam)

    MB_OKCANCEL, MB_ICONWARNING, MB_DEFBUTTON2 = 0x1, 0x30, 0x100
    MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
    flags = MB_OKCANCEL | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST

    hook = user32.SetWindowsHookExW(WH_CBT, on_cbt, None, kernel32.GetCurrentThreadId())
    try:
        # MessageBoxTimeoutW (exported by user32 since XP) adds the auto-cancel.
        box = getattr(user32, "MessageBoxTimeoutW", None)
        if box is not None:
            box.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
                            wintypes.WORD, wintypes.DWORD]
            result = box(None, message, title, flags, 0, timeout * 1000)
        else:
            user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
            result = user32.MessageBoxW(None, message, title, flags)
    finally:
        if hook:
            user32.UnhookWindowsHookEx(hook)
    if result == TIMED_OUT:
        log.info("dialog timed out after %ss; cancelled", timeout)
    return CONTINUE if result == IDOK else CANCEL


# ----------------------------------------------------------------------------- macOS

_MAC_SCRIPT = [
    "on run argv",
    "activate",
    'display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Continue"} '
    'default button "Cancel" cancel button "Cancel" with icon caution giving up after (item 3 of argv as integer)',
    'if gave up of result then return "cancel"',
    'return "continue"',
    "end run",
]


def _mac(title: str, message: str, timeout: int) -> str:
    args = ["osascript"]
    for line in _MAC_SCRIPT:
        args += ["-e", line]
    proc = subprocess.run(args + [title, message, str(timeout)], capture_output=True, text=True)
    # Cancel makes osascript exit with error -128.
    return CONTINUE if proc.returncode == 0 and proc.stdout.strip() == "continue" else CANCEL


# ----------------------------------------------------------------------------- Linux

def _zenity(title: str, message: str, timeout: int) -> str:
    proc = subprocess.run(["zenity", "--question", "--title", title, "--text", message, "--no-markup",
                           "--ok-label=Continue", "--cancel-label=Cancel", "--default-cancel",
                           "--icon-name=dialog-warning", f"--timeout={timeout}"])
    return CONTINUE if proc.returncode == 0 else CANCEL


def _tk(title: str, message: str, timeout: int) -> str:
    import tkinter as tk

    choice = {"value": CANCEL}
    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    tk.Label(root, text=message, justify="left", wraplength=480, padx=20, pady=16).pack()
    row = tk.Frame(root, pady=10)
    row.pack()

    def pick(value: str) -> None:
        choice["value"] = value
        root.destroy()

    tk.Button(row, text="Cancel", width=12, command=lambda: pick(CANCEL)).pack(side="right", padx=6)
    tk.Button(row, text="Continue", width=12, command=lambda: pick(CONTINUE)).pack(side="right", padx=6)
    root.protocol("WM_DELETE_WINDOW", lambda: pick(CANCEL))
    root.after(timeout * 1000, lambda: pick(CANCEL))
    root.mainloop()
    return choice["value"]
