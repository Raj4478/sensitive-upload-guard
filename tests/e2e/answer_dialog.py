"""Test helper: wait for the native Upload Guard dialog, report it, press a button.

    python answer_dialog.py continue|cancel [timeout_seconds]

Prints one JSON line: {"seen": bool, "text": "..."} where text is the window
title plus any readable child text. Works for both the Task Dialog and the
MessageBox fallback.
"""

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time

TITLE_PREFIX = "Upload Guard"
TDM_CLICK_BUTTON = 0x0400 + 102
WM_COMMAND = 0x0111
user32 = ctypes.windll.user32


def window_text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_dialog():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and window_text(hwnd).startswith(TITLE_PREFIX):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "#32770":          # standard dialog class (Task Dialog and MessageBox)
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


def all_text(hwnd) -> str:
    parts = [window_text(hwnd)]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(child, _):
        text = window_text(child)
        if text:
            parts.append(text)
        return True

    user32.EnumChildWindows(hwnd, each, 0)
    return "\n".join(parts)


def main() -> None:
    answer = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    end = time.time() + timeout
    hwnd = None
    while time.time() < end and not hwnd:
        hwnd = find_dialog()
        time.sleep(0.1)
    if not hwnd:
        print(json.dumps({"seen": False, "text": ""}))
        return
    time.sleep(0.5)
    text = all_text(hwnd)
    button = 1 if answer == "continue" else 2          # Continue / Cancel button IDs
    user32.PostMessageW(hwnd, TDM_CLICK_BUTTON, button, 0)   # Task Dialog
    time.sleep(0.5)
    if user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
        user32.PostMessageW(hwnd, WM_COMMAND, button, 0)     # MessageBox fallback
    print(json.dumps({"seen": True, "text": text}))


if __name__ == "__main__":
    main()
