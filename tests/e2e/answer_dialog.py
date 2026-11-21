"""Test helper: wait for the native Upload Guard dialog, print its text, press a button.

    python answer_dialog.py continue|cancel|none [timeout_seconds]

Prints one JSON line: {"seen": bool, "text": "..."}. With "none" it only reports
whether a dialog appeared (and closes it with Cancel so nothing is left open).
"""

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time

TITLE_PREFIXES = ("Sensitive content detected", "File could not be checked")
user32 = ctypes.windll.user32


def find_dialog():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(TITLE_PREFIXES):
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


def dialog_text(hwnd) -> str:
    parts = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(child, _):
        n = user32.GetWindowTextLengthW(child)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(child, buf, n + 1)
            parts.append(buf.value)
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
    time.sleep(0.4)
    text = dialog_text(hwnd)
    button = 1 if answer == "continue" else 2          # IDOK (renamed "Continue") / IDCANCEL
    user32.PostMessageW(hwnd, 0x0111, button, 0)        # WM_COMMAND
    print(json.dumps({"seen": True, "text": text}))


if __name__ == "__main__":
    main()
