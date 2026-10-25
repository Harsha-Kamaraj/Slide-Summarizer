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
- **Concept:** what belongs to a category.
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
        ("Concept", "what belongs to a category."),
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


def test_short_leading_labels_are_bolded():
    from backend.pdf_builder import _bold_label

    assert (
        _bold_label("Feature Space: all combinations") == "<b>Feature Space:</b> all combinations"
    )
    # A long sentence that happens to contain a colon is left alone.
    long = "The algorithm in this lecture works as follows: start specific"
    assert _bold_label(long) == long


def test_topic_explanation_and_numbered_steps():
    s = parse_summary(
        "# T\n## Key Points\n### Find-S\nFind-S finds the most specific hypothesis.\n"
        "It ignores negatives.\n1. Initialise h\n2. Generalise on positives\n- Fast\n"
    )
    topic = s.topics[0]
    assert topic.explanation == "Find-S finds the most specific hypothesis. It ignores negatives."
    assert topic.points == ["1. Initialise h", "2. Generalise on positives", "Fast"]

    pdf = build_pdf(s)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "It ignores negatives." in text
    assert "1." in text and "Initialise h" in text


def test_formula_lines_are_parsed_and_boxed():
    s = parse_summary(
        "# T\n## Key Points\n### Concept Space\n- The number of concepts grows fast.\n"
        "Formula: N = 2^(2^d)\n- `Formula: J(w) = sum(x_i)`\n"
    )
    assert s.topics[0].points == [
        "The number of concepts grows fast.",
        "Formula: N = 2^(2^d)",
        "Formula: J(w) = sum(x_i)",
    ]
    pdf = build_pdf(s)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        text = page.get_text()
        has_box = any(d.get("fill") for d in page.get_drawings())
    assert "N = 2" in text and "sum(x" in text
    assert has_box  # the shaded background was drawn


def test_formula_markup_superscripts_and_subscripts():
    from backend.pdf_builder import _formula_markup

    assert _formula_markup("2^(2^d)") == "2<super>2<super>d</super></super>"
    assert _formula_markup("h(x_i) - y_i") == "h(x<sub>i</sub>) - y<sub>i</sub>"
    assert _formula_markup("a < b") == "a &lt; b"
    assert _formula_markup("2 ^ (3 x 2)") == "2<super>3 x 2</super>"
