"""Unit tests for the parts of the pipeline that don't need the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pdf_text, tts  # noqa: E402


def test_dehyphenates_across_line_breaks():
    pages = pdf_text.clean_pages(["This is an exam-\nple of hyphenation."])
    assert "example" in pages[0]
    assert "exam-" not in pages[0]


def test_strips_running_headers_and_page_numbers():
    raw = [
        f"Acme Quarterly Report\n"
        f"Body sentence {n} runs on for a while so it reads like real prose.\n"
        f"Page {n} of 8"
        for n in range(1, 9)
    ]
    cleaned = pdf_text.clean_pages(raw)
    assert all("Acme Quarterly Report" not in page for page in cleaned)
    # The numbered footer differs page to page, so only the digits-normalized
    # pass can catch it.
    assert all("Page" not in page for page in cleaned)
    assert "Body sentence 3 runs on" in cleaned[2]


def test_keeps_headers_in_short_documents():
    # Too few pages to tell a header from ordinary content — leave it alone.
    raw = ["Title Line\nBody one.", "Title Line\nBody two."]
    cleaned = pdf_text.clean_pages(raw)
    assert "Title Line" in cleaned[0]


def test_detects_missing_text_layer():
    assert pdf_text.has_text_layer(["x" * 100, "y" * 100]) is True
    assert pdf_text.has_text_layer(["", "", "", ""]) is False


def test_chunking_respects_limit_and_keeps_all_words():
    text = " ".join(f"Sentence number {i} carries some weight." for i in range(200))
    chunks = tts.chunk_text(text, limit=300)
    assert chunks
    assert max(len(c) for c in chunks) <= 300
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_chunking_splits_a_single_enormous_sentence():
    chunks = tts.chunk_text("word " * 500, limit=120)
    assert max(len(c) for c in chunks) <= 120


def test_page_offsets_absorb_the_gap_added_at_every_join():
    from app.pipeline import _page_offsets

    durations = {n: 10.0 for n in range(1, 11)}
    # Joining ten parts without re-encoding leaves ~0.05 s at each seam.
    total = 100.5
    offsets = _page_offsets(10, durations, total)

    # A naive running sum would put page 10 at 90.0 and land half a second
    # early; the correction spreads the measured surplus across the joins.
    assert offsets[1] == 0.0
    assert 90.4 <= offsets[10] <= 90.5
    assert offsets == dict(sorted(offsets.items()))
    assert all(offsets[n] < offsets[n + 1] for n in range(1, 10))


def test_page_offsets_point_failed_pages_at_the_next_audio():
    from app.pipeline import _page_offsets

    # Page 2 produced no audio.
    offsets = _page_offsets(3, {1: 10.0, 3: 10.0}, 20.0)
    assert offsets[1] == 0.0
    assert offsets[2] == offsets[3]  # skip straight to the next narrated page


def test_page_offsets_ignore_an_implausible_measurement():
    from app.pipeline import _page_offsets

    # A total wildly larger than the parts must not be spread into the offsets.
    offsets = _page_offsets(3, {1: 10.0, 2: 10.0, 3: 10.0}, 900.0)
    assert offsets[3] <= 20.5
