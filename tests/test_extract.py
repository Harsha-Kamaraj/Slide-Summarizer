import shutil

import pytest

from backend.extract import ExtractionError, extract_deck


def test_pptx_title_text_and_notes(sample_pptx):
    deck = extract_deck(sample_pptx)

    assert deck.title == "Intro to Machine Learning"
    assert len(deck.slides) == 3

    first = deck.slides[0]
    assert first.number == 1
    assert first.title == "What is ML?"
    assert "labelled examples" in first.text
    assert "What is ML?" not in first.text  # title is not duplicated in body
    assert first.notes == "Mention spam filters as an example."


def test_pptx_tables_keep_columns(sample_pptx):
    table_slide = extract_deck(sample_pptx).slides[1]
    assert "Supervised | Regression" in table_slide.text


def test_image_heavy_detection(sample_pptx):
    slides = extract_deck(sample_pptx).slides
    assert not slides[0].is_image_heavy
    assert slides[2].is_image_heavy  # title only, no body text


def test_pdf_one_page_per_slide(sample_pdf):
    deck = extract_deck(sample_pdf)

    assert [s.title for s in deck.slides] == ["Linear Regression", "Gradient Descent"]
    assert "squared error" in deck.slides[0].text
    assert deck.title == "Linear Regression"  # no PDF metadata title, so first heading


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_legacy_ppt_via_libreoffice(sample_pptx, tmp_path):
    # Make a real .ppt by asking LibreOffice to convert our .pptx down.
    import subprocess

    subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "ppt",
            "--outdir",
            str(tmp_path),
            str(sample_pptx),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    deck = extract_deck(tmp_path / "intro.ppt")
    assert len(deck.slides) == 3
    assert "labelled examples" in deck.slides[0].text


def test_rejects_unsupported_type(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_text("hello")
    with pytest.raises(ExtractionError, match="Unsupported file type"):
        extract_deck(path)


@pytest.mark.parametrize("name", ["broken.pptx", "broken.pdf"])
def test_corrupt_file_gives_clear_error(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"this is not a real file")
    with pytest.raises(ExtractionError, match="may be corrupt"):
        extract_deck(path)


def test_missing_file(tmp_path):
    with pytest.raises(ExtractionError, match="File not found"):
        extract_deck(tmp_path / "nope.pptx")


def test_pdf_strips_repeated_headers_and_bullet_glyphs(tmp_path):
    # Mimics the college exports: a course-name header on every page, above
    # the real slide heading, plus bullet glyphs exported as text.
    import pymupdf

    doc = pymupdf.open()
    for heading in ["Concept", "Feature Space", "Version Space", "Inductive Bias"]:
        page = doc.new_page()
        page.insert_text((72, 40), "Machine Learning", fontsize=10)
        page.insert_text((72, 80), heading, fontsize=20)
        # Body lines differ per page; identical ones would count as boilerplate.
        page.insert_text((72, 120), f"• point about {heading}", fontsize=12)
        page.insert_text((72, 140), f"-0.5 is a weight for {heading}", fontsize=12)
    path = tmp_path / "headers.pdf"
    doc.save(path)

    slides = extract_deck(path).slides
    assert [s.title for s in slides] == [
        "Concept",
        "Feature Space",
        "Version Space",
        "Inductive Bias",
    ]
    assert "Machine Learning" not in slides[0].text
    assert "- point about Concept" in slides[0].text
    assert "-0.5 is a weight for Concept" in slides[0].text  # minus sign untouched
