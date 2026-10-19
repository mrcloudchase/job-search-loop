"""csvsafe.py: any value made safe for the tracker CSV. Pure functions, no I/O, and they never raise.

The tracker writes unquoted fields, so a comma, a double quote or a line break in a value would shift every column after it
or split the row in two. These helpers promise that what they return has none of those, starts with nothing a spreadsheet
would evaluate as a formula (= + - @, tab, CR), holds no control or bidi-override characters, fits a length cap, and reads
back through the csv module as one field, unchanged. Applying a helper twice changes nothing the second time.
"""
from __future__ import annotations

import re
import unicodedata

FIELD_WIDTH = 200
NOTE_WIDTH = 300
URL_WIDTH = 500
_FORMULA_STARTS = "=+-@"
_AS_SPACE = {"Zs", "Zl", "Zp"}


def _text(value: object) -> str:
    """Whatever it is, as text; an object that cannot be shown becomes empty."""
    try:
        if value is None:
            return ""
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8", "replace")
        return str(value).encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return ""


def _clean(text: str) -> str:
    """Folded to NFKC (so fullwidth = + - @ cannot slip past the formula guard), controls and bidi marks gone."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        category = unicodedata.category(ch)
        if ch in "\t\r\n\v\f" or category in _AS_SPACE:
            out.append(" ")
        elif category[0] in "CZ":
            continue
        else:
            out.append(ch)
    return "".join(out)


def _fit(text: str, width: int) -> str:
    width = max(1, width)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def field(value: object, width: int = FIELD_WIDTH) -> str:
    """One CSV field: one line, no commas or double quotes, no formula start, no control characters, at most `width`."""
    text = _clean(_text(value)).replace('"', "'")
    text = " ".join(re.sub(r"\s*,\s*", " - ", text).split())
    if text and text[0] in _FORMULA_STARTS:
        text = "'" + text
    return _fit(text, width)


def note(value: object) -> str:
    """A note fragment: a field with the longer note width."""
    return field(value, NOTE_WIDTH)


def url(value: object) -> str:
    """A link: characters that would break the CSV are percent-encoded (the link keeps working), not dropped."""
    text = "".join(c for c in _clean(_text(value)).strip() if c != "\x7f")
    for char, code in ((" ", "%20"), (",", "%2C"), ('"', "%22")):
        text = text.replace(char, code)
    return field(text, URL_WIDTH)


def is_safe(value: object, width: int = FIELD_WIDTH) -> bool:
    """True when `field` would return the value unchanged."""
    return isinstance(value, str) and field(value, width) == value
