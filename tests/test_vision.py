"""Vision step with the model replaced by a fake that records its requests."""

import pymupdf
import pytest

from backend import config, llm, summarize, vision
from backend.extract import extract_deck

TEXT_SLIDE = (
    "Gradient descent repeatedly moves the weights a small step against the gradient "
    "of the cost function until the cost stops decreasing"
)


@pytest.fixture
def mixed_pdf(tmp_path):
    """Slide 1 has plenty of text; slides 2 and 3 are title-only (image-heavy)."""
    doc = pymupdf.open()
    for title, body in [
        ("Gradient Descent", TEXT_SLIDE),
        ("Pipeline Diagram", ""),
        ("Thank You", ""),
    ]:
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=20)
        if body:
            page.insert_textbox(pymupdf.Rect(72, 100, 520, 300), body, fontsize=12)
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    return path


@pytest.fixture
def fake_vision(monkeypatch):
    requests = []
    replies = {"Pipeline Diagram": "Boxes: Data -> Model -> Prediction.", "Thank You": "NO_CONTENT"}

    def fake_chat(messages, model=None, **kwargs):
        text_part, image_part = messages[0]["content"]
        requests.append({"model": model, "text": text_part["text"], "image": image_part})
        title = next(t for t in replies if t in text_part["text"])
        return replies[title]

    monkeypatch.setattr(vision.llm, "chat", fake_chat)
    return requests


def test_only_image_heavy_slides_are_sent_and_described(mixed_pdf, fake_vision, monkeypatch):
    monkeypatch.setattr(config, "VISION_MODEL", "vision-model")
    deck = extract_deck(mixed_pdf)

    described = vision.describe_image_slides(deck, mixed_pdf)

    assert described == 1  # the diagram; "Thank You" answered NO_CONTENT
    assert len(fake_vision) == 1  # skip the text-heavy slide and the closing slide
    assert all(r["model"] == "vision-model" for r in fake_vision)
    assert fake_vision[0]["image"]["image_url"]["url"].startswith("data:image/png;base64,")
    assert deck.slides[1].text == "[From the slide image] Boxes: Data -> Model -> Prediction."
    assert deck.slides[2].text == ""  # NO_CONTENT adds nothing
    assert deck.slides[0].text.startswith("Gradient descent")  # untouched


def test_one_failed_slide_does_not_stop_the_rest(mixed_pdf, monkeypatch):
    def flaky(messages, model=None, **kwargs):
        if "Pipeline Diagram" in messages[0]["content"][0]["text"]:
            raise llm.LLMError("model choked on this image")
        return "A slide with a chart of accuracy per epoch."

    monkeypatch.setattr(vision.llm, "chat", flaky)
    deck = extract_deck(mixed_pdf)
    # The only teaching image fails; the administrative closing slide isn't sent.
    assert vision.describe_image_slides(deck, mixed_pdf) == 0
    assert deck.vision_failures == [2]


def test_vision_calls_are_capped(mixed_pdf, fake_vision, monkeypatch):
    monkeypatch.setattr(config, "MAX_VISION_SLIDES", 1)
    vision.describe_image_slides(extract_deck(mixed_pdf), mixed_pdf)
    assert len(fake_vision) == 1


def test_pipeline_skips_vision_when_disabled(mixed_pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "VISION_ENABLED", False)
    monkeypatch.setattr(summarize, "describe_image_slides", lambda *a: calls.append(a))
    monkeypatch.setattr(summarize, "summarize_deck", lambda deck, mode, progress=None: "summary")

    assert summarize.summarize_file(mixed_pdf) == "summary"
    assert calls == []
