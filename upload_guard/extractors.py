"""Turn an uploaded file into text that can be searched for the marker.

Supported formats (as the brief requires): text files, PDF files, image files.
The type is decided from the file's first bytes (its "magic number"), then the
MIME type and file name, so a renamed file is still read correctly.

* **Text** (.txt .md .csv .json .log .py ... anything that decodes as text):
  UTF-8, UTF-16 (with BOM) or Latin-1.
* **PDF**: the text layer via ``pypdf``. If a page has little or no text (a
  scanned page), the images embedded in that page are OCR'd as well.
* **Images** (PNG, JPEG, GIF, BMP, TIFF, WebP): OCR (see ocr.py). Text stored in
  the image's metadata (PNG text chunks, EXIF comments) is searched too, since
  it travels with the file.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from upload_guard import ocr

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 200
MAX_OCR_IMAGES_PER_PDF = 20

IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
    b"II*\x00": ".tif",
    b"MM\x00*": ".tif",
}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".yaml", ".yml",
                   ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql", ".ini",
                   ".cfg", ".conf", ".rtf", ".tex", ".srt", ".env"}


@dataclass
class Extracted:
    kind: str                       # "text" | "pdf" | "image" | "unknown"
    text: str = ""
    locations: list[str] = field(default_factory=list)   # e.g. ["page 2"], per chunk of text
    chunks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, location: str, text: str) -> None:
        if text:
            self.chunks.append(text)
            self.locations.append(location)
            self.text += text + "\n"


def sniff(data: bytes, name: str = "", mime: str = "") -> str:
    """Return "pdf", "image", "text" or "unknown"."""
    head = data[:16]
    if head.startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if any(head.startswith(m) for m in IMAGE_MAGIC) or (head[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    lower = name.lower()
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf" or lower.endswith(".pdf"):
        return "pdf"
    if mime.startswith("text/") or any(lower.endswith(ext) for ext in TEXT_EXTENSIONS):
        return "text"
    return "text" if _looks_textual(data) else "unknown"


def _looks_textual(data: bytes) -> bool:
    sample = data[:4096]
    if not sample:
        return True
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    return b"\x00" not in sample


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _image_suffix(data: bytes) -> str:
    for magic, suffix in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _metadata_text(data: bytes) -> str:
    """Printable ASCII runs from the image bytes (PNG tEXt, EXIF/XMP comments)."""
    out, run = [], bytearray()
    for b in data[:2_000_000]:
        if 32 <= b < 127:
            run.append(b)
        else:
            if len(run) >= 8:
                out.append(run.decode("ascii"))
            run = bytearray()
    if len(run) >= 8:
        out.append(run.decode("ascii"))
    return "\n".join(out)


def extract(data: bytes, name: str = "", mime: str = "") -> Extracted:
    kind = sniff(data, name, mime)
    result = Extracted(kind)
    if kind == "text":
        result.add("text", decode_text(data))
    elif kind == "pdf":
        _extract_pdf(data, result)
    elif kind == "image":
        result.add("image metadata", _metadata_text(data))
        try:
            result.add("image text (OCR)", ocr.image_to_text(data, _image_suffix(data)))
        except ocr.OCRUnavailable as exc:
            result.errors.append(f"could not read text in the image: {exc}")
    else:
        result.errors.append("unsupported file type")
    return result


def _extract_pdf(data: bytes, result: Extracted) -> None:
    try:
        from pypdf import PdfReader
    except ImportError:
        result.errors.append("pypdf is not installed (pip install pypdf)")
        return
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                result.errors.append("the PDF is password-protected")
                return
        ocr_budget = MAX_OCR_IMAGES_PER_PDF
        for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
            text = ""
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                result.errors.append(f"page {number}: {exc}")
            result.add(f"page {number}", text)
            if len(text.strip()) < 20 and ocr_budget > 0:   # likely a scanned page
                images, image_error = _page_images(page)
                if image_error:
                    # Never treat an unreadable scanned page as clean: recording the
                    # error makes the file "could not be checked", so the user is asked.
                    result.errors.append(f"page {number}: could not read its images: {image_error}")
                    ocr_budget = 0
                for image in images:
                    if ocr_budget <= 0:
                        break
                    ocr_budget -= 1
                    try:
                        result.add(f"page {number} image (OCR)", ocr.image_to_text(image, _image_suffix(image)))
                    except ocr.OCRUnavailable as exc:
                        result.errors.append(f"page {number}: could not read an image: {exc}")
                        ocr_budget = 0
        # Metadata (title, subject, keywords) also leaves with the file.
        meta = reader.metadata or {}
        result.add("document properties", " ".join(str(v) for v in meta.values() if v))
    except Exception as exc:
        result.errors.append(f"could not read the PDF: {exc}")


def _page_images(page) -> tuple[list[bytes], str]:
    """The page's embedded images, plus an error message if they could not be read.

    pypdf needs Pillow to decode most image streams. A failure here must be
    reported, not swallowed: an empty list would look like "this page has no
    images" and a scanned page with the marker would pass as clean.
    """
    try:
        return [img.data for img in page.images], ""
    except ImportError:
        return [], "Pillow is not installed (pip install -r requirements.txt)"
    except Exception as exc:
        return [], str(exc) or type(exc).__name__
