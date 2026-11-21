"""Find the marker WALLED_SENSITIVE in text, tolerating how real content mangles it.

Text pulled out of PDFs and images rarely matches byte-for-byte:

* PDF extraction can split a word across lines or insert spaces.
* OCR can read ``_`` as a space or drop it, and confuse look-alike characters
  (``0``/``O``, ``1``/``I``/``l``, ``5``/``S``).

So both the marker and the content are folded to a canonical form: upper case,
look-alikes mapped to one letter, and everything that is not a letter removed.
``WALLED_SENSITIVE``, ``walled sensitive``, ``WALLED_\\nSENSITIVE`` and the OCR
misread ``WALLED SENS1TIVE`` all fold to ``WALLEDSENSITIVE``.

The same rules are implemented in extension/content.js (``foldText``) so the
browser's quick pre-check and the desktop app always agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from upload_guard import MARKER

# Characters OCR commonly confuses with letters in the marker.
_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "|": "I", "!": "I", "5": "S", "$": "S", "€": "E"})
_NON_LETTERS = re.compile(r"[^A-Z]+")


def fold(text: str) -> str:
    """Canonical form used for matching (see module docstring)."""
    upper = text.upper().translate(_LOOKALIKES)
    return _NON_LETTERS.sub("", upper)


_FOLDED_MARKER = fold(MARKER)
# "l" (lower-case L) is upper-cased to "L" by fold, so I/L confusion only matters
# for OCR output; accept either letter in the two L positions of WALLED.
_FUZZY = re.compile(_FOLDED_MARKER.replace("LL", "[LI][LI]"))


@dataclass(frozen=True)
class Match:
    found: bool
    snippet: str = ""   # a short piece of the original text around the hit, for the dialog


def find_marker(text: str) -> Match:
    """Return whether the marker occurs in ``text`` and a short snippet near it."""
    if not text:
        return Match(False)
    if not _FUZZY.search(fold(text)):
        return Match(False)
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        exact = line.upper().find(MARKER)
        if exact >= 0:   # the usual case: show that line, centred on the marker if it is long
            start = max(0, exact - 60)
            return Match(True, " ".join(line[start:exact + len(MARKER) + 60].split()))
    # Fuzzy hit (OCR / split text): show the line, or pair of lines, that matched.
    for i in range(len(lines)):
        for window in (lines[i], " ".join(lines[i:i + 2])):
            if _FUZZY.search(fold(window)):
                return Match(True, " ".join(window.split())[:160])
    return Match(True, " ".join(text.split())[:160])
