import base64
import json

import pymupdf
import pytest

from backend import cache, config, diagrams, figures, llm, service
from backend.extract import Slide, extract_deck
from backend.pdf_builder import Topic, build_pdf, parse_summary


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


MD = "# ER Model\n## Overview\nEntities have relationships.\n## Key Points\n### Entities and relationships\n- Students enroll in courses.\n### Unrelated topic\n- Other information.\n"
GRAPH = {
    "kind": "graph",
    "caption": "Events flow through a buffer.",
    "nodes": [
        {"id": "a", "label": "Source"},
        {"id": "b", "label": "Channel"},
        {"id": "c", "label": "Sink"},
    ],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
}


def test_cropped_er_diagram_stays_with_topic_after_source_deleted(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    deck = extract_deck(source)
    calls = []
    monkeypatch.setattr(
        llm,
        "chat",
        lambda *a, **k: calls.append(1)
        or json.dumps(
            {
                "kind": "crop",
                "bbox": [0.08, 0.35, 0.92, 0.61],
                "caption": "Enrollment relationship.",
            }
        ),
    )
    marker = figures.prepare(deck, source, lambda _: None, parse_summary(MD).topics)
    assert figures.prepare(deck, source, lambda _: None, parse_summary(MD).topics) == marker
    assert len(calls) == 1
    source.unlink()
    for _ in range(2):
        with pymupdf.open(stream=build_pdf(parse_summary(MD + marker)), filetype="pdf") as doc:
            assert len(doc) == 1
            text = doc[0].get_text()
            assert (
                text.index("Students enroll")
                < text.index("Diagram from source slide 1")
                < text.index("Unrelated topic")
            )
            assert "Original Visual Slides" not in text
            assert len(doc[0].get_images()) == 1


def test_graph_is_vector_and_topic_linked(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps(GRAPH))
    marker = figures.prepare(extract_deck(source), source, lambda _: None, parse_summary(MD).topics)
    with pymupdf.open(stream=build_pdf(parse_summary(MD + marker)), filetype="pdf") as doc:
        text = doc[0].get_text()
        assert all(
            word in text for word in ["Source", "Channel", "Sink", "Redrawn from source slide 1"]
        )
        assert text.index("Channel") < text.index("Unrelated topic")
        assert not doc[0].get_images()
        assert len(doc[0].get_drawings()) > 4


def test_graph_rejects_cycles_unknown_ids_and_long_labels():
    for change in [
        {"edges": [{"from": "a", "to": "missing"}]},
        {"edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]},
        {"nodes": [{"id": "a", "label": "a" * 65}, {"id": "b", "label": "B"}]},
    ]:
        with pytest.raises(ValueError):
            diagrams.validate({**GRAPH, **change})


def test_unsupported_graph_falls_back_to_source_crop(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    png = figures.render_slides(source, [1])[1]
    cyclic = {
        **GRAPH,
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}],
        "bbox": [0.08, 0.35, 0.92, 0.61],
    }
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps(cyclic))
    result = figures.analyze(extract_deck(source).slides[0], png, Topic("Entities"))
    assert result["kind"] == "crop" and base64.b64decode(result["png"])


def test_bad_crop_or_model_failure_does_not_fail_summary(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '{"kind":"crop","bbox":[0,0,1,1]}')
    marker = figures.prepare(extract_deck(source), source, lambda _: None, parse_summary(MD).topics)
    assert figures.needs_upgrade(MD + marker)
    with pymupdf.open(stream=build_pdf(parse_summary(MD + marker)), filetype="pdf") as doc:
        assert "Some diagrams are unavailable" in doc[0].get_text()
        assert not doc[0].get_images()


def test_no_diagram_response_is_cached_and_covers_not_sent(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    deck = extract_deck(source)
    deck.slides.insert(0, Slide(1, "Course", "Department of Computer Science", has_visual=True))
    calls = []
    monkeypatch.setattr(llm, "chat", lambda *a, **k: calls.append(1) or '{"kind":"none"}')
    marker = figures.prepare(deck, source, lambda _: None, parse_summary(MD).topics)
    assert not figures.needs_upgrade(MD + marker)
    figures.prepare(deck, source, lambda _: None, parse_summary(MD).topics)
    assert len(calls) == 1


def test_topic_matching_does_not_attach_unrelated_deep_dive():
    topics = [Topic("CDC Deep Dive"), Topic("Apache Flume – Legacy Streaming Log Collector")]
    assert figures.match_topic(Slide(9, "Architecture Deep Dive", "Connector API"), topics) is None
    assert (
        figures.match_topic(Slide(3, "Apache Flume — A Legacy Tool", "Source channel sink"), topics)
        == 1
    )


def test_legacy_appendix_upgrade_reuses_summary_and_extras(tmp_path, monkeypatch):
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    old = "c" * 64
    cache.put(old, json.dumps({"total": 0, "slides": []}))
    cache.put(cache.cache_key("deck", "short"), MD + f"<!-- source-figures:{old} -->")
    cache.put(cache.cache_key("deck", "short", "flashcards"), "## Flashcards\n- Q: Saved? | A: Yes")
    monkeypatch.setattr(
        service, "summarize_file", lambda *a, **k: pytest.fail("Text should be reused")
    )
    monkeypatch.setattr(
        service, "generate_extra", lambda *a, **k: pytest.fail("Cards should be reused")
    )
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps(GRAPH))
    first, hit = service.run(source, "deck", "short", ["flashcards"])
    assert not hit and "Saved?" in first and not figures.needs_upgrade(first)
    again, hit = service.run(source, "deck", "short", ["flashcards"])
    assert hit and first == again


def test_missing_figures_are_disclosed_without_an_extra_page():
    markdown = "# Summary\n<!-- source-figures:" + "a" * 64 + " -->"
    with pymupdf.open(stream=build_pdf(parse_summary(markdown)), filetype="pdf") as doc:
        assert len(doc) == 1
        assert "Some diagrams are unavailable" in doc[0].get_text()


def test_diagrams_can_be_disabled_and_text_slides_are_not_added(sample_pdf, tmp_path, monkeypatch):
    assert figures.prepare(
        extract_deck(sample_pdf), sample_pdf, lambda _: None, parse_summary(MD).topics
    )
    source = tmp_path / "diagram.pdf"
    make_diagram(source)
    monkeypatch.setattr(config, "INCLUDE_DIAGRAMS", False)
    assert (
        figures.prepare(extract_deck(source), source, lambda _: None, parse_summary(MD).topics)
        == ""
    )
