"""Sensitive Upload Guard: a lightweight desktop DLP for ChatGPT and Perplexity.

A browser extension (the sensor) holds each outgoing prompt and file upload on
chatgpt.com and perplexity.ai. This desktop app (the brain) inspects it for the
marker ``WALLED_SENSITIVE`` and, when found, asks the user in a native dialog
whether to Continue or Cancel. See docs/ARCHITECTURE.md.
"""

__version__ = "1.0.0"

MARKER = "WALLED_SENSITIVE"
HOST_NAME = "com.walled.upload_guard"
