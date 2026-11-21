"""Tests for detection, file reading and the native-messaging host."""

from __future__ import annotations

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


def test_scanned_pdf_without_pillow_is_unreadable_not_clean(monkeypatch):
    """pypdf needs Pillow to extract page images. If that fails, the scanned page
    must count as "could not be checked" (user is asked), never as clean."""
    from pypdf import PageObject

    def no_pillow(self):
        raise ImportError("pillow is required to do image extraction. "
                          "It can be installed via 'pip install pypdf[image]'")

    monkeypatch.setattr(PageObject, "images", property(no_pillow))
    finding = inspector.inspect_file("sensitive-scanned.pdf", (SAMPLES / "sensitive-scanned.pdf").read_bytes())
    assert not finding.detected and not finding.inspected
    assert any("Pillow" in err for err in finding.errors)


def test_corrupt_pdf_is_reported_not_crashed():
    finding = inspector.inspect_file("broken.pdf", b"%PDF-1.4\nnot really a pdf")
    assert not finding.detected and not finding.inspected and finding.kind == "pdf"


def test_unsupported_binary_is_left_alone():
    finding = inspector.inspect_file("archive.zip", b"PK\x03\x04" + b"\x00\x01" * 50)
    assert finding.kind == "unknown" and not finding.detected
