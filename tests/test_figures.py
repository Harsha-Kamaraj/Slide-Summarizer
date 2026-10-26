import pymupdf

from backend import config, figures
from backend.extract import extract_deck
from backend.pdf_builder import build_pdf, parse_summary


def make_diagram(path):
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=480)
    page.insert_text((40, 45), "Entities and relationships", fontsize=24)
    for rect, label in [((70, 180, 260, 280), "STUDENT"), ((450, 180, 640, 280), "COURSE")]:
        page.draw_rect(rect, color=(0, 0.2, 0.5), width=2)
        page.insert_text((rect[0] + 30, 235), label, fontsize=18)
    page.draw_line((260, 230), (450, 230), color=(0, 0.2, 0.5), width=2)
    page.insert_text((300, 210), "ENROLLS", fontsize=16)
    doc.save(path)
    doc.close()


def test_original_diagram_survives_deleting_source_and_rebuilding_pdf(tmp_path):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    deck = extract_deck(source)
    assert deck.slides[0].has_visual
    marker = figures.prepare(deck, source, lambda _: None)
    source.unlink()
    markdown = (
        "# ER Model\n## Overview\nEntities have relationships.\n## Key Points\n### Enrollment\n- Students enroll in courses.\n"
        + marker
    )
    parsed = parse_summary(markdown)
    assert "source-figures" not in parsed.topics[0].explanation
    for _ in range(2):
        with pymupdf.open(stream=build_pdf(parsed), filetype="pdf") as doc:
            assert len(doc) == 2
            assert "Slide 1: Entities and relationships" in doc[1].get_text()
            assert len(doc[1].get_images()) == 1


def test_missing_figures_are_disclosed_in_pdf():
    markdown = "# Summary\n<!-- source-figures:" + "a" * 64 + " -->"
    with pymupdf.open(stream=build_pdf(parse_summary(markdown)), filetype="pdf") as doc:
        assert "Saved slide images are unavailable" in doc[1].get_text()


def test_diagrams_can_be_disabled_and_text_slides_are_not_added(sample_pdf, tmp_path, monkeypatch):
    assert figures.prepare(extract_deck(sample_pdf), sample_pdf, lambda _: None) == ""
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    monkeypatch.setattr(config, "INCLUDE_DIAGRAMS", False)
    assert figures.prepare(extract_deck(source), source, lambda _: None) == ""
