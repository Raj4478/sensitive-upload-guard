"""Sensitive Upload Guard: a standalone desktop DLP for ChatGPT and Perplexity.

A local inspecting proxy sees every prompt and file upload the browser sends to
chatgpt.com and perplexity.ai *before* it leaves the computer, checks it for the
marker ``WALLED_SENSITIVE`` (text, PDF, images via OCR) and, when found, holds it
while a native desktop dialog asks Continue or Cancel. It also recolours AI
responses on both sites. No browser extension. See docs/ARCHITECTURE.md.
"""

__version__ = "2.0.0"

MARKER = "WALLED_SENSITIVE"
