"""PDF text extraction and cleanup.

Extraction is per page (PyMuPDF). Cleanup does two things the PRD calls for:
drop running headers/footers/page numbers that repeat across the document, and
rejoin words split by a hyphen at a line break. On top of that it reflows lines
into paragraphs, which is what makes the TTS output sound like prose instead of
a list of fragments.
"""
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

from . import config

# Ligatures and typographic marks that trip up TTS pronunciation.
_REPLACEMENTS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "­": "", "‘": "'", "’": "'",
    "“": '"', "”": '"', "–": "-", "—": " - ",
    "…": "...", " ": " ", "•": " ", "●": " ",
    "·": " ", "﻿": "",
}

_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")
_MULTISPACE = re.compile(r"[ \t]+")
_BLANKLINES = re.compile(r"\n{3,}")
_DIGITS = re.compile(r"\d+")
_PAGE_NUMBER_LINE = re.compile(r"^[\s\W]*\d{1,4}[\s\W]*$")

# A running header can be a full title line; a "Page 12 of 300"-style footer is
# short, and the length cap is what keeps ordinary prose that happens to contain
# a number from being mistaken for one.
MAX_HEADER_CHARS = 120
MAX_NUMBERED_HEADER_CHARS = 40


class NoTextLayerError(Exception):
    """Raised when a PDF has essentially no extractable text (scanned pages)."""


def extract_raw_pages(pdf_path: Path) -> List[str]:
    """Return the raw text of each page, in order."""
    with fitz.open(pdf_path) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("This PDF is password protected.")
        return [page.get_text("text") or "" for page in doc]


def _normalize_chars(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Strip control characters that survive normalization.
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")


def _line_signature(line: str) -> str:
    """Collapse a line to a form that matches its twin on other pages.

    Digits become '#' so "Page 12" and "Page 13" count as the same header.
    """
    return _DIGITS.sub("#", line.strip().lower())


def _repeated_lines(pages: List[str]) -> tuple:
    """Find lines that recur near the top or bottom of many pages.

    Two passes, because the two kinds of repeat need different caution:
    an identical line (a running header) can be fairly long, while a
    digits-normalized match ("Page 12" vs "Page 13") is only trustworthy for
    short lines — otherwise a body line that merely mentions a number could be
    mistaken for a footer.
    """
    if len(pages) < 4:
        return set(), set()

    exact_counts: Counter = Counter()
    sig_counts: Counter = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        # Headers and footers live in the outer few lines; anything repeating in
        # the middle of a page is more likely to be real content.
        edges = lines[:3] + lines[-3:]
        seen_exact = {ln.lower() for ln in edges if len(ln) <= MAX_HEADER_CHARS}
        seen_sig = {
            sig
            for sig in (
                _line_signature(ln)
                for ln in edges
                if len(ln) <= MAX_NUMBERED_HEADER_CHARS
            )
            # Only lines that actually carry a number need this looser rule;
            # identical lines are already caught by the exact pass.
            if "#" in sig
        }
        exact_counts.update(seen_exact)
        sig_counts.update(seen_sig)

    threshold = max(3, int(len(pages) * 0.4))
    exact = {k for k, n in exact_counts.items() if n >= threshold and k}
    sig = {k for k, n in sig_counts.items() if n >= threshold and k}
    return exact, sig


def _reflow(text: str) -> str:
    """Join wrapped lines into paragraphs, keeping blank-line breaks."""
    paragraphs = []
    for block in text.split("\n\n"):
        lines = [ln.strip() for ln in block.split("\n")]
        joined = " ".join(ln for ln in lines if ln)
        joined = _MULTISPACE.sub(" ", joined).strip()
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs)


def clean_pages(raw_pages: List[str]) -> List[str]:
    """Clean every page of a document together, so cross-page repeats are visible."""
    normalized = [_normalize_chars(p) for p in raw_pages]
    # De-hyphenate before splitting into lines, while the line break is still there.
    normalized = [_HYPHEN_BREAK.sub(r"\1\2", p) for p in normalized]

    repeated_exact, repeated_sig = _repeated_lines(normalized)

    cleaned = []
    for page in normalized:
        kept = []
        lines = page.split("\n")
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                kept.append("")
                continue
            near_edge = idx < 3 or idx >= len(lines) - 3
            if near_edge and _PAGE_NUMBER_LINE.match(stripped):
                continue
            if near_edge and stripped.lower() in repeated_exact:
                continue
            if (
                near_edge
                and len(stripped) <= MAX_NUMBERED_HEADER_CHARS
                and _line_signature(stripped) in repeated_sig
            ):
                continue
            kept.append(stripped)
        page_text = _BLANKLINES.sub("\n\n", "\n".join(kept)).strip()
        cleaned.append(_reflow(page_text))
    return cleaned


def has_text_layer(cleaned_pages: List[str]) -> bool:
    """True when enough pages carry real text to be worth narrating."""
    if not cleaned_pages:
        return False
    with_text = sum(
        1 for p in cleaned_pages if len(p.strip()) >= config.MIN_CHARS_FOR_TEXT_LAYER
    )
    return with_text / len(cleaned_pages) >= config.MIN_TEXT_PAGE_RATIO


def extract_and_clean(pdf_path: Path) -> List[str]:
    """Full pipeline: raw extraction, cleanup, scanned-PDF check."""
    cleaned = clean_pages(extract_raw_pages(pdf_path))
    if not has_text_layer(cleaned):
        raise NoTextLayerError(
            "No extractable text found — likely a scanned PDF"
        )
    return cleaned
