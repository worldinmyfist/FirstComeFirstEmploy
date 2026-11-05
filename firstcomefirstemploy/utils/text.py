"""Pure text helpers."""

import html
import re
import time


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(value):
    return _NON_ALNUM.sub(" ", str(value or "").lower()).strip()


def clean_description(value):
    """Convert an HTML/plain-text description into searchable text."""
    if not value:
        return ""
    # Unescape first: some APIs (Greenhouse) return HTML-escaped HTML.
    value = html.unescape(str(value))
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(value):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value)


def fmt_ts(value):
    """Epoch seconds/millis -> YYYY-MM-DD (other values returned as text)."""
    if value in (None, ""):
        return ""
    try:
        number = float(value)
        if number > 1e11:
            number /= 1000.0
        return time.strftime("%Y-%m-%d", time.gmtime(number))
    except (TypeError, ValueError):
        return str(value)


def join_locations(value):
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value if v)
    return str(value or "")