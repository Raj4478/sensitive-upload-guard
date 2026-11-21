"""Create the demo files: one clean and one marked file of each supported type.

    python samples/make_samples.py

Writes into samples/:
  clean.txt  sensitive.txt              plain text
  clean.pdf  sensitive.pdf              PDF with a text layer (marker on page 2)
  sensitive-scanned.pdf                 PDF that is only a picture of text (needs OCR)
  clean.png  sensitive.png              images (marker is rendered text, needs OCR)
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "WALLED_SENSITIVE"


def text_pdf(pages: list[list[str]]) -> bytes:
    """A minimal valid PDF with real text (no libraries needed)."""
    objects: list[bytes] = []
    kids = []
    font_id = 3 + 2 * len(pages)
    for i, lines in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        ops = ["BT", "/F1 16 Tf", "72 740 Td", "22 TL"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >> endobj\n".encode())
        objects.append(f"{content_id} 0 obj << /Length {len(stream)} >> stream\n".encode() + stream + b"\nendstream endobj\n")
    head = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            f"2 0 obj << /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >> endobj\n".encode()]
    font = f"{font_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode()
    body = head + objects + [font]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for obj in body:
        offsets.append(len(out))
        out += obj
    xref = len(out)
    out += f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def picture(lines: list[str]):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1000, 120 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf" if sys.platform == "win32" else "Arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default(size=36)
    for i, line in enumerate(lines):
        draw.text((50, 50 + 60 * i), line, fill="black", font=font)
    return img


def main() -> None:
    (HERE / "clean.txt").write_text("Weekly status: the launch is on track.\nNo blockers.\n", encoding="utf-8")
    (HERE / "sensitive.txt").write_text(
        f"Customer list - internal\nClassification: {MARKER}\nAcme Corp, contract value 1.2M\n", encoding="utf-8")
    (HERE / "clean.pdf").write_bytes(text_pdf([["Product brochure", "Our new app helps teams plan."]]))
    (HERE / "sensitive.pdf").write_bytes(text_pdf([
        ["Board meeting notes", "Agenda: budget, hiring"],
        ["Appendix B - salary bands", f"Label: {MARKER}", "Do not share outside the company."],
    ]))
    picture(["Holiday photo caption", "Team offsite 2026"]).save(HERE / "clean.png")
    picture(["Q3 revenue forecast (draft)", f"Label: {MARKER}"]).save(HERE / "sensitive.png")
    picture(["Scanned contract page 1", f"Stamp: {MARKER}", "Signed by both parties"]).save(
        HERE / "sensitive-scanned.pdf", "PDF", resolution=150)
    print("samples written to", HERE)


if __name__ == "__main__":
    main()
