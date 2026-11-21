"""Windows Task Dialog: the modern native dialog Windows uses for its own prompts.

Compared with a plain MessageBox it gives a large heading, a coloured warning
banner, body text, and two large "command link" buttons, each with a one-line
explanation underneath:

    ┌───────────────────────────────────────────────────────────┐
    │ ▓▓ (yellow banner)  Sensitive content in a file         ▓▓ │
    │  report.pdf is being uploaded to Perplexity and contains   │
    │  the marker WALLED_SENSITIVE. ...                          │
    │  →  Continue                                               │
    │     Upload the file unchanged                              │
    │  →  Cancel                                                 │
    │     Do not upload it                                       │
    │  ⓘ Upload Guard · auto-cancels in 118 s                    │
    └───────────────────────────────────────────────────────────┘

TaskDialogIndirect lives in version 6 of the Common Controls library, which a
process only gets through a side-by-side manifest. python.exe has none, so we
create an activation context from a tiny manifest at runtime before loading
comctl32. The structures are declared with 1-byte packing, as in commctrl.h.
"""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes

CONTINUE_ID, CANCEL_ID = 1, 2          # IDOK / IDCANCEL, so Esc and the close box mean Cancel

# Documented icons are MAKEINTRESOURCE(-1..-4); the banner variants below are the
# ones Windows uses for its own security prompts (yellow / red header band).
ICON_WARNING_BANNER = 0xFFFA
ICON_INFORMATION = 0xFFFD

TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
TDF_USE_COMMAND_LINKS = 0x0010
TDF_CALLBACK_TIMER = 0x0800
TDF_SIZE_TO_CONTENT = 0x01000000
TDN_CREATED, TDN_TIMER = 0, 4
TDM_CLICK_BUTTON = 0x0400 + 102
TDM_UPDATE_ELEMENT_TEXT = 0x0400 + 114
TDE_FOOTER = 2

_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency><dependentAssembly>
    <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
      processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
  </dependentAssembly></dependency>
</assembly>"""


class _Button(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("nButtonID", ctypes.c_int), ("pszButtonText", wintypes.LPCWSTR)]


_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                               wintypes.LPARAM, ctypes.c_ssize_t)


class _Config(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("cbSize", wintypes.UINT), ("hwndParent", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", wintypes.DWORD), ("dwCommonButtons", wintypes.DWORD),
        ("pszWindowTitle", wintypes.LPCWSTR), ("pszMainIcon", ctypes.c_void_p),
        ("pszMainInstruction", wintypes.LPCWSTR), ("pszContent", wintypes.LPCWSTR),
        ("cButtons", wintypes.UINT), ("pButtons", ctypes.POINTER(_Button)), ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", wintypes.UINT), ("pRadioButtons", ctypes.c_void_p), ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", wintypes.LPCWSTR), ("pszExpandedInformation", wintypes.LPCWSTR),
        ("pszExpandedControlText", wintypes.LPCWSTR), ("pszCollapsedControlText", wintypes.LPCWSTR),
        ("pszFooterIcon", ctypes.c_void_p), ("pszFooter", wintypes.LPCWSTR),
        ("pfCallback", _CALLBACK), ("lpCallbackData", ctypes.c_ssize_t), ("cxWidth", wintypes.UINT),
    ]


class _ActCtx(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("dwFlags", wintypes.DWORD), ("lpSource", wintypes.LPCWSTR),
                ("wProcessorArchitecture", wintypes.USHORT), ("wLangId", wintypes.WORD),
                ("lpAssemblyDirectory", wintypes.LPCWSTR), ("lpResourceName", wintypes.LPCWSTR),
                ("lpApplicationName", wintypes.LPCWSTR), ("hModule", wintypes.HMODULE)]


def _task_dialog_function():
    """Load TaskDialogIndirect from Common Controls v6 (via an activation context)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateActCtxW.restype = wintypes.HANDLE
    fd, path = tempfile.mkstemp(suffix=".manifest")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_MANIFEST)
    try:
        ctx = _ActCtx(cbSize=ctypes.sizeof(_ActCtx), lpSource=path)
        handle = kernel32.CreateActCtxW(ctypes.byref(ctx))
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError("CreateActCtx failed")
        cookie = ctypes.c_size_t()
        if not kernel32.ActivateActCtx(wintypes.HANDLE(handle), ctypes.byref(cookie)):
            raise OSError("ActivateActCtx failed")
        comctl32 = ctypes.WinDLL("comctl32")
        fn = comctl32.TaskDialogIndirect
        fn.argtypes = [ctypes.POINTER(_Config), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                       ctypes.POINTER(wintypes.BOOL)]
        fn.restype = ctypes.c_long
        return fn
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def show(content, timeout: int) -> str:
    """``content`` is a dialog.Content."""
    """Show the Task Dialog; return "continue" or "cancel". Raises OSError if unavailable."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    task_dialog = _task_dialog_function()

    buttons = (_Button * 2)(
        _Button(CONTINUE_ID, f"Continue\n{content.continue_note}"),
        _Button(CANCEL_ID, f"Cancel\n{content.cancel_note}"),
    )
    footer = f"Upload Guard  ·  auto-cancels in {timeout} s"

    @_CALLBACK
    def on_event(hwnd, notification, wparam, lparam, _ref):
        if notification == TDN_CREATED:
            # Keep it above the browser and bring it forward.
            user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user32.SetForegroundWindow(hwnd)
        elif notification == TDN_TIMER:
            left = timeout - int(wparam) // 1000
            if left <= 0:
                user32.SendMessageW(hwnd, TDM_CLICK_BUTTON, CANCEL_ID, 0)
            else:
                user32.SendMessageW(hwnd, TDM_UPDATE_ELEMENT_TEXT, TDE_FOOTER,
                                    ctypes.c_wchar_p(f"Upload Guard  ·  auto-cancels in {left} s"))
        return 0

    config = _Config()
    config.cbSize = ctypes.sizeof(_Config)
    config.dwFlags = TDF_USE_COMMAND_LINKS | TDF_ALLOW_DIALOG_CANCELLATION | TDF_CALLBACK_TIMER | TDF_SIZE_TO_CONTENT
    config.pszWindowTitle = content.window_title
    config.pszMainIcon = ICON_WARNING_BANNER
    config.pszMainInstruction = content.heading
    config.pszContent = content.body
    config.cButtons = 2
    config.pButtons = buttons
    config.nDefaultButton = CANCEL_ID          # Enter = Cancel: the safe choice
    config.pszFooterIcon = ICON_INFORMATION
    config.pszFooter = footer
    config.pfCallback = on_event
    config.cxWidth = 0

    pressed = ctypes.c_int(0)
    hr = task_dialog(ctypes.byref(config), ctypes.byref(pressed), None, None)
    if hr != 0:
        raise OSError(f"TaskDialogIndirect failed: 0x{hr & 0xFFFFFFFF:08X}")
    return "continue" if pressed.value == CONTINUE_ID else "cancel"
