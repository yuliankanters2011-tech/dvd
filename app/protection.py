"""Shared copy-protection notice and kernel sense recognition.

DVD Bridge only detects protection; it never authenticates, requests keys or
decrypts CSS/CPPM/CPRM content.
"""

from __future__ import annotations

import re

CSS_NOTICE = "Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video."

# Linux/sr prints e.g. "Sense Key : 0x5 ... ASC=0x6f ASCQ=0x3".
# T10 6F/03 = READ OF SCRAMBLED SECTOR WITHOUT AUTHENTICATION.
_CSS_SENSE = re.compile(
    r"Sense\s+Key\s*[:=]?\s*(?:0x)?0?5\b.*?\bASC\s*[:=]?\s*(?:0x)?6f\b"
    r".*?\bASCQ\s*[:=]?\s*(?:0x)?0?3\b",
    re.IGNORECASE | re.DOTALL,
)


def is_css_sense(text: str) -> bool:
    """Return True only for the explicit illegal-request/scrambled-sector sense."""
    return bool(_CSS_SENSE.search(text))


def is_css_error(exc: BaseException) -> bool:
    """Recognize the notice or a wrapped kernel sense without treating EIO as CSS."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        if text == CSS_NOTICE or is_css_sense(text):
            return True
        current = current.__cause__ or current.__context__
    return False
