import pymupdf

from backend.pdf_builder import build_pdf, parse_summary

SAMPLE = """# Concept Learning and Find-S

## Overview
This lecture covers concept learning.
It introduces Find-S.

## Key Points
### Hypothesis Space
- Concept space size is 2^(2^n) for n binary features.
- A task is given by <P,T,E>.
### Find-S
- Starts from the **most specific** hypothesis.

## Important Terms
- **Version Space**: hypotheses consistent with the data.
- Feature Space (χ): all attribute combinations.
"""


def test_parse_sections():
    s = parse_summary(SAMPLE)
    assert s.title == "Concept Learning and Find-S"
    assert s.overview == "This lecture covers concept learning. It introduces Find-S."
    assert [t.heading for t in s.topics] == ["Hypothesis Space", "Find-S"]
    assert len(s.topics[0].points) == 2


def test_parse_terms_with_and_without_bold():
    # The model is inconsistent about bold markers, so accept both forms.
    terms = parse_summary(SAMPLE).terms
    assert terms == [
        ("Version Space", "hypotheses consistent with the data."),
        ("Feature Space (χ)", "all attribute combinations."),
    ]


def test_bullets_before_any_topic_heading_are_kept():
    s = parse_summary("# T\n## Key Points\n- orphan point\n")
    assert s.topics[0].points == ["orphan point"]


def test_pdf_contains_text_including_special_characters():
    pdf = build_pdf(parse_summary(SAMPLE), source_name="2.pdf")
    assert pdf.startswith(b"%PDF")

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
    assert "Concept Learning and Find-S" in text
    assert "<P,T,E>" in text  # escaped, not swallowed as an HTML tag
    assert "χ" in text  # needs a Unicode font, not built-in Helvetica
    assert "most specific" in text
    assert "From 2.pdf" in text
