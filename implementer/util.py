"""Small text/date helpers shared by the automation steps."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# "05-09-2026 12:00:00" / "05-09-2026 12:00"
_DMY_RE = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")


def squash(text: str) -> str:
    """Collapse whitespace and strip."""
    return _WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def fold(text: str) -> str:
    """Aggressive normalisation for comparing names.

    Drops accents, punctuation, titles and spacing so that
    'dr Ida Bagus Ramajaya Sutawan M.Biomed., Sp.A (K)' and
    'dr Ida Bagus Ramajaya Sutawan, M.Biomed., Sp.A (K)' compare equal.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub("", text.lower())


def names_match(a: str, b: str) -> bool:
    """True if two person names refer to the same person.

    Exact after folding, or one folded name contained in the other (handles a
    trailing degree list being present in one place and not the other).
    """
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return False
    return fa == fb or fa in fb or fb in fa


def name_tokens(text: str) -> set[str]:
    """Folded word set of a person's name, ignoring one-letter noise."""
    words = _NON_ALNUM_RE.split(fold_words(text))
    return {word for word in words if len(word) > 1}


def fold_words(text: str) -> str:
    """Like fold(), but keeps word boundaries."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", text.lower()).strip()


def patient_names_match(worklist: str, chart: str) -> bool:
    """Stricter than names_match: used before writing to a patient record.

    One name's words must be a subset of the other's, so a shortened entry
    ('Chiara Klau') still matches the full chart name, but an unrelated name
    that merely shares a prefix does not.
    """
    left, right = name_tokens(worklist), name_tokens(chart)
    if not left or not right:
        return False
    return left <= right or right <= left


def contains_keyword(haystack: str, keyword: str) -> bool:
    """Case/space-insensitive substring test used for the CPPT keyword."""
    return fold(keyword) in fold(haystack)


def parse_dmy(text: str) -> date | None:
    """Pull a dd-mm-yyyy date out of a 'Tgl & Jam' cell."""
    match = _DMY_RE.search(text or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_today(text: str, today: date | None = None) -> bool:
    parsed = parse_dmy(text)
    return parsed is not None and parsed == (today or datetime.now().date())


def tindakan_name(option_text: str) -> str:
    """'Visite Dokter ~ 01.01.010' -> 'Visite Dokter'."""
    return squash(option_text.split("~")[0]) if "~" in option_text else squash(option_text)


def make_run_id() -> str:
    """Timestamp used to name this run's log and report."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")
