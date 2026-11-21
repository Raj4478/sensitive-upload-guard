"""Tests for detection, file reading and the native-messaging host."""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from upload_guard import detector, extractors, inspector

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="session", autouse=True)
def samples():
    subprocess.run([sys.executable, str(SAMPLES / "make_samples.py")], check=True, capture_output=True)


# ----------------------------------------------------------------------------- detector

@pytest.mark.parametrize("text", [
    "WALLED_SENSITIVE", "please review walled_sensitive data", "WALLED SENSITIVE", "WALLED_\nSENSITIVE",
    "WA11ED_SENS1TIVE", "W A L L E D _ S E N S I T I V E", "xx-WALLED-SENSITIVE-yy",
])
def test_marker_variants_are_found(text):
    assert detector.find_marker(text).found


@pytest.mark.parametrize("text", ["", "WALLED", "SENSITIVE data", "walled garden, sensitive topic later", "WALLED_SENSITIV"])
def test_near_misses_are_not_flagged(text):
    assert not detector.find_marker(text).found


def test_snippet_points_at_the_marker():
    match = detector.find_marker("line one\nsome text WALLED_SENSITIVE here\nline three")
    assert "WALLED_SENSITIVE" in match.snippet and "line three" not in match.snippet


# ----------------------------------------------------------------------------- files

def test_text_encodings():
    for data in ("WALLED_SENSITIVE".encode("utf-8"), "WALLED_SENSITIVE".encode("utf-16"),
                 b"\xef\xbb\xbfWALLED_SENSITIVE", "café WALLED_SENSITIVE".encode("latin-1")):
        assert inspector.inspect_file("a.txt", data).detected


def test_type_is_sniffed_from_content_not_name():
    pdf = (SAMPLES / "sensitive.pdf").read_bytes()
    assert extractors.sniff(pdf, "renamed.txt") == "pdf"
    finding = inspector.inspect_file("renamed.txt", pdf)
    assert finding.detected and finding.kind == "pdf" and finding.location == "page 2"


@pytest.mark.parametrize("name, detected", [
    ("clean.txt", False), ("sensitive.txt", True), ("clean.pdf", False), ("sensitive.pdf", True),
])
def test_text_and_pdf_samples(name, detected):
    finding = inspector.inspect_file(name, (SAMPLES / name).read_bytes())
    assert finding.detected is detected and finding.inspected


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="needs the OS OCR engine")
@pytest.mark.parametrize("name, detected, location", [
    ("clean.png", False, ""), ("sensitive.png", True, "image text (OCR)"),
    ("sensitive-scanned.pdf", True, "page 1 image (OCR)"),
])
def test_ocr_samples(name, detected, location):
    finding = inspector.inspect_file(name, (SAMPLES / name).read_bytes())
    assert finding.detected is detected and finding.location == location


def test_marker_in_png_metadata_is_found():
    from PIL import Image, PngImagePlugin
    import io
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "WALLED_SENSITIVE")
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, "PNG", pnginfo=info)
    finding = inspector.inspect_file("blank.png", buf.getvalue())
    assert finding.detected and finding.location == "image metadata"


def test_corrupt_pdf_is_reported_not_crashed():
    finding = inspector.inspect_file("broken.pdf", b"%PDF-1.4\nnot really a pdf")
    assert not finding.detected and not finding.inspected and finding.kind == "pdf"


def test_unsupported_binary_is_left_alone():
    finding = inspector.inspect_file("archive.zip", b"PK\x03\x04" + b"\x00\x01" * 50)
    assert finding.kind == "unknown" and not finding.detected


# ----------------------------------------------------------------------------- host protocol

def run_host(messages: list[dict], answer: str) -> list[dict]:
    """Talk to `python -m upload_guard.host` exactly like Chrome does."""
    payload = b"".join(struct.pack("<I", len(b := json.dumps(m).encode())) + b for m in messages)
    env = {**os.environ, "UPLOAD_GUARD_AUTO_ANSWER": answer, "PYTHONPATH": str(ROOT),
           "UPLOAD_GUARD_HOME": str(ROOT / ".pytest_cache" / "home")}
    proc = subprocess.run([sys.executable, "-m", "upload_guard.host"], input=payload, capture_output=True,
                          env=env, timeout=120)
    out, replies = proc.stdout, []
    while out:
        (n,) = struct.unpack("<I", out[:4])
        replies.append(json.loads(out[4:4 + n]))
        out = out[4 + n:]
    return replies


def test_host_ping_and_clean_prompt_need_no_dialog():
    ping, clean = run_host([{"type": "ping"}, {"type": "inspect_prompt", "site": "ChatGPT", "text": "hello"}],
                           answer="cancel")
    assert ping["ok"] and clean["decision"] == "allow"


@pytest.mark.parametrize("answer", ["continue", "cancel"])
def test_host_prompt_with_marker_asks_the_user(answer):
    (reply,) = run_host([{"type": "inspect_prompt", "site": "ChatGPT", "text": "x WALLED_SENSITIVE y", "id": 7}],
                        answer=answer)
    assert reply["decision"] == answer and reply["finding"]["detected"] and reply["id"] == 7


def test_host_file_round_trip():
    data = base64.b64encode((SAMPLES / "sensitive.pdf").read_bytes()).decode()
    clean = base64.b64encode((SAMPLES / "clean.txt").read_bytes()).decode()
    flagged, ok = run_host([
        {"type": "inspect_file", "site": "Perplexity", "name": "sensitive.pdf", "mime": "application/pdf", "data": data},
        {"type": "inspect_file", "site": "Perplexity", "name": "clean.txt", "mime": "text/plain", "data": clean},
    ], answer="cancel")
    assert flagged["decision"] == "cancel" and flagged["finding"]["location"] == "page 2"
    assert ok["decision"] == "allow"


def test_host_too_large_file_asks():
    (reply,) = run_host([{"type": "inspect_file", "name": "huge.pdf", "mime": "application/pdf",
                          "size": 80_000_000, "too_large": True}], answer="cancel")
    assert reply["decision"] == "cancel" and not reply["finding"]["inspected"]


def test_host_rejects_bad_requests_without_dying():
    bad, ping = run_host([{"type": "inspect_file", "name": "x", "data": "!!!not base64"}, {"type": "ping"}],
                         answer="cancel")
    assert "error" in bad and ping["ok"]
